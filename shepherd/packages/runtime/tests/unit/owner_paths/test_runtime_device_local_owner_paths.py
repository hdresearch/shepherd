"""Tests for the runtime-owned LocalDevice owner path."""

from __future__ import annotations

import importlib

import pytest
from shepherd_runtime.device.local import LocalDevice as RuntimeLocalDevice
from shepherd_runtime.device.local import LocalSandboxHandle as RuntimeLocalSandboxHandle


def test_runtime_local_device_owner_path_exposes_runtime_symbols() -> None:
    assert RuntimeLocalDevice.__module__ == "shepherd_runtime.device.local"
    assert RuntimeLocalSandboxHandle.__module__ == "shepherd_runtime.device.local"


def test_core_local_device_module_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shepherd_core.device.local")
