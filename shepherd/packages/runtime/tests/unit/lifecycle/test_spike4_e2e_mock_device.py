"""Spike 4: End-to-End with Mock Device.

Validates that all layers compose correctly for programmatic device execution:
- Spec construction (build TaskSpec from a task class)
- Mock device execution (device receives spec, reconstructs, executes, returns result)
- Output deserialization via Pydantic wrapper (metadata.task_outputs -> pydantic.create_model -> model_validate)
- Fallback guard checks (hasattr, _task_source check, isolation_level check)
- Phase management (_mark_device_phases_completed)

Reference: design/SPIKES-programmatic-device-execution.md (Spike 4)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pydantic
import pytest
from shepherd_core.foundation.protocols.device import (
    ContextStateBase,
    DeviceCapabilities,
    EffectBundle,
    ExecutionResult,
    ExecutionSpec,
    SandboxConfig,
    SandboxExecutionError,
)
from shepherd_core.types import ProviderCapabilities
from shepherd_runtime._lifecycle_impl import ExecutionLifecycle
from shepherd_runtime.task._mixin import _async_mode
from shepherd_runtime.task.authoring import Input, Output, task
from shepherd_runtime.task.reconstruction import reconstruct_task_class
from shepherd_runtime.task.source_analysis import extract_task_source

if TYPE_CHECKING:
    from shepherd_runtime.task.metadata import TaskMetadata

# ===========================================================================
# TaskSpec: proposed dataclass from the design (not yet in codebase)
# ===========================================================================


@dataclass(frozen=True)
class TaskSpec:
    """Proposed TaskSpec for programmatic device execution.

    This mirrors what the parent design proposes to add to ExecutionSpec.
    We define it here to validate the composition pattern.
    """

    task_source: str
    task_imports: list[str]
    task_inputs: dict[str, Any]
    output_fields: list[str]
    context_fields: dict[str, str]  # field_name -> binding_name


# ===========================================================================
# Mock Context State (simulates a serializable context)
# ===========================================================================


@dataclass(frozen=True)
class MockContextState(ContextStateBase):
    """A mock context state for testing."""

    label: str = ""
    value: int = 0

    @property
    def context_type(self) -> str:
        return "mock_context"

    def to_dict(self) -> dict[str, Any]:
        return {"context_type": self.context_type, "label": self.label, "value": self.value}

    def rebind(self, env: dict[str, str]) -> MockContextState:
        return self


class MockContext:
    """A mock context object that supports to_state() and from_state()."""

    def __init__(self, label: str = "", value: int = 0):
        self.label = label
        self.value = value

    def to_state(self) -> MockContextState:
        return MockContextState(label=self.label, value=self.value)

    @classmethod
    def from_state(cls, state: MockContextState) -> MockContext:
        return cls(label=state.label, value=state.value)


# ===========================================================================
# Mock SandboxHandle
# ===========================================================================


class MockSandboxHandle:
    """Minimal SandboxHandle implementation."""

    def __init__(self, sandbox_id: str = "sb-mock-1"):
        self._sandbox_id = sandbox_id
        self._device_name = "mock-device"

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id

    @property
    def device_name(self) -> str:
        return self._device_name


# ===========================================================================
# Mock Device implementing DeviceProtocol
# ===========================================================================


class MockDevice:
    """Mock device that simulates ContainerDevice + task_runner for programmatic tasks.

    The execute() method reconstructs the task from the spec, instantiates it,
    calls execute(), and returns serialized outputs in metadata.task_outputs.
    """

    def __init__(self) -> None:
        self.cleanup_called = False
        self.execute_called = False
        self.last_spec: ExecutionSpec | None = None
        self.last_task_spec: TaskSpec | None = None
        self._should_fail = False
        self._isolation_level = "container"

    @property
    def name(self) -> str:
        return "mock-device"

    @property
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            isolation_level=self._isolation_level,
            effect_capture="overlay",
        )

    async def create_sandbox(
        self,
        scope: Any,
        config: SandboxConfig,
    ) -> MockSandboxHandle:
        return MockSandboxHandle()

    async def execute(
        self,
        sandbox: MockSandboxHandle,
        spec: ExecutionSpec,
        task_spec: TaskSpec | None = None,
    ) -> ExecutionResult:
        """Simulate ContainerDevice + task_runner.

        Reconstructs the task from source, instantiates with inputs,
        calls execute(), and returns serialized outputs.
        """
        self.execute_called = True
        self.last_spec = spec

        if self._should_fail:
            raise SandboxExecutionError(
                "Container execution failed",
                phase="task_execution",
                exit_code=1,
            )

        if task_spec is None:
            # No task_spec => this is an LLM-style call, return empty
            return ExecutionResult(success=True, output_text="", metadata={})

        self.last_task_spec = task_spec

        # --- Simulate task_runner logic (Spike 3 validated this) ---

        # 1. Reconstruct task class from source
        cls = reconstruct_task_class(
            task_spec.task_source,
            imports=task_spec.task_imports,
            validate=False,
        )

        # 2. Instantiate with suppressed auto-execution
        token = _async_mode.set(True)
        try:
            instance = cls.model_validate(task_spec.task_inputs)
        finally:
            _async_mode.reset(token)

        # 3. Attach mock contexts from context_fields
        # In real device, this would use deserialize_context + from_state
        # Here we just attach a MockContext from the sandbox config
        for field_name in task_spec.context_fields:
            mock_ctx = MockContext(label="from-device", value=42)
            setattr(instance, field_name, mock_ctx)

        # 4. Execute
        instance.execute()

        # 5. Serialize outputs
        meta: TaskMetadata = cls._task_meta
        task_outputs: dict[str, Any] = {}
        for field_name in task_spec.output_fields:
            if field_name in meta.outputs:
                val = getattr(instance, field_name)
                task_outputs[field_name] = val

        # 6. Return in the expected format (task_outputs nested in metadata)
        return ExecutionResult(
            success=True,
            output_text="",
            metadata={"task_outputs": task_outputs},
        )

    async def extract_effects(
        self,
        sandbox: MockSandboxHandle,
        execution_result: ExecutionResult,
    ) -> EffectBundle:
        return EffectBundle(context_effects={}, lifecycle_effects=[])

    async def cleanup(self, sandbox: MockSandboxHandle, **kwargs: Any) -> None:
        self.cleanup_called = True


# ===========================================================================
# Test Task Definitions
# ===========================================================================

SIMPLE_TASK_SOURCE = '''\
@task
class SimpleTask(BaseModel):
    """A simple programmatic task."""
    name: Input(str)
    greeting: Output(str) = ""

    def execute(self):
        self.greeting = f"Hello, {self.name}!"
'''

CONTEXT_TASK_SOURCE = '''\
@task
class ContextTask(BaseModel):
    """A task that uses a context."""
    name: Input(str)
    info: Output(str) = ""

    def execute(self):
        ctx = self.my_ctx
        self.info = f"name={self.name}, label={ctx.label}, value={ctx.value}"
'''


# ===========================================================================
# Helpers
# ===========================================================================


def _make_scope(bindings: list[MagicMock] | None = None) -> MagicMock:
    """Create a mock scope with effect tracking."""
    scope = MagicMock()
    scope.id = "mock-scope-id"
    scope._parent_proxy = None
    scope.emit = MagicMock()
    scope.update_context = MagicMock()
    scope.mark_binding_lifecycle = MagicMock()
    scope.all_bindings = MagicMock(return_value=bindings or [])
    scope.current_device = None
    scope.effects = MagicMock()
    scope._get_cache_store = MagicMock(return_value=None)
    scope._get_cache_config = MagicMock(return_value=None)
    return scope


def _make_provider(provider_id: str = "test-provider") -> MagicMock:
    """Create a mock provider."""
    provider = MagicMock()
    provider.provider_id = provider_id
    provider.capabilities = ProviderCapabilities(provider_type="test")
    provider.validate_binding = MagicMock()
    provider.formatter = MagicMock()
    provider.execute_sdk = AsyncMock(return_value=MagicMock(output_text="LLM output"))
    return provider


def _build_task_spec(
    task_source: str,
    task_imports: list[str],
    task_inputs: dict[str, Any],
    output_fields: list[str],
    context_fields: dict[str, str] | None = None,
) -> TaskSpec:
    """Build a TaskSpec from components (simulates _build_programmatic_spec)."""
    return TaskSpec(
        task_source=task_source,
        task_imports=task_imports,
        task_inputs=task_inputs,
        output_fields=output_fields,
        context_fields=context_fields or {},
    )


def _deserialize_outputs_pydantic(
    task_outputs: dict[str, Any],
    output_field_types: dict[str, type],
) -> dict[str, Any]:
    """Deserialize outputs using the Pydantic wrapper model approach (Spike 2 validated).

    Creates a dynamic Pydantic model from the output field types,
    validates the serialized data through it, and returns the deserialized values.
    """
    if not task_outputs:
        return {}

    fields: dict[str, Any] = {}
    for name, typ in output_field_types.items():
        fields[name] = (typ, ...)

    WrapperModel = pydantic.create_model("OutputWrapper", **fields)
    validated = WrapperModel.model_validate(task_outputs)
    return {name: getattr(validated, name) for name in output_field_types}


# ===========================================================================
# Test Class 1: Spec Construction
# ===========================================================================


@pytest.mark.spike
class TestSpecConstruction:
    """Verify TaskSpec can be built from a task class's metadata."""

    def test_build_spec_from_task_source(self) -> None:
        """TaskSpec captures source, imports, inputs, and output fields."""
        spec = _build_task_spec(
            task_source=SIMPLE_TASK_SOURCE,
            task_imports=[],
            task_inputs={"name": "World"},
            output_fields=["greeting"],
        )

        assert spec.task_source == SIMPLE_TASK_SOURCE
        assert spec.task_inputs == {"name": "World"}
        assert spec.output_fields == ["greeting"]
        assert spec.context_fields == {}

    def test_build_spec_with_context_fields(self) -> None:
        """TaskSpec captures context field -> binding name mapping."""
        spec = _build_task_spec(
            task_source=CONTEXT_TASK_SOURCE,
            task_imports=[],
            task_inputs={"name": "test"},
            output_fields=["info"],
            context_fields={"my_ctx": "workspace"},
        )

        assert spec.context_fields == {"my_ctx": "workspace"}

    def test_spec_from_live_task_class(self) -> None:
        """Build a TaskSpec from a live @task class using extract_task_source."""

        @task
        class LiveTask:
            """A live task for spec extraction."""

            query: Input(str)
            answer: Output(str) = ""

            def execute(self):
                self.answer = f"Answer: {self.query}"

        source = extract_task_source(LiveTask)
        meta: TaskMetadata = LiveTask._task_meta

        spec = _build_task_spec(
            task_source=source,
            task_imports=[],
            task_inputs={"query": "hello"},
            output_fields=list(meta.outputs.keys()),
        )

        assert "LiveTask" in spec.task_source
        assert spec.task_inputs == {"query": "hello"}
        assert spec.output_fields == ["answer"]

    def test_spec_inputs_json_serializable(self) -> None:
        """Task inputs must survive JSON round-trip (mode='json' from Spike 2)."""
        inputs = {"name": "World", "count": 42, "tags": ["a", "b"]}
        roundtripped = json.loads(json.dumps(inputs))
        assert roundtripped == inputs


