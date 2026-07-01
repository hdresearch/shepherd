"""Programmatic-task execution helpers for the runtime-owned task runner."""

from __future__ import annotations

import enum
import os
import traceback
from typing import Any

from shepherd_runtime.device.container.effect_collector import EffectCollector
from shepherd_runtime.device.container.io_protocol import write_error, write_output


async def _run_programmatic_task(
    task_spec: dict[str, Any],
    contexts: dict[str, Any],
) -> None:
    """Execute a programmatic (non-LLM) task inside the container."""
    try:
        from shepherd_runtime.task._mixin import _async_execute_mode, _async_mode
        from shepherd_runtime.task.reconstruction import reconstruct_task_class
        from shepherd_runtime.task.source_analysis import extract_task_source

        source = task_spec["task_source"]
        imports = task_spec.get("task_imports")
        task_inputs = task_spec.get("task_inputs", {})
        context_fields = task_spec.get("context_fields", {})
        output_fields = task_spec.get("output_fields", [])
        is_async = task_spec.get("is_async", False)

        task_class = reconstruct_task_class(source, imports, validate=False)

        token = _async_mode.set(True)
        try:
            instance = task_class.model_validate(task_inputs)  # type: ignore[attr-defined]
        finally:
            _async_mode.reset(token)

        for field_name, binding_name in context_fields.items():
            state = contexts.get(binding_name)
            if state is None:
                continue

            ctx_type = getattr(state, "context_type", None)
            if ctx_type is None and isinstance(state, dict):
                ctx_type = state.get("context_type")

            ctx_class = None
            if ctx_type == "workspace":
                try:
                    import shepherd_contexts.workspace.ref as _workspace_ref  # type: ignore[import-not-found,unused-ignore]
                except ImportError:
                    ctx_class = None
                else:
                    ctx_class = _workspace_ref.WorkspaceRef

            if ctx_class is not None and hasattr(ctx_class, "from_state"):
                reconstructed = ctx_class.from_state(state)  # type: ignore[arg-type]
                setattr(instance, field_name, reconstructed)
            else:
                setattr(instance, field_name, state)

        if is_async:
            token_exec = _async_execute_mode.set(True)
            try:
                await instance.execute()
            finally:
                _async_execute_mode.reset(token_exec)
        else:
            instance.execute()

        task_outputs: dict[str, Any] = {}
        for field_name in output_fields:
            value = getattr(instance, field_name, None)
            if value is None:
                task_outputs[field_name] = None
            elif hasattr(value, "model_dump"):
                task_outputs[field_name] = value.model_dump(mode="json")
            elif hasattr(type(value), "_task_meta"):
                task_outputs[field_name] = extract_task_source(type(value))
            elif isinstance(value, enum.Enum):
                task_outputs[field_name] = value.value
            else:
                task_outputs[field_name] = value

        collector = EffectCollector(_id=f"container-{os.getpid()}")
        result_dict = {
            "success": True,
            "output_text": "",
            "structured_output": None,
            "session_id": None,
            "metadata": {"task_outputs": task_outputs},
        }
        write_output(
            {
                "success": True,
                "result": result_dict,
                "collected_effects": collector.serialize_for_transport(),
                "error": None,
            }
        )
    except Exception:  # noqa: BLE001 -- container entrypoint must capture all errors for transport
        write_error(traceback.format_exc())


__all__ = ["_run_programmatic_task"]
