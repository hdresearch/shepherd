"""Runtime-owned device module for execution backends.

Devices determine HOW effects are captured and provide isolated
execution environments. See device.py in foundation/protocols for
the DeviceProtocol specification.

Available devices:
- local: In-process execution with git-based effect capture
- container: Isolated container execution with OverlayFS effect capture

Usage:
    # Context manager approach (recommended)
    from shepherd_runtime.device import Device

    with Scope() as scope:
        with Device("local"):  # or "container"
            result, outputs = await scope.execute("Fix the bug")

    # Explicit device registration
    from shepherd_runtime.device import register_device, get_device, LocalDevice

    my_device = LocalDevice()
    register_device("custom", my_device)
    device = get_device("custom")

Device routing for programmatic tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Device("container") affects both LLM-powered tasks and programmatic tasks
(those with a custom execute() method), provided the task class has
``_task_source`` set (which the ``@task`` decorator does automatically).

Programmatic tasks are routed through ``ExecutionLifecycle.run_executor()``,
which checks for device availability, isolation level, and source
availability before delegating to the device.

If ``_task_source`` is ``None`` (e.g., dynamically generated classes where
``inspect.getsource()`` fails), the task falls back to in-process execution.
"""

from __future__ import annotations

import contextlib
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from .container import (
    ContainerDevice,
    ContainerSandbox,
    EffectCollector,
    OverlayEffectExtractor,
    OverlayMount,
    PodmanSandboxManager,
    ProviderCreationError,
    create_provider,
    deserialize_context,
    register_context_deserializer,
    register_provider_factory,
)
from .errors import (
    BundleApplicationError,
    ContainerStartupError,
    DeviceBoundaryError,
    DeviceNestingError,
    DeviceSpaceError,
    EffectExtractionError,
    MountError,
    PatchApplicationError,
    TaskTimeoutError,
)
from .local import LocalDevice
from .transfer import (
    TransferBundle,
    collect_visible_patches,
    compute_content_hash,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from shepherd_core.foundation.protocols.device import DeviceProtocol

logger = logging.getLogger(__name__)

# =============================================================================
# Device Registry
# =============================================================================

# Context variable for current device (thread/task-local)
_current_device: ContextVar[DeviceProtocol | None] = ContextVar("current_device", default=None)

# Context variable for current device name (for nesting prevention error messages)
_current_device_name: ContextVar[str | None] = ContextVar("current_device_name", default=None)

# Global device registry
_device_registry: dict[str, DeviceProtocol] = {}

# Built-in devices are created lazily so importing the device module does not
# require container/VM infrastructure to be healthy unless container execution
# is actually requested.
_BUILTIN_DEVICE_NAMES = frozenset({"local", "container"})


def register_device(name: str, device: DeviceProtocol) -> None:
    """Register a device by name.

    Args:
        name: Device name (e.g., "local", "container", "cloud")
        device: Device instance implementing DeviceProtocol

    Example:
        from shepherd_runtime.device import register_device
        from shepherd_runtime.device.local import LocalDevice

        register_device("my-local", LocalDevice())
    """
    _device_registry[name] = device


def get_device(name: str) -> DeviceProtocol:
    """Get a registered device by name.

    Args:
        name: Device name to look up

    Returns:
        The registered device instance

    Raises:
        KeyError: If no device is registered with that name

    Example:
        device = get_device("local")
        sandbox = await device.create_sandbox(scope, config)
    """
    if name not in _device_registry:
        builtin = _create_builtin_device(name)
        if builtin is not None:
            _device_registry[name] = builtin
            return builtin

        available = sorted(set(_device_registry.keys()) | set(_BUILTIN_DEVICE_NAMES))
        raise KeyError(f"Device '{name}' not registered. Available: {available}")
    return _device_registry[name]


def get_current_device() -> DeviceProtocol | None:
    """Get the current device from context.

    Returns:
        The device set by the innermost Device() context manager,
        or None if no device context is active.

    Example:
        with Device("local"):
            device = get_current_device()  # Returns LocalDevice
            print(device.name)  # "local"
    """
    return _current_device.get()


def list_devices() -> list[str]:
    """List all registered device names.

    Returns:
        List of registered device names.
    """
    return sorted(set(_device_registry.keys()) | set(_BUILTIN_DEVICE_NAMES))


# =============================================================================
# Device Context Manager
# =============================================================================


@contextmanager
def Device(name: str, **kwargs) -> Iterator[DeviceProtocol]:  # type: ignore[no-untyped-def]
    """Context manager for setting the active device.

    Sets the device for the duration of the context. All scope operations
    within the context will use this device for execution.

    IMPORTANT: Device contexts cannot be nested. Attempting to enter a
    Device context while already in one will raise DeviceNestingError.
    If you need to change devices, exit the current context first.

    IMPORTANT: Container sandboxes created during this context are cleaned up
    when the context exits. This unmounts OverlayFS mounts and frees VM resources.
    Use preserve_overlays=True if you need to inspect overlays after exit.

    Args:
        name: Name of a registered device
        **kwargs: Device-specific options passed to device constructor.
            Common options:
            - debug (bool): Enable debug mode - preserves artifacts on failure,
              verbose logging. Can also be set via SHEPHERD_DEBUG=1 env var.
            - preserve_overlays (bool): If True, don't clean up container overlays
              on context exit. Useful for debugging. Default: False.

    Yields:
        The device instance

    Raises:
        KeyError: If no device is registered with that name
        DeviceNestingError: If already inside a Device context

    Example:
        from shepherd_runtime.device import Device

        with Scope() as scope:
            # Basic usage - overlays cleaned on exit
            with Device("container"):
                result = await scope.execute("Fix bug")

            # Sequential device contexts are OK
            with Device("local"):
                result = await scope.execute("Quick check")

            # With debug mode - preserves artifacts on failure
            with Device("container", debug=True):
                result = await scope.execute("Fix bug")

            # Preserve overlays for inspection
            with Device("container", preserve_overlays=True):
                result = await scope.execute("Fix bug")
            # Overlays still mounted - manual cleanup needed

    Note:
        Device contexts CANNOT be nested. To use different devices,
        exit the current device context first:

            # WRONG - will raise DeviceNestingError
            with Device("container"):
                with Device("local"):  # Raises!
                    ...

            # RIGHT - sequential contexts
            with Device("container"):
                ...
            with Device("local"):
                ...

        If kwargs are provided, a new device instance is created with those
        options. Otherwise, the registered device instance is used.

        Workspace layering works correctly within a single Device context:
        multiple tasks can see each other's file changes via stacked overlays.
    """
    from shepherd_runtime.scope import current_scope

    # Check for nested device context
    outer_device_name = _current_device_name.get()
    if outer_device_name is not None:
        raise DeviceNestingError(
            outer_device=outer_device_name,
            inner_device=name,
        )

    # Extract our options (preserve_overlays) vs device options
    preserve_overlays = kwargs.pop("preserve_overlays", False)

    # Create or get device instance
    device = _create_device_with_options(name, **kwargs) if kwargs else get_device(name)

    # Track sandboxes created during this context for cleanup
    scope = current_scope()
    sandboxes_before: set[str] = set()
    sandbox_tracker = None
    if scope is not None and hasattr(scope, "_sandbox_tracker"):
        sandbox_tracker = scope._sandbox_tracker
        sandboxes_before = set(sandbox_tracker._sandboxes.keys())

    token = _current_device.set(device)
    name_token = _current_device_name.set(name)
    try:
        yield device
    finally:
        _current_device.reset(token)
        _current_device_name.reset(name_token)

        # Clean up container sandboxes created during this Device context
        # This prevents overlay mount leaks that cause memory issues on macOS
        # (VirtioFS caches directory metadata for each mounted overlay)
        if not preserve_overlays and sandbox_tracker is not None:
            sandboxes_created = set(sandbox_tracker._sandboxes.keys()) - sandboxes_before
            if sandboxes_created:
                logger.debug(f"Cleaning up {len(sandboxes_created)} container sandbox(es) on Device context exit")
            for sandbox_id in list(sandboxes_created):
                sandbox = sandbox_tracker._sandboxes.get(sandbox_id)
                if sandbox is not None:
                    try:
                        # ContainerSandbox uses cleanup(), others may use discard()
                        if hasattr(sandbox, "cleanup"):
                            sandbox.cleanup()
                        elif hasattr(sandbox, "discard"):
                            sandbox.discard()
                        logger.debug(f"Cleaned up sandbox {sandbox_id}")
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"Sandbox cleanup failed for {sandbox_id}: {e}")
                    finally:
                        # Always remove from tracking, even if cleanup failed
                        # This prevents memory leaks from accumulating tracking references
                        sandbox_tracker._sandboxes.pop(sandbox_id, None)