# ===========================================================================
# Test Class 2: Mock Device Execution (Full Round-Trip)
# ===========================================================================


@pytest.mark.spike
class TestMockDeviceExecution:
    """Verify mock device simulates the full task_runner round-trip."""

    async def test_simple_task_round_trip(self) -> None:
        """Mock device reconstructs, executes, and returns correct outputs."""
        device = MockDevice()
        sandbox = await device.create_sandbox(MagicMock(), SandboxConfig(context_states={}))

        spec = ExecutionSpec(prompt="", provider_config={})
        task_spec = _build_task_spec(
            task_source=SIMPLE_TASK_SOURCE,
            task_imports=[],
            task_inputs={"name": "World"},
            output_fields=["greeting"],
        )

        result = await device.execute(sandbox, spec, task_spec=task_spec)

        assert result.success is True
        assert result.metadata is not None
        assert "task_outputs" in result.metadata
        assert result.metadata["task_outputs"]["greeting"] == "Hello, World!"

    async def test_outputs_survive_json_roundtrip(self) -> None:
        """Outputs nested in metadata.task_outputs survive JSON serialization."""
        device = MockDevice()
        sandbox = await device.create_sandbox(MagicMock(), SandboxConfig(context_states={}))

        spec = ExecutionSpec(prompt="", provider_config={})
        task_spec = _build_task_spec(
            task_source=SIMPLE_TASK_SOURCE,
            task_imports=[],
            task_inputs={"name": "JSON"},
            output_fields=["greeting"],
        )

        result = await device.execute(sandbox, spec, task_spec=task_spec)

        # Simulate container boundary: serialize and deserialize
        metadata_json = json.dumps(dict(result.metadata))
        deserialized = json.loads(metadata_json)

        assert deserialized["task_outputs"]["greeting"] == "Hello, JSON!"

    async def test_device_tracks_execution(self) -> None:
        """Mock device tracks that execute was called."""
        device = MockDevice()
        sandbox = await device.create_sandbox(MagicMock(), SandboxConfig(context_states={}))

        spec = ExecutionSpec(prompt="", provider_config={})
        task_spec = _build_task_spec(
            task_source=SIMPLE_TASK_SOURCE,
            task_imports=[],
            task_inputs={"name": "Track"},
            output_fields=["greeting"],
        )

        await device.execute(sandbox, spec, task_spec=task_spec)

        assert device.execute_called
        assert device.last_task_spec is task_spec

    async def test_cleanup_tracked(self) -> None:
        """Mock device tracks cleanup calls."""
        device = MockDevice()
        sandbox = await device.create_sandbox(MagicMock(), SandboxConfig(context_states={}))

        await device.cleanup(sandbox)
        assert device.cleanup_called

    async def test_extract_effects_returns_empty_bundle(self) -> None:
        """Mock device returns empty EffectBundle."""
        device = MockDevice()
        sandbox = await device.create_sandbox(MagicMock(), SandboxConfig(context_states={}))

        result = ExecutionResult(success=True, output_text="")
        bundle = await device.extract_effects(sandbox, result)

        assert bundle.context_effects == {}
        assert list(bundle.lifecycle_effects) == []


