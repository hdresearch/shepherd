"""Runtime-owned container device leaf owner paths."""

from __future__ import annotations

from .context_registry import (
    ContextDeserializationError,
    deserialize_all_contexts,
    deserialize_context,
    register_context_deserializer,
)
from .device import ContainerDevice
from .effect_collector import EffectCollector
from .fuse_overlay import (
    FuseOverlayManager,
    fuse_overlayfs_available,
)
from .overlay_extractor import OverlayEffectExtractor
from .podman import (
    ContainerSandbox,
    OverlayMount,
    PodmanSandboxManager,
)
from .preflight import (
    PreflightError,
    PreflightResult,
    preflight_check,
    preflight_check_spec,
)
from .provider_registry import (
    ProviderCreationError,
    create_provider,
    get_provider_factory,
    list_registered_provider_types,
    register_provider_factory,
)
from .stack_hooks import StackHooks
from .vm_extraction import (
    VMFileInfo,
    VMUpperLayerReader,
)
from .vm_paths import (
    VMCommandRunner,
    VMPathTranslator,
    is_macos,
    is_vm_available,
)

__all__ = [
    "ContainerDevice",
    "ContainerSandbox",
    "ContextDeserializationError",
    "EffectCollector",
    "FuseOverlayManager",
    "OverlayEffectExtractor",
    "OverlayMount",
    "PodmanSandboxManager",
    "PreflightError",
    "PreflightResult",
    "ProviderCreationError",
    "StackHooks",
    "VMCommandRunner",
    "VMFileInfo",
    "VMPathTranslator",
    "VMUpperLayerReader",
    "create_provider",
    "deserialize_all_contexts",
    "deserialize_context",
    "fuse_overlayfs_available",
    "get_provider_factory",
    "is_macos",
    "is_vm_available",
    "list_registered_provider_types",
    "preflight_check",
    "preflight_check_spec",
    "register_context_deserializer",
    "register_provider_factory",
]