def _create_device_with_options(name: str, **kwargs) -> DeviceProtocol:  # type: ignore[no-untyped-def]
    """Create a device instance with custom options.

    Args:
        name: Device type name ("local", "container")
        **kwargs: Options to pass to device constructor

    Returns:
        New device instance with options applied
    """
    if name == "container":
        return ContainerDevice(**kwargs)  # type: ignore[return-value]
    if name == "local":
        return LocalDevice(**kwargs)
    # For custom devices, try to get the registered device and copy with options
    base_device = get_device(name)
    # If it's a dataclass, we can use replace
    from dataclasses import is_dataclass, replace

    if is_dataclass(base_device):
        return replace(base_device, **kwargs)
    # Can't apply options to non-dataclass device
    raise ValueError(f"Cannot apply options to device '{name}'. Device must be a dataclass to accept options.")


# =============================================================================
# Auto-registration of built-in devices
# =============================================================================


def _create_builtin_device(name: str) -> DeviceProtocol | None:
    """Create a built-in device on demand.

    Built-ins are intentionally lazy so harmless accessors like
    Scope.current_device can import this module without instantiating
    the container device and probing the local VM.
    """
    if name == "container":
        return ContainerDevice()  # type: ignore[return-value]
    if name == "local":
        return LocalDevice()
    return None