# ===========================================================================
# Test Class 3: Output Deserialization via Pydantic Wrapper
# ===========================================================================


@pytest.mark.spike
class TestOutputDeserialization:
    """Verify Pydantic wrapper model deserializes outputs at the lifecycle boundary."""

    def test_str_output(self) -> None:
        """String output round-trips through Pydantic wrapper."""
        outputs = _deserialize_outputs_pydantic(
            {"greeting": "Hello!"},
            {"greeting": str},
        )
        assert outputs["greeting"] == "Hello!"

    def test_int_output(self) -> None:
        """Integer output round-trips through Pydantic wrapper."""
        outputs = _deserialize_outputs_pydantic(
            {"count": 42},
            {"count": int},
        )
        assert outputs["count"] == 42
        assert isinstance(outputs["count"], int)

    def test_list_output(self) -> None:
        """List[str] output round-trips."""
        outputs = _deserialize_outputs_pydantic(
            {"items": ["a", "b", "c"]},
            {"items": list[str]},
        )
        assert outputs["items"] == ["a", "b", "c"]

    def test_dict_output(self) -> None:
        """dict[str, int] output round-trips (important for programmatic tasks)."""
        outputs = _deserialize_outputs_pydantic(
            {"counts": {"a": 1, "b": 2}},
            {"counts": dict[str, int]},
        )
        assert outputs["counts"] == {"a": 1, "b": 2}

    def test_set_output_from_list(self) -> None:
        """set[str] serialized as list round-trips via Pydantic wrapper."""
        # Sets serialize to lists in JSON; Pydantic coerces list -> set
        outputs = _deserialize_outputs_pydantic(
            {"tags": ["x", "y", "z"]},
            {"tags": set[str]},
        )
        assert outputs["tags"] == {"x", "y", "z"}
        assert isinstance(outputs["tags"], set)

    def test_datetime_output(self) -> None:
        """datetime serialized as ISO string round-trips."""
        now = datetime(2025, 6, 15, 12, 30, 0)
        outputs = _deserialize_outputs_pydantic(
            {"timestamp": now.isoformat()},
            {"timestamp": datetime},
        )
        assert outputs["timestamp"] == now

    def test_basemodel_output(self) -> None:
        """Pydantic BaseModel output round-trips."""

        class ResultInfo(pydantic.BaseModel):
            score: float
            label: str

        serialized = {"info": {"score": 0.95, "label": "good"}}
        outputs = _deserialize_outputs_pydantic(
            serialized,
            {"info": ResultInfo},
        )
        assert isinstance(outputs["info"], ResultInfo)
        assert outputs["info"].score == 0.95
        assert outputs["info"].label == "good"

    def test_enum_output(self) -> None:
        """Enum output round-trips (serialized as value)."""

        class Status(Enum):
            PASS = "pass"
            FAIL = "fail"

        outputs = _deserialize_outputs_pydantic(
            {"status": "pass"},
            {"status": Status},
        )
        assert outputs["status"] == Status.PASS

    def test_empty_outputs(self) -> None:
        """Empty outputs dict handled correctly."""
        outputs = _deserialize_outputs_pydantic({}, {})
        assert outputs == {}

    def test_multiple_outputs(self) -> None:
        """Multiple output fields deserialized together."""
        outputs = _deserialize_outputs_pydantic(
            {"name": "test", "count": 5, "tags": ["a"]},
            {"name": str, "count": int, "tags": list[str]},
        )
        assert outputs == {"name": "test", "count": 5, "tags": ["a"]}


