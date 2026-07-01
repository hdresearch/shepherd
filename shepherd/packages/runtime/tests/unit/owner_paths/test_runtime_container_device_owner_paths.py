"""Tests for hardened runtime container device owner paths."""

from __future__ import annotations

import importlib

import pytest
from shepherd_runtime.device.container.device import ContainerDevice as RuntimeContainerDevice
from shepherd_runtime.device.container.device import ContainerSandbox as RuntimeContainerSandbox


def test_runtime_container_device_owner_path_defines_public_symbols() -> None:
    assert RuntimeContainerDevice.__module__ == "shepherd_runtime.device.container.device"
    assert RuntimeContainerSandbox.__module__ == "shepherd_runtime.device.container.podman"


def test_core_container_device_module_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shepherd_core.device.container.device")
