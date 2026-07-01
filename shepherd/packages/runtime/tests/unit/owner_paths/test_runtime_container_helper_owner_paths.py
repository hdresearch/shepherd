"""Tests for hardened runtime container helper owner paths."""

from __future__ import annotations

import importlib

import pytest
from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor as RuntimeOverlayEffectExtractor
from shepherd_runtime.device.container.podman import (
    ContainerSandbox as RuntimeContainerSandbox,
)
from shepherd_runtime.device.container.podman import (
    OverlayMount as RuntimeOverlayMount,
)
from shepherd_runtime.device.container.podman import (
    PodmanSandboxManager as RuntimePodmanSandboxManager,
)
from shepherd_runtime.device.container.preflight import (
    PreflightError as RuntimePreflightError,
)
from shepherd_runtime.device.container.preflight import (
    PreflightResult as RuntimePreflightResult,
)
from shepherd_runtime.device.container.preflight import (
    preflight_check as runtime_preflight_check,
)
from shepherd_runtime.device.container.preflight import (
    preflight_check_spec as runtime_preflight_check_spec,
)
from shepherd_runtime.device.container.vm_extraction import (
    VMFileInfo as RuntimeVMFileInfo,
)
from shepherd_runtime.device.container.vm_extraction import (
    VMUpperLayerReader as RuntimeVMUpperLayerReader,
)
from shepherd_runtime.device.container.vm_paths import (
    VMCommandRunner as RuntimeVMCommandRunner,
)
from shepherd_runtime.device.container.vm_paths import (
    VMPathTranslator as RuntimeVMPathTranslator,
)
from shepherd_runtime.device.container.vm_paths import (
    is_macos as runtime_is_macos,
)
from shepherd_runtime.device.container.vm_paths import (
    is_vm_available as runtime_is_vm_available,
)


def test_runtime_container_helper_owner_paths_define_public_symbols() -> None:
    assert RuntimeOverlayEffectExtractor.__module__ == "shepherd_runtime.device.container.overlay_extractor"
    assert RuntimeContainerSandbox.__module__ == "shepherd_runtime.device.container.podman"
    assert RuntimeOverlayMount.__module__ == "shepherd_runtime.device.container.podman"
    assert RuntimePodmanSandboxManager.__module__ == "shepherd_runtime.device.container.podman"
    assert RuntimePreflightError.__module__ == "shepherd_runtime.device.container.preflight"
    assert RuntimePreflightResult.__module__ == "shepherd_runtime.device.container.preflight"
    assert runtime_preflight_check.__module__ == "shepherd_runtime.device.container.preflight"
    assert runtime_preflight_check_spec.__module__ == "shepherd_runtime.device.container.preflight"
    assert RuntimeVMFileInfo.__module__ == "shepherd_runtime.device.container.vm_extraction"
    assert RuntimeVMUpperLayerReader.__module__ == "shepherd_runtime.device.container.vm_extraction"
    assert RuntimeVMCommandRunner.__module__ == "shepherd_runtime.device.container.vm_paths"
    assert RuntimeVMPathTranslator.__module__ == "shepherd_runtime.device.container.vm_paths"
    assert runtime_is_macos.__module__ == "shepherd_runtime.device.container.vm_paths"
    assert runtime_is_vm_available.__module__ == "shepherd_runtime.device.container.vm_paths"


@pytest.mark.parametrize(
    "module_name",
    [
        "shepherd_core.device.container.overlay_extractor",
        "shepherd_core.device.container.podman",
        "shepherd_core.device.container.preflight",
        "shepherd_core.device.container.vm_extraction",
        "shepherd_core.device.container.vm_paths",
    ],
)
def test_core_container_helper_modules_removed(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)