# ===========================================================================
# Test Class 4: Fallback Guard Checks
# ===========================================================================


@pytest.mark.spike
class TestFallbackGuardChecks:
    """Verify fallback conditions that would cause in-process execution."""

    def test_source_unavailable_triggers_fallback(self) -> None:
        """When _task_source is None, the device path should not be taken."""

        @task
        class SourcelessTask:
            """A task where we remove _task_source."""

            name: Input(str)
            result: Output(str) = ""

            def execute(self):
                self.result = f"Result: {self.name}"

        # Simulate source unavailable
        original_source = SourcelessTask._task_source
        try:
            SourcelessTask._task_source = None

            # The guard check: if _task_source is None, fall back
            task_source = getattr(SourcelessTask, "_task_source", None)
            should_use_device = task_source is not None

            assert not should_use_device, "_task_source=None should trigger fallback"
        finally:
            SourcelessTask._task_source = original_source

    def test_non_bound_executor_triggers_fallback(self) -> None:
        """A lambda executor without __self__ should trigger fallback."""
        executor = lambda: None  # noqa: E731

        # Guard check: executor must be a bound method with __self__
        has_self = hasattr(executor, "__self__")
        assert not has_self, "Lambda should not have __self__"

        # Further check: must have _task_source on the class
        if has_self:
            task_cls = type(executor.__self__)
            has_source = hasattr(task_cls, "_task_source")
        else:
            has_source = False

        assert not has_source, "Lambda fallback should skip device path"

    def test_bound_method_passes_guard(self) -> None:
        """A bound method on a task instance passes the guard check."""

        @task
        class GuardTask:
            """Task for guard check."""

            name: Input(str)
            result: Output(str) = ""

            def execute(self):
                self.result = self.name

        token = _async_mode.set(True)
        try:
            instance = GuardTask.model_validate({"name": "test"})
        finally:
            _async_mode.reset(token)

        executor = instance.execute

        has_self = hasattr(executor, "__self__")
        assert has_self, "Bound method should have __self__"

        task_cls = type(executor.__self__)
        has_source = hasattr(task_cls, "_task_source") and task_cls._task_source is not None
        assert has_source, "Task class should have _task_source"

    def test_isolation_level_none_skips_device(self) -> None:
        """Device with isolation_level='none' should not trigger device dispatch."""
        device = MockDevice()
        device._isolation_level = "none"

        caps = device.capabilities
        should_use_device = caps.isolation_level != "none"

        assert not should_use_device, "isolation_level='none' should skip device path"

    def test_isolation_level_container_triggers_device(self) -> None:
        """Device with isolation_level='container' should trigger device dispatch."""
        device = MockDevice()
        device._isolation_level = "container"

        caps = device.capabilities
        should_use_device = caps.isolation_level != "none"

        assert should_use_device, "isolation_level='container' should use device path"