def _register_builtins() -> None:
    """Register built-in devices.

    Called on module import to ensure standard devices are available.
    """
    # Register local eagerly because it is pure in-process and safe.
    if "local" not in _device_registry:
        _device_registry["local"] = LocalDevice()


# Defer registration until all modules are imported
# This is called at the end of the module after LocalDevice is available
# We use a try/except to handle the case where local module doesn't exist yet
with contextlib.suppress(ImportError):
    _register_builtins()


__all__ = [
    "BundleApplicationError",
    # Core devices
    "ContainerDevice",
    "ContainerSandbox",
    "ContainerStartupError",
    # Device selection API
    "Device",
    # Device boundary errors
    "DeviceBoundaryError",
    "DeviceNestingError",
    "DeviceSpaceError",
    # Effect collection
    "EffectCollector",
    "EffectExtractionError",
    "LocalDevice",
    "MountError",
    "OverlayEffectExtractor",
    # Container infrastructure
    "OverlayMount",
    "PatchApplicationError",
    "PodmanSandboxManager",
    "ProviderCreationError",
    "TaskTimeoutError",
    # Device state transfer
    "TransferBundle",
    "collect_visible_patches",
    "compute_content_hash",
    "create_provider",
    "deserialize_context",
    "get_current_device",
    "get_device",
    "list_devices",
    # Context serialization
    "register_context_deserializer",
    "register_device",
    # Provider instantiation (for containers)
    "register_provider_factory",
]
