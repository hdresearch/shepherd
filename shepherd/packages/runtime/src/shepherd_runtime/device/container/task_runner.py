"""Runtime-owned container task runner entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import traceback
from pathlib import Path

from shepherd_runtime.device.container.context_registry import deserialize_all_contexts
from shepherd_runtime.device.container.effect_collector import EffectCollector
from shepherd_runtime.device.container.io_protocol import (
    REBIND_ENV_PATH,
    TASK_INPUT_PATH,
    TASK_OUTPUT_PATH,
    load_input,
    load_rebind_env,
    write_error,
    write_output,
)
from shepherd_runtime.device.container.programmatic_execution import _run_programmatic_task
from shepherd_runtime.device.container.provider_execution import (
    ProviderNotAvailableError,
    _build_binding_from_contexts,
    _create_provider,
    _enforce_container_session_invariants,
    _MockProvider,
    _serialize_execution_result,
    _validate_session_resumable,
)

logger = logging.getLogger(__name__)

_deserializers_registered = False
_deserializers_lock = threading.Lock()
LAYERS_ROOT = Path("/layers")


def _ensure_context_deserializers_registered() -> None:
    """Ensure context deserializers are registered by importing context modules."""
    global _deserializers_registered

    if _deserializers_registered:
        return

    with _deserializers_lock:
        if _deserializers_registered:
            return

        try:
            import shepherd_contexts.workspace.ref as _workspace_ref  # type: ignore[import-not-found,unused-ignore]

            logger.debug("Registered workspace deserializer via %s", _workspace_ref.__name__)
        except ImportError as e:
            logger.warning("Could not import workspace context: %s", e)

        try:
            import shepherd_contexts.session.state as _session_state  # type: ignore[import-not-found,unused-ignore]

            logger.debug("Registered session deserializer via %s", _session_state.__name__)
        except ImportError as e:
            logger.warning("Could not import session context: %s", e)

        _deserializers_registered = True


def _discover_fuse_layers() -> list[Path] | None:
    """Discover workspace layers from SHEPHERD_LAYERS and /layers mounts."""
    layers_env = os.environ.get("SHEPHERD_LAYERS")
    if not layers_env:
        return None

    layer_names = layers_env.split(":")
    layers: list[Path] = []
    for name in layer_names:
        layer_path = LAYERS_ROOT / name
        if layer_path.exists() and layer_path.is_dir():
            layers.append(layer_path)
        else:
            logger.warning("Layer %s from SHEPHERD_LAYERS not found, skipping", layer_path)

    if not layers:
        logger.warning("No valid layers found from SHEPHERD_LAYERS, falling back to legacy mode")
        return None

    logger.info("Discovered %s workspace layers from SHEPHERD_LAYERS: %s", len(layers), layer_names)
    return layers


async def run_task() -> None:
    """Main container task runner entrypoint."""
    log_level = os.environ.get("LOGLEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[task_runner] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        import time as _time

        container_timings: dict[str, float] = {}

        t0 = _time.perf_counter()
        task_input = load_input()

        prompt = task_input.get("prompt", "")
        provider_config = task_input.get("provider_config", {})
        context_states = task_input.get("context_states", {})
        tools = task_input.get("tools")
        task_name = task_input.get("task_name")
        output_format = task_input.get("output_format")
        task_spec = task_input.get("task_spec")
        container_timings["container.load_input"] = (_time.perf_counter() - t0) * 1000

        logger.info("Task runner starting: prompt=%s...", prompt[:50])

        rebind_env = load_rebind_env()
        logger.debug("Rebind environment: %s", rebind_env)

        t0 = _time.perf_counter()
        _ensure_context_deserializers_registered()
        contexts = deserialize_all_contexts(context_states, rebind_env)
        container_timings["container.deserialize_contexts"] = (_time.perf_counter() - t0) * 1000
        logger.info("Loaded contexts: %s", list(contexts.keys()))

        if task_spec is not None:
            await _run_programmatic_task(task_spec, contexts)
            return

        collector = EffectCollector(_id=f"container-{os.getpid()}")
        t0 = _time.perf_counter()
        provider = _create_provider(provider_config)
        container_timings["container.create_provider"] = (_time.perf_counter() - t0) * 1000
        logger.info("Created provider: %s", provider.provider_id)

        binding = _build_binding_from_contexts(contexts, tools, output_format)
        binding = _enforce_container_session_invariants(binding, provider_config=provider_config)

        overlay_mgr = None
        hooks_config = None

        from shepherd_runtime.device.container.fuse_overlay import fuse_overlayfs_available

        if fuse_overlayfs_available():
            from shepherd_runtime.device.container.fuse_overlay import FuseOverlayManager
            from shepherd_runtime.device.container.stack_hooks import StackHooks

            try:
                overlay_mgr = FuseOverlayManager()
                lower_layers = _discover_fuse_layers()
                overlay_mgr.setup(lower_layers=lower_layers)
                stack_hooks = StackHooks(overlay_mgr, collector)
                hooks_config = stack_hooks.as_hooks_dict()
                logger.info("Per-tool-call overlay isolation enabled (fuse-overlayfs)")
            except (ImportError, OSError, RuntimeError) as e:
                logger.warning("fuse-overlayfs setup failed, falling back to single-layer mode: %s", e)
                overlay_mgr = None
                hooks_config = None
        else:
            logger.debug("fuse-overlayfs not available -- single-layer mode")

        logger.info("Calling provider.execute_sdk with prompt=%s...", prompt[:50])
        logger.debug("  binding=%s", binding)
        logger.debug("  collector_id=%s", collector.id)

        try:
            from shepherd_core.provider import DefaultProviderRuntime

            t0 = _time.perf_counter()
            result = await provider.execute_sdk(
                prompt=prompt,
                binding=binding,
                runtime=DefaultProviderRuntime.from_emitter(collector, task_name=task_name),
                hooks=hooks_config,
            )
            container_timings["container.provider_execution"] = (_time.perf_counter() - t0) * 1000
            logger.info("Execution complete: success=%s", result.success)
            logger.debug("  output_text length=%s", len(result.output_text) if result.output_text else 0)
            logger.debug("  structured_output=%s", result.structured_output)
        except Exception as sdk_error:
            logger.exception("SDK execution failed: %s: %s", type(sdk_error).__name__, sdk_error)
            logger.exception(traceback.format_exc())
            raise
        finally:
            if overlay_mgr:
                overlay_mgr.teardown()

        result_dict = _serialize_execution_result(result)

        # Pass container-side timings through result metadata
        if container_timings:
            result_metadata = result_dict.setdefault("metadata", {})
            result_metadata["_container_timings"] = container_timings

        t0 = _time.perf_counter()
        write_output(
            {
                "success": True,
                "result": result_dict,
                "collected_effects": collector.serialize_for_transport(),
                "error": None,
            }
        )
        # Log write_output timing (can't include in output since it's already written)
        logger.debug("container.write_output: %.1fms", (_time.perf_counter() - t0) * 1000)

    except FileNotFoundError as e:
        write_error(f"Input file not found: {e}")
        sys.exit(1)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.exception("Task runner failed: %s", error_msg)
        write_error(error_msg)
        sys.exit(1)


def main() -> None:
    """Synchronous entry point."""
    asyncio.run(run_task())


if __name__ == "__main__":
    main()


__all__ = [
    "REBIND_ENV_PATH",
    "TASK_INPUT_PATH",
    "TASK_OUTPUT_PATH",
    "ProviderNotAvailableError",
    "_MockProvider",
    "_build_binding_from_contexts",
    "_create_provider",
    "_ensure_context_deserializers_registered",
    "_run_programmatic_task",
    "_serialize_execution_result",
    "_validate_session_resumable",
    "load_input",
    "load_rebind_env",
    "main",
    "run_task",
    "write_error",
    "write_output",
]