# ===========================================================================
# Test Class 5: Device Error Handling
# ===========================================================================


@pytest.mark.spike
class TestDeviceErrorHandling:
    """Verify cleanup is called on device errors and errors propagate correctly."""

    async def test_device_error_cleanup_still_called(self) -> None:
        """When device.execute() raises, cleanup() must still be called."""
        device = MockDevice()
        device._should_fail = True
        sandbox = await device.create_sandbox(MagicMock(), SandboxConfig(context_states={}))

        try:
            spec = ExecutionSpec(prompt="", provider_config={})
            task_spec = _build_task_spec(
                task_source=SIMPLE_TASK_SOURCE,
                task_imports=[],
                task_inputs={"name": "test"},
                output_fields=["greeting"],
            )
            try:
                await device.execute(sandbox, spec, task_spec=task_spec)
            except SandboxExecutionError:
                pass
            else:
                pytest.fail("Expected SandboxExecutionError")
        finally:
            await device.cleanup(sandbox)

        assert device.cleanup_called

    async def test_scaffold_pattern_ensures_cleanup(self) -> None:
        """The scaffold pattern (try/finally) guarantees cleanup on failure."""
        device = MockDevice()
        device._should_fail = True
        scope = _make_scope()

        sandbox = await device.create_sandbox(scope, SandboxConfig(context_states={}))

        caught_error = False
        try:
            spec = ExecutionSpec(prompt="", provider_config={})
            task_spec = _build_task_spec(
                task_source=SIMPLE_TASK_SOURCE,
                task_imports=[],
                task_inputs={"name": "fail"},
                output_fields=["greeting"],
            )
            await device.execute(sandbox, spec, task_spec=task_spec)
        except SandboxExecutionError:
            caught_error = True
        finally:
            await device.cleanup(sandbox)

        assert caught_error, "SandboxExecutionError should have been raised"
        assert device.cleanup_called, "cleanup must be called even on error"

    async def test_sandbox_execution_error_attributes(self) -> None:
        """SandboxExecutionError carries diagnostic context."""
        device = MockDevice()
        device._should_fail = True
        sandbox = await device.create_sandbox(MagicMock(), SandboxConfig(context_states={}))

        with pytest.raises(SandboxExecutionError) as exc_info:
            await device.execute(
                sandbox,
                ExecutionSpec(prompt="", provider_config={}),
                task_spec=_build_task_spec(
                    task_source=SIMPLE_TASK_SOURCE,
                    task_imports=[],
                    task_inputs={"name": "x"},
                    output_fields=["greeting"],
                ),
            )

        err = exc_info.value
        assert err.phase == "task_execution"
        assert err.exit_code == 1


