"""Unit tests for programmatic (non-LLM) task execution in containers.

Tests the _run_programmatic_task() function, input serialization in
ContainerDevice, and preflight validation for task_spec-based execution.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from shepherd_core.foundation.protocols.device import ExecutionSpec, TaskSpec
from shepherd_runtime.device.container.preflight import preflight_check_spec
from shepherd_runtime.device.container.task_runner import (
    _run_programmatic_task,
)

# =============================================================================
# Task source fixtures
# =============================================================================

SIMPLE_TASK_SOURCE = """\
@task
class SimpleTask:
    name: str = Input(str)
    greeting: str = Output(str)

    def execute(self):
        self.greeting = f"hello {self.name}"
"""

ASYNC_TASK_SOURCE = """\
@task
class AsyncTask:
    name: str = Input(str)
    greeting: str = Output(str)

    async def execute(self):
        self.greeting = f"async hello {self.name}"
"""

SYNTAX_ERROR_SOURCE = """\
@task
class BrokenTask:
    name: str = Input(
    # missing closing paren
"""

ENUM_OUTPUT_SOURCE = """\
import enum
from typing import Any

class Color(enum.Enum):
    RED = "red"
    GREEN = "green"

@task
class EnumTask:
    color: Any = Output(Any)

    def execute(self):
        self.color = Color.RED
"""

PYDANTIC_OUTPUT_SOURCE = """\
from pydantic import BaseModel
from typing import Any

class Info(BaseModel):
    x: int
    y: str

@task
class PydanticTask:
    info: Any = Output(Any)

    def execute(self):
        self.info = Info(x=42, y="hello")
"""

PRIMITIVE_OUTPUT_SOURCE = """\
@task
class PrimitiveTask:
    count: int = Output(int)
    label: str = Output(str)

    def execute(self):
        self.count = 7
        self.label = "done"
"""

# =============================================================================
# Helpers
# =============================================================================

WRITE_OUTPUT_PATH = "shepherd_runtime.device.container.programmatic_execution.write_output"
WRITE_ERROR_PATH = "shepherd_runtime.device.container.programmatic_execution.write_error"


def _make_task_spec(
    source: str,
    task_inputs: dict[str, Any] | None = None,
    output_fields: list[str] | None = None,
    is_async: bool = False,
) -> dict[str, Any]:
    return {
        "task_source": source,
        "task_imports": [],
        "task_inputs": task_inputs or {},
        "output_fields": output_fields or [],
        "context_fields": {},
        "is_async": is_async,
    }


# =============================================================================
# 1. Happy path
# =============================================================================


class TestRunProgrammaticTaskHappyPath:
    """_run_programmatic_task with a simple synchronous task."""

    @pytest.mark.asyncio
    async def test_happy_path(self):
        spec = _make_task_spec(
            SIMPLE_TASK_SOURCE,
            task_inputs={"name": "test"},
            output_fields=["greeting"],
        )
        with patch(WRITE_OUTPUT_PATH) as mock_write, patch(WRITE_ERROR_PATH) as mock_err:
            await _run_programmatic_task(spec, contexts={})

        mock_err.assert_not_called()
        mock_write.assert_called_once()
        output = mock_write.call_args[0][0]
        assert output["success"] is True
        assert output["result"]["metadata"]["task_outputs"]["greeting"] == "hello test"


# =============================================================================
# 2. Async execute
# =============================================================================


class TestRunProgrammaticTaskAsync:
    """_run_programmatic_task with an async execute method."""

    @pytest.mark.asyncio
    async def test_async_execute(self):
        spec = _make_task_spec(
            ASYNC_TASK_SOURCE,
            task_inputs={"name": "world"},
            output_fields=["greeting"],
            is_async=True,
        )
        with patch(WRITE_OUTPUT_PATH) as mock_write, patch(WRITE_ERROR_PATH) as mock_err:
            await _run_programmatic_task(spec, contexts={})

        mock_err.assert_not_called()
        mock_write.assert_called_once()
        output = mock_write.call_args[0][0]
        assert output["success"] is True
        assert output["result"]["metadata"]["task_outputs"]["greeting"] == "async hello world"


# =============================================================================
# 3. Error propagation
# =============================================================================


class TestRunProgrammaticTaskError:
    """_run_programmatic_task with invalid source code."""

    @pytest.mark.asyncio
    async def test_syntax_error_calls_write_error(self):
        spec = _make_task_spec(SYNTAX_ERROR_SOURCE, output_fields=["name"])
        with patch(WRITE_OUTPUT_PATH) as mock_write, patch(WRITE_ERROR_PATH) as mock_err:
            await _run_programmatic_task(spec, contexts={})

        mock_write.assert_not_called()
        mock_err.assert_called_once()
        error_str = mock_err.call_args[0][0]
        assert isinstance(error_str, str)
        # Should contain a traceback
        assert "Traceback" in error_str or "SyntaxError" in error_str


# =============================================================================
# 4. Output serialization
# =============================================================================


class TestOutputSerialization:
    """Verify enum, BaseModel, and primitive output serialization."""

    @pytest.mark.asyncio
    async def test_enum_serialized_as_value(self):
        spec = _make_task_spec(ENUM_OUTPUT_SOURCE, output_fields=["color"])
        with patch(WRITE_OUTPUT_PATH) as mock_write, patch(WRITE_ERROR_PATH):
            await _run_programmatic_task(spec, contexts={})

        output = mock_write.call_args[0][0]
        assert output["success"] is True
        assert output["result"]["metadata"]["task_outputs"]["color"] == "red"

    @pytest.mark.asyncio
    async def test_pydantic_serialized_with_model_dump(self):
        spec = _make_task_spec(PYDANTIC_OUTPUT_SOURCE, output_fields=["info"])
        with patch(WRITE_OUTPUT_PATH) as mock_write, patch(WRITE_ERROR_PATH):
            await _run_programmatic_task(spec, contexts={})

        output = mock_write.call_args[0][0]
        assert output["success"] is True
        info = output["result"]["metadata"]["task_outputs"]["info"]
        assert info == {"x": 42, "y": "hello"}

    @pytest.mark.asyncio
    async def test_primitive_identity(self):
        spec = _make_task_spec(
            PRIMITIVE_OUTPUT_SOURCE,
            output_fields=["count", "label"],
        )
        with patch(WRITE_OUTPUT_PATH) as mock_write, patch(WRITE_ERROR_PATH):
            await _run_programmatic_task(spec, contexts={})

        output = mock_write.call_args[0][0]
        assert output["success"] is True
        outputs = output["result"]["metadata"]["task_outputs"]
        assert outputs["count"] == 7
        assert outputs["label"] == "done"


# =============================================================================
# 5. Output nesting in metadata
# =============================================================================


class TestOutputNesting:
    """task_outputs must live inside result.metadata, not as a sibling."""

    @pytest.mark.asyncio
    async def test_task_outputs_inside_metadata(self):
        spec = _make_task_spec(
            SIMPLE_TASK_SOURCE,
            task_inputs={"name": "nest"},
            output_fields=["greeting"],
        )
        with patch(WRITE_OUTPUT_PATH) as mock_write, patch(WRITE_ERROR_PATH):
            await _run_programmatic_task(spec, contexts={})

        output = mock_write.call_args[0][0]
        # task_outputs must be nested under result -> metadata
        assert "task_outputs" in output["result"]["metadata"]
        # task_outputs must NOT be a sibling of result
        assert "task_outputs" not in output


# =============================================================================
# 6. EffectCollector present
# =============================================================================


class TestEffectCollectorPresent:
    """Output dict must include collected_effects with valid structure."""

    @pytest.mark.asyncio
    async def test_collected_effects_present(self):
        spec = _make_task_spec(
            SIMPLE_TASK_SOURCE,
            task_inputs={"name": "fx"},
            output_fields=["greeting"],
        )
        with patch(WRITE_OUTPUT_PATH) as mock_write, patch(WRITE_ERROR_PATH):
            await _run_programmatic_task(spec, contexts={})

        output = mock_write.call_args[0][0]
        collected = output["collected_effects"]
        assert collected is not None
        assert "collector_id" in collected
        assert "effects" in collected
        assert isinstance(collected["effects"], list)


# =============================================================================
# 7. Input serialization in ContainerDevice
# =============================================================================


class TestInputSerializationContainerDevice:
    """Verify TaskSpec serialization into input_data dict."""

    def test_task_spec_serialization_structure(self):
        ts = TaskSpec(
            task_source="@task\nclass T:\n    pass",
            task_class_name="T",
            task_imports=("import os",),
            task_inputs={"x": 1},
            output_fields=("result",),
            context_fields={"ws": "workspace"},
            is_async=True,
        )
        spec = ExecutionSpec(
            prompt="",
            provider_config={},
            tools=["Read"],
            task_spec=ts,
        )

        # Reproduce the serialization logic from ContainerDevice.execute()
        input_data: dict[str, Any] = {
            "prompt": spec.prompt,
            "provider_config": dict(spec.provider_config),
            "tools": list(spec.tools) if spec.tools else None,
            "output_format": dict(spec.output_format) if spec.output_format else None,
        }
        if spec.task_spec is not None:
            t = spec.task_spec
            input_data["task_spec"] = {
                "task_source": t.task_source,
                "task_class_name": t.task_class_name,
                "task_imports": list(t.task_imports),
                "task_inputs": dict(t.task_inputs),
                "output_fields": list(t.output_fields),
                "context_fields": dict(t.context_fields),
                "is_async": t.is_async,
            }

        ts_dict = input_data["task_spec"]
        assert ts_dict["task_source"] == ts.task_source
        assert ts_dict["task_class_name"] == "T"
        assert ts_dict["task_imports"] == ["import os"]
        assert ts_dict["task_inputs"] == {"x": 1}
        assert ts_dict["output_fields"] == ["result"]
        assert ts_dict["context_fields"] == {"ws": "workspace"}
        assert ts_dict["is_async"] is True


# =============================================================================
# 8. Preflight validation
# =============================================================================


class TestPreflightCheckSpec:
    """preflight_check_spec with various task_spec / prompt combinations."""

    def test_task_spec_valid_source_no_errors(self):
        spec = ExecutionSpec(
            prompt="",
            provider_config={},
            task_spec=TaskSpec(
                task_source="@task\nclass T:\n    pass",
                task_class_name="T",
                task_imports=(),
                task_inputs={},
                output_fields=(),
                context_fields={},
            ),
        )
        result = preflight_check_spec(spec)
        assert result.is_ok

    def test_task_spec_empty_source_error(self):
        spec = ExecutionSpec(
            prompt="",
            provider_config={},
            task_spec=TaskSpec(
                task_source="",
                task_class_name="T",
                task_imports=(),
                task_inputs={},
                output_fields=(),
                context_fields={},
            ),
        )
        result = preflight_check_spec(spec)
        assert not result.is_ok
        assert any("task_source" in e for e in result.errors)

    def test_no_task_spec_empty_prompt_error(self):
        spec = ExecutionSpec(
            prompt="",
            provider_config={},
            task_spec=None,
        )
        result = preflight_check_spec(spec)
        assert not result.is_ok
        assert any("prompt" in e for e in result.errors)

    def test_no_task_spec_valid_prompt_and_config_ok(self):
        spec = ExecutionSpec(
            prompt="Do something",
            provider_config={"provider_type": "mock"},
            task_spec=None,
        )
        result = preflight_check_spec(spec)
        assert result.is_ok
