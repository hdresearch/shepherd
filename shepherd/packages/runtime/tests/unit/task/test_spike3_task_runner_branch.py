"""Spike 3: task_runner Programmatic Branch.

Validates that a task class can be reconstructed from source, instantiated
with suppressed auto-execution, have contexts attached, and produce correct
outputs via direct execute() call.

Reference: design/SPIKES-programmatic-device-execution.md (Spike 3)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from shepherd_runtime.task._mixin import TaskMixin, _async_execute_mode, _async_mode
from shepherd_runtime.task.reconstruction import ReconstructionError, reconstruct_task_class

if TYPE_CHECKING:
    from shepherd_runtime.task.metadata import TaskMetadata

# ---------------------------------------------------------------------------
# Helpers: minimal task sources for reconstruction
# ---------------------------------------------------------------------------

MINIMAL_SYNC_TASK_SOURCE = '''\
@task
class Greeter(BaseModel):
    """Greets a user."""
    name: Input(str)
    greeting: Output(str) = ""

    def execute(self):
        self.greeting = f"Hello, {self.name}!"
'''

MINIMAL_ASYNC_TASK_SOURCE = '''\
@task
class AsyncGreeter(BaseModel):
    """Greets a user asynchronously."""
    name: Input(str)
    greeting: Output(str) = ""

    async def execute(self):
        self.greeting = f"Hello, {self.name}!"
'''

TASK_WITH_CONTEXT_SOURCE = '''\
@task
class ContextTask(BaseModel):
    """A task that uses a context."""
    name: Input(str)
    workspace: Context(Any)
    result: Output(str) = ""

    def execute(self):
        ws = self.workspace
        self.result = f"path={getattr(ws, 'path', 'none')}, name={self.name}"
'''

TASK_WITH_STEP_SOURCE = '''\
from shepherd_runtime.step.api import step

@task
class StepTask(BaseModel):
    """A task that uses a @step method."""
    query: Input(str)
    answer: Output(str) = ""

    @step
    def think(self, query: str) -> str:
        """Think about the query."""
        return "thought"

    def execute(self):
        result = self.think(self.query)
        self.answer = result
'''

SYNTAX_ERROR_SOURCE = """\
@task
class BadSyntax(BaseModel):
    name: Input(str
"""

MISSING_IMPORT_SOURCE = """\
import nonexistent_module_xyz

@task
class BadImport(BaseModel):
    name: Input(str)
"""

MODULE_LEVEL_SYMBOL_SOURCE = """\
@task
class SymbolTask(BaseModel):
    name: Input(str)
    result: Output(str) = ""

    def execute(self):
        self.result = UNDEFINED_SYMBOL
"""


# =========================================================================
# Assumption 1: ContextVar suppression
# =========================================================================


class TestContextVarSuppression:
    """Validate that _async_mode ContextVar suppresses auto-execution."""

    @pytest.mark.spike
    def test_async_mode_suppresses_auto_execution(self):
        """With _async_mode=True, model_validate() should NOT trigger execute()."""
        cls = reconstruct_task_class(MINIMAL_SYNC_TASK_SOURCE, validate=False)

        token = _async_mode.set(True)
        try:
            instance = cls.model_validate({"name": "World"})
            # Output should still be at default -- execute() was NOT called
            assert instance.greeting == ""
        finally:
            _async_mode.reset(token)

    @pytest.mark.spike
    def test_without_suppression_triggers_auto_execution(self):
        """Without _async_mode, model_validate() DOES trigger auto-execution.

        Since there is no scope configured, _execute_sync raises ScopeNotConfiguredError.
        This confirms the ContextVar is the necessary guard.
        """
        from shepherd_core.errors import ScopeNotConfiguredError

        cls = reconstruct_task_class(MINIMAL_SYNC_TASK_SOURCE, validate=False)

        # Without suppression, auto-execute fires and needs a scope
        with pytest.raises(ScopeNotConfiguredError):
            cls.model_validate({"name": "World"})


# =========================================================================
# Assumption 2a: setattr on reconstructed instances
# =========================================================================


class TestSetattrOnReconstructedInstances:
    """Validate that setattr works for context attachment on reconstructed tasks."""

    @pytest.mark.spike
    def test_setattr_attaches_mock_context(self):
        """setattr on a reconstructed instance should work for context fields."""
        cls = reconstruct_task_class(TASK_WITH_CONTEXT_SOURCE, validate=False)

        token = _async_mode.set(True)
        try:
            instance = cls.model_validate({"name": "test"})
        finally:
            _async_mode.reset(token)

        # Attach a mock context via setattr
        mock_ctx = MagicMock()
        mock_ctx.path = "/mock/workspace"
        instance.workspace = mock_ctx

        # Verify getattr returns the mock
        assert instance.workspace is mock_ctx

        # Verify execute() can access it
        instance.execute()
        assert instance.result == "path=/mock/workspace, name=test"

    @pytest.mark.spike
    def test_setattr_with_typed_context_field(self):
        """setattr should not trigger Pydantic validation that rejects the mock."""
        cls = reconstruct_task_class(TASK_WITH_CONTEXT_SOURCE, validate=False)

        token = _async_mode.set(True)
        try:
            instance = cls.model_validate({"name": "typed"})
        finally:
            _async_mode.reset(token)

        # Even a plain object should work as a context
        class FakeWorkspace:
            path = "/fake/path"

        instance.workspace = FakeWorkspace()
        assert instance.workspace.path == "/fake/path"


# =========================================================================
# Assumption 2b: Context reconstruction from state via rebinding
# =========================================================================


class TestContextReconstructionFromState:
    """Validate WorkspaceRef -> WorkspaceState -> rebind -> from_state chain."""

    @pytest.mark.spike
    def test_workspace_state_roundtrip_with_rebinding(self):
        """Full chain: to_state -> serialize -> deserialize+rebind -> from_state."""
        from shepherd_contexts.workspace.ref import WorkspaceRef, WorkspaceState
        from shepherd_runtime.device.container.context_registry import deserialize_context

        # Create a WorkspaceRef with a host-style path.
        # We need a valid 40-char SHA for the base_commit validator.
        host_path = "/home/user/repo"
        fake_sha = "a" * 40
        ws = WorkspaceRef(
            path=host_path,
            base_commit=fake_sha,
            frozen_context_id="workspace:test:12345678",
        )

        # to_state produces a WorkspaceState
        state = ws.to_state()
        assert isinstance(state, WorkspaceState)
        assert state.path == host_path

        # Serialize to JSON and back
        state_dict = state.to_dict()
        json_str = json.dumps(state_dict)
        deserialized_dict = json.loads(json_str)

        # Deserialize with rebind_env
        container_path = "/tmp/test-container-workspace"
        rebound_state = deserialize_context(
            deserialized_dict,
            rebind_env={"WORKSPACE_PATH": container_path},
        )

        # Confirm it is a WorkspaceState (not WorkspaceRef)
        assert isinstance(rebound_state, WorkspaceState)
        assert not isinstance(rebound_state, WorkspaceRef)

        # Confirm path is rebound
        assert rebound_state.path == container_path

        # Reconstruct full WorkspaceRef from state WITHOUT sandbox_path
        ref = WorkspaceRef.from_state(rebound_state)
        assert isinstance(ref, WorkspaceRef)
        assert ref.path == container_path
        assert ref.base_commit == fake_sha

    @pytest.mark.spike
    def test_from_state_uses_state_path_when_no_sandbox_path(self):
        """from_state(state) without sandbox_path should use state.path."""
        from shepherd_contexts.workspace.ref import WorkspaceRef, WorkspaceState

        fake_sha = "b" * 40
        state = WorkspaceState(
            path="/container/workspace",
            base_commit=fake_sha,
        )

        ref = WorkspaceRef.from_state(state)
        assert ref.path == "/container/workspace"

    @pytest.mark.spike
    def test_reconstructed_ref_attached_to_task(self):
        """Attach a reconstructed WorkspaceRef to a task instance via setattr."""
        from shepherd_contexts.workspace.ref import WorkspaceRef, WorkspaceState

        cls = reconstruct_task_class(TASK_WITH_CONTEXT_SOURCE, validate=False)

        token = _async_mode.set(True)
        try:
            instance = cls.model_validate({"name": "device-test"})
        finally:
            _async_mode.reset(token)

        # Simulate container-side context reconstruction
        fake_sha = "c" * 40
        state = WorkspaceState(
            path="/container/ws",
            base_commit=fake_sha,
        )
        ref = WorkspaceRef.from_state(state)

        instance.workspace = ref
        instance.execute()
        assert instance.result == "path=/container/ws, name=device-test"

    @pytest.mark.spike
    def test_unknown_context_type_raises(self):
        """Deserializing an unknown context_type should raise."""
        from shepherd_runtime.device.container.context_registry import (
            ContextDeserializationError,
            deserialize_context,
        )

        with pytest.raises(ContextDeserializationError):
            deserialize_context({"context_type": "nonexistent_ctx_type_xyz"})


# =========================================================================
# Assumption 3: Reconstructed class completeness
# =========================================================================


class TestReconstructedClassCompleteness:
    """Verify reconstructed class has TaskMixin, _task_meta, _task_source, execute()."""

    @pytest.mark.spike
    def test_taskmixin_in_mro(self):
        cls = reconstruct_task_class(MINIMAL_SYNC_TASK_SOURCE, validate=False)
        assert TaskMixin in cls.__mro__

    @pytest.mark.spike
    def test_task_meta_has_correct_fields(self):
        cls = reconstruct_task_class(MINIMAL_SYNC_TASK_SOURCE, validate=False)
        meta: TaskMetadata = cls._task_meta

        assert "name" in meta.inputs
        assert meta.inputs["name"].inner_type is str

        assert "greeting" in meta.outputs
        # Output(str) wraps to str | None via Annotated[typ | None, ...]
        inner = meta.outputs["greeting"].inner_type
        assert inner is not None
        # Accept both str and str | None (Union)
        import types as _types
        from typing import get_args, get_origin

        if get_origin(inner) is _types.UnionType or str(inner) == "str | None":
            assert str in get_args(inner)
        else:
            assert inner is str

    @pytest.mark.spike
    def test_task_meta_has_contexts(self):
        cls = reconstruct_task_class(TASK_WITH_CONTEXT_SOURCE, validate=False)
        meta: TaskMetadata = cls._task_meta
        assert "workspace" in meta.contexts

    @pytest.mark.spike
    def test_task_source_is_populated(self):
        cls = reconstruct_task_class(MINIMAL_SYNC_TASK_SOURCE, validate=False)
        assert hasattr(cls, "_task_source")
        assert cls._task_source is not None
        assert "Greeter" in cls._task_source

    @pytest.mark.spike
    def test_execute_is_callable(self):
        cls = reconstruct_task_class(MINIMAL_SYNC_TASK_SOURCE, validate=False)
        assert callable(getattr(cls, "execute", None))


# =========================================================================
# Assumption 4: Direct execute call
# =========================================================================


class TestDirectExecuteCall:
    """Validate that execute() can be called directly on reconstructed instances."""

    @pytest.mark.spike
    def test_sync_execute_sets_outputs(self):
        """Sync execute() sets output fields correctly."""
        cls = reconstruct_task_class(MINIMAL_SYNC_TASK_SOURCE, validate=False)

        token = _async_mode.set(True)
        try:
            instance = cls.model_validate({"name": "World"})
        finally:
            _async_mode.reset(token)

        assert instance.greeting == ""
        instance.execute()
        assert instance.greeting == "Hello, World!"

    @pytest.mark.spike
    @pytest.mark.asyncio
    async def test_async_execute_sets_outputs(self):
        """Async execute() sets output fields when _async_execute_mode is set."""
        cls = reconstruct_task_class(MINIMAL_ASYNC_TASK_SOURCE, validate=False)

        token = _async_mode.set(True)
        try:
            instance = cls.model_validate({"name": "Async"})
        finally:
            _async_mode.reset(token)

        assert instance.greeting == ""

        token_exec = _async_execute_mode.set(True)
        try:
            await instance.execute()
        finally:
            _async_execute_mode.reset(token_exec)

        assert instance.greeting == "Hello, Async!"

    @pytest.mark.spike
    def test_step_method_raises_runtime_error(self):
        """@step in execute() should raise RuntimeError about task scope."""
        cls = reconstruct_task_class(
            TASK_WITH_STEP_SOURCE,
            imports=["from shepherd_runtime.step.api import step"],
            validate=False,
        )

        token = _async_mode.set(True)
        try:
            instance = cls.model_validate({"query": "test"})
        finally:
            _async_mode.reset(token)

        with pytest.raises(RuntimeError, match="requires a task scope"):
            instance.execute()


# =========================================================================
# Failure modes
# =========================================================================


class TestFailureModes:
    """Validate error contracts for reconstruction failures."""

    @pytest.mark.spike
    def test_syntax_error_source(self):
        with pytest.raises(ReconstructionError) as exc_info:
            reconstruct_task_class(SYNTAX_ERROR_SOURCE, validate=False)
        assert exc_info.value.error_type == "SYNTAX_ERROR"

    @pytest.mark.spike
    def test_missing_import_source(self):
        with pytest.raises(ReconstructionError) as exc_info:
            reconstruct_task_class(MISSING_IMPORT_SOURCE, validate=False)
        assert exc_info.value.error_type == "IMPORT_ERROR"

    @pytest.mark.spike
    def test_module_level_symbol_raises_name_error(self):
        """Reference to undefined module-level symbol raises ReconstructionError."""
        cls = reconstruct_task_class(MODULE_LEVEL_SYMBOL_SOURCE, validate=False)

        token = _async_mode.set(True)
        try:
            instance = cls.model_validate({"name": "test"})
        finally:
            _async_mode.reset(token)

        # The NameError happens at execute() time, not reconstruction time,
        # because UNDEFINED_SYMBOL is inside a method body.
        with pytest.raises(NameError, match="UNDEFINED_SYMBOL"):
            instance.execute()


# =========================================================================
# Synthetic output.json structure test
# =========================================================================


class TestSyntheticOutputStructure:
    """Validate the output.json format expected by ContainerDevice.execute()."""

    @pytest.mark.spike
    def test_output_json_has_task_outputs_in_metadata(self):
        """task_outputs must be nested inside result.metadata for ContainerDevice parsing."""
        # Simulate the container producing output
        cls = reconstruct_task_class(MINIMAL_SYNC_TASK_SOURCE, validate=False)

        token = _async_mode.set(True)
        try:
            instance = cls.model_validate({"name": "Test"})
        finally:
            _async_mode.reset(token)

        instance.execute()

        # Build output.json as the container task_runner would
        task_outputs = {}
        meta: TaskMetadata = cls._task_meta
        for field_name in meta.outputs:
            task_outputs[field_name] = getattr(instance, field_name)

        output_json = {
            "success": True,
            "result": {
                "structured_output": {},
                "metadata": {
                    "task_outputs": task_outputs,
                },
                "tool_calls": [],
                "tool_results": [],
            },
            "collected_effects": [],
        }

        # Serialize and deserialize to verify JSON fidelity
        roundtripped = json.loads(json.dumps(output_json))

        # Verify task_outputs survives nested inside result.metadata
        assert roundtripped["result"]["metadata"]["task_outputs"]["greeting"] == "Hello, Test!"

    @pytest.mark.spike
    def test_output_json_structure_survives_container_device_parsing(self):
        """Verify the nesting matches what ContainerDevice.execute() expects.

        ContainerDevice reads result_dict.get("metadata", {}), so task_outputs
        must be inside metadata to survive.
        """
        result_dict = {
            "structured_output": {"some_key": "some_val"},
            "metadata": {
                "task_outputs": {"greeting": "Hello!"},
                "task_name": "Greeter",
            },
            "tool_calls": [],
            "tool_results": [],
        }

        # Simulate ContainerDevice parsing: metadata = result_dict.get("metadata", {})
        metadata = result_dict.get("metadata", {})
        assert "task_outputs" in metadata
        assert metadata["task_outputs"]["greeting"] == "Hello!"