# ===========================================================================
# Test Class 6: Pipeline Phase Management
# ===========================================================================


@pytest.mark.spike
class TestPipelinePhaseManagement:
    """Verify _mark_device_phases_completed() works for programmatic path."""

    async def test_mark_device_phases_completed_advances_past_apply(self) -> None:
        """After _mark_device_phases_completed(), pipeline is at cleanup phase."""
        scope = _make_scope()
        provider = _make_provider()

        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)

        async with lifecycle:
            pipeline = lifecycle._pipeline
            assert pipeline is not None

            phase_names = [p.name for p in pipeline.phases]

            # Call _mark_device_phases_completed (simulating what scaffold does)
            lifecycle._mark_device_phases_completed()

            # Phase index should be at cleanup
            cleanup_index = phase_names.index("cleanup")
            assert pipeline._phase_index == cleanup_index, (
                f"Expected phase_index={cleanup_index} (cleanup), got {pipeline._phase_index}"
            )

    async def test_device_phases_in_completed_list(self) -> None:
        """Device phases (execute, artifact, extract, apply) are marked completed."""
        scope = _make_scope()
        provider = _make_provider()

        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)

        async with lifecycle:
            lifecycle._mark_device_phases_completed()

            completed_names = [p.name for p in lifecycle._pipeline._completed_phases]
            for phase in ["execute", "artifact", "extract", "apply"]:
                assert phase in completed_names, (
                    f"'{phase}' should be in completed phases after _mark_device_phases_completed()"
                )

    async def test_aexit_runs_only_cleanup_after_device_phases_marked(self) -> None:
        """After _mark_device_phases_completed, __aexit__ should only run cleanup."""
        scope = _make_scope()
        provider = _make_provider()

        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)

        async with lifecycle:
            pipeline = lifecycle._pipeline
            assert pipeline is not None

            lifecycle._mark_device_phases_completed()

            # Verify we're at cleanup
            phase_names = [p.name for p in pipeline.phases]
            assert phase_names[pipeline._phase_index] == "cleanup"

        # If __aexit__ completed without error, cleanup ran without
        # trying to re-run execute/artifact/extract/apply

    async def test_run_executor_phase_interaction(self) -> None:
        """run_executor() works through pipeline phases correctly.

        Uses provider=None because run_executor() is the programmatic path —
        ExecutePhase checks ctx.executor first and uses it directly, never
        reaching the provider. Passing a mock provider would cause issues if
        the executor somehow didn't propagate.
        """
        scope = _make_scope()

        # Set up a simple executor
        executed = False

        async def simple_executor() -> None:
            nonlocal executed
            executed = True

        # executor must be set BEFORE __aenter__ so it propagates to PhaseContext
        lifecycle = ExecutionLifecycle(scope=scope, provider=None, executor=simple_executor)

        async with lifecycle:
            # run_executor runs phases up through apply
            await lifecycle.run_executor()

        assert executed, "Executor should have been called by ExecutePhase"
        assert executed, "Executor should have been called by the execute phase"


# ===========================================================================
# Test Class 7: End-to-End Composition (Component Integration)
# ===========================================================================


