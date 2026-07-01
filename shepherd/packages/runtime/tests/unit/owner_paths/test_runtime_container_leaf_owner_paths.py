"""Tests for hardened runtime container leaf owner paths."""

from __future__ import annotations

import importlib

import pytest
from shepherd_runtime.device.container.context_registry import (
    ContextDeserializationError as RuntimeContextDeserializationError,
)
from shepherd_runtime.device.container.effect_collector import EffectCollector as RuntimeEffectCollector
from shepherd_runtime.device.container.fuse_overlay import (
    FuseOverlayManager as RuntimeFuseOverlayManager,
)
from shepherd_runtime.device.container.fuse_overlay import (
    fuse_overlayfs_available as runtime_fuse_overlayfs_available,
)
from shepherd_runtime.device.container.provider_registry import ProviderCreationError as RuntimeProviderCreationError
from shepherd_runtime.device.container.stack_hooks import StackHooks as RuntimeStackHooks


def test_runtime_container_leaf_owner_paths_define_public_symbols() -> None:
    assert RuntimeEffectCollector.__module__ == "shepherd_runtime.device.container.effect_collector"
    assert RuntimeFuseOverlayManager.__module__ == "shepherd_runtime.device.container.fuse_overlay"
    assert RuntimeProviderCreationError.__module__ == "shepherd_runtime.device.container.provider_registry"
    assert RuntimeStackHooks.__module__ == "shepherd_runtime.device.container.stack_hooks"
    # ContextDeserializationError is defined in shepherd_runtime.registry and
    # re-exported through device.container.context_registry (thin delegation).
    assert RuntimeContextDeserializationError.__module__ == "shepherd_runtime.registry"
    assert callable(runtime_fuse_overlayfs_available)


@pytest.mark.parametrize(
    "module_name",
    [
        "shepherd_core.device.container.context_registry",
        "shepherd_core.device.container.effect_collector",
        "shepherd_core.device.container.provider_registry",
    ],
)
def test_core_container_leaf_modules_removed(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)