@pytest.mark.spike
class TestEndToEndComposition:
    """Test that all components compose correctly end-to-end.

    Since run_executor() doesn't have the device check yet, we test
    the components that would compose in sequence.
    """

    async def test_full_round_trip_simple_task(self) -> None:
        """Complete round-trip: spec build -> device execute -> output deserialize."""
        # Step 1: Build spec
        spec = _build_task_spec(
            task_source=SIMPLE_TASK_SOURCE,
            task_imports=[],
            task_inputs={"name": "E2E"},
            output_fields=["greeting"],
        )

        # Step 2: Execute via mock device
        device = MockDevice()
        sandbox = await device.create_sandbox(MagicMock(), SandboxConfig(context_states={}))
        result = await device.execute(
            sandbox,
            ExecutionSpec(prompt="", provider_config={}),
            task_spec=spec,
        )

        # Step 3: Extract effects
        bundle = await device.extract_effects(sandbox, result)

        # Step 4: Deserialize outputs via Pydantic wrapper
        task_outputs = result.metadata["task_outputs"]
        deserialized = _deserialize_outputs_pydantic(
            task_outputs,
            {"greeting": str},
        )

        # Step 5: Cleanup
        await device.cleanup(sandbox)

        # Verify full chain
        assert deserialized["greeting"] == "Hello, E2E!"
        assert bundle.context_effects == {}
        assert device.cleanup_called

    async def test_full_round_trip_with_json_boundary(self) -> None:
        """Full round-trip including JSON serialization at the container boundary."""
        spec = _build_task_spec(
            task_source=SIMPLE_TASK_SOURCE,
            task_imports=[],
            task_inputs={"name": "Boundary"},
            output_fields=["greeting"],
        )

        device = MockDevice()
        sandbox = await device.create_sandbox(MagicMock(), SandboxConfig(context_states={}))
        result = await device.execute(
            sandbox,
            ExecutionSpec(prompt="", provider_config={}),
            task_spec=spec,
        )

        # Simulate container boundary: JSON serialize/deserialize
        metadata_json = json.dumps(dict(result.metadata))
        deserialized_metadata = json.loads(metadata_json)

        # Deserialize through Pydantic wrapper
        outputs = _deserialize_outputs_pydantic(
            deserialized_metadata["task_outputs"],
            {"greeting": str},
        )

        assert outputs["greeting"] == "Hello, Boundary!"
        await device.cleanup(sandbox)

    async def test_lifecycle_with_mark_device_phases_then_cleanup(self) -> None:
        """Lifecycle + device execution + phase management composes correctly."""
        scope = _make_scope()
        provider = _make_provider()
        device = MockDevice()

        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)

        async with lifecycle:
            # Simulate device execution + phase bypass
            sandbox = await device.create_sandbox(scope, SandboxConfig(context_states={}))

            spec = _build_task_spec(
                task_source=SIMPLE_TASK_SOURCE,
                task_imports=[],
                task_inputs={"name": "Lifecycle"},
                output_fields=["greeting"],
            )

            try:
                result = await device.execute(
                    sandbox,
                    ExecutionSpec(prompt="", provider_config={}),
                    task_spec=spec,
                )
                bundle = await device.extract_effects(sandbox, result)
                lifecycle._apply_effect_bundle(bundle)

                # Mark device phases completed (pipeline bypass)
                lifecycle._mark_device_phases_completed()
            finally:
                await device.cleanup(sandbox)

        # Verify everything composed
        assert result.metadata["task_outputs"]["greeting"] == "Hello, Lifecycle!"
        assert device.cleanup_called

    def test_boundary_contract_task_outputs_in_metadata(self) -> None:
        """Verify the nesting contract: task_outputs MUST be in metadata, not structured_output."""
        # This is the key boundary contract from the design table
        result = ExecutionResult(
            success=True,
            output_text="",
            metadata={"task_outputs": {"greeting": "Hello!"}},
        )

        # Consumer side: lifecycle reads from metadata
        assert result.metadata is not None
        assert "task_outputs" in result.metadata
        assert result.metadata["task_outputs"]["greeting"] == "Hello!"

        # NOT in structured_output
        assert result.structured_output is None


# ===========================================================================
# Test Class 8: Multiple Output Types (End-to-End)
# ===========================================================================


@pytest.mark.spike
class TestMultipleOutputTypes:
    """Test that various output types survive the full serialization round-trip.

    This uses the Pydantic wrapper approach validated by Spike 2, but
    tests it in the context of the full device round-trip (JSON boundary).
    """

    @pytest.mark.parametrize(
        ("type_name", "raw_value", "expected_type", "output_type"),
        [
            ("str", "hello", str, str),
            ("int", 42, int, int),
            ("float", 3.14, float, float),
            ("bool", True, bool, bool),
            ("list_str", ["a", "b"], list, list[str]),
            ("dict_str_int", {"a": 1, "b": 2}, dict, dict[str, int]),
        ],
        ids=lambda x: x if isinstance(x, str) else "",
    )
    def test_basic_types_through_json_and_pydantic(
        self,
        type_name: str,
        raw_value: Any,
        expected_type: type,
        output_type: type,
    ) -> None:
        """Basic types survive JSON -> Pydantic wrapper round-trip."""
        # Simulate: output -> JSON serialize -> JSON deserialize -> Pydantic wrapper
        serialized = json.loads(json.dumps({"value": raw_value}))
        outputs = _deserialize_outputs_pydantic(
            serialized,
            {"value": output_type},
        )
        assert isinstance(outputs["value"], expected_type)
        assert outputs["value"] == raw_value

    def test_set_survives_json_roundtrip(self) -> None:
        """set[str] -> list (JSON) -> set (Pydantic) round-trip."""
        original = {"x", "y", "z"}
        serialized = json.loads(json.dumps({"value": list(original)}))
        outputs = _deserialize_outputs_pydantic(
            serialized,
            {"value": set[str]},
        )
        assert outputs["value"] == original
        assert isinstance(outputs["value"], set)

    def test_datetime_survives_json_roundtrip(self) -> None:
        """datetime -> ISO string (JSON) -> datetime (Pydantic) round-trip."""
        dt = datetime(2025, 6, 15, 10, 30, 0)
        serialized = json.loads(json.dumps({"value": dt.isoformat()}))
        outputs = _deserialize_outputs_pydantic(
            serialized,
            {"value": datetime},
        )
        assert outputs["value"] == dt

    def test_basemodel_survives_json_roundtrip(self) -> None:
        """Pydantic BaseModel -> dict (JSON) -> BaseModel (Pydantic) round-trip."""

        class Info(pydantic.BaseModel):
            score: float
            label: str

        original = Info(score=0.9, label="good")
        serialized = json.loads(json.dumps({"value": original.model_dump()}))
        outputs = _deserialize_outputs_pydantic(
            serialized,
            {"value": Info},
        )
        assert isinstance(outputs["value"], Info)
        assert outputs["value"].score == 0.9

    def test_enum_survives_json_roundtrip(self) -> None:
        """Enum -> value (JSON) -> Enum (Pydantic) round-trip."""

        class Priority(Enum):
            LOW = "low"
            HIGH = "high"

        serialized = json.loads(json.dumps({"value": "high"}))
        outputs = _deserialize_outputs_pydantic(
            serialized,
            {"value": Priority},
        )
        assert outputs["value"] == Priority.HIGH

    def test_nested_dict_with_list_values(self) -> None:
        """dict[str, list[int]] survives JSON round-trip."""
        original = {"a": [1, 2], "b": [3, 4]}
        serialized = json.loads(json.dumps({"value": original}))
        outputs = _deserialize_outputs_pydantic(
            serialized,
            {"value": dict[str, list[int]]},
        )
        assert outputs["value"] == original


# ===========================================================================
# Test Class 9: Context State Composition
# ===========================================================================


@pytest.mark.spike
class TestContextStateComposition:
    """Verify context state serialization and reconstruction patterns."""

    def test_mock_context_state_roundtrip(self) -> None:
        """MockContextState serializes to dict and back."""
        ctx = MockContext(label="test", value=99)
        state = ctx.to_state()

        assert isinstance(state, MockContextState)
        assert state.label == "test"
        assert state.value == 99

        # Reconstruct from state
        reconstructed = MockContext.from_state(state)
        assert reconstructed.label == "test"
        assert reconstructed.value == 99

    def test_context_state_json_roundtrip(self) -> None:
        """Context state survives JSON serialization (container boundary)."""
        ctx = MockContext(label="json-test", value=42)
        state = ctx.to_state()
        state_dict = state.to_dict()

        # JSON round-trip
        json_str = json.dumps(state_dict)
        deserialized = json.loads(json_str)

        # Reconstruct
        rehydrated_state = MockContextState(
            label=deserialized["label"],
            value=deserialized["value"],
        )
        reconstructed = MockContext.from_state(rehydrated_state)

        assert reconstructed.label == "json-test"
        assert reconstructed.value == 42

    def test_context_states_in_sandbox_config(self) -> None:
        """Context states can be passed to SandboxConfig."""
        ctx = MockContext(label="sandbox", value=1)
        state = ctx.to_state()

        config = SandboxConfig(context_states={"my_ctx": state})
        assert "my_ctx" in config.context_states
        assert config.context_states["my_ctx"].label == "sandbox"
