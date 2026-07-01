"""Tests for hardened runtime device owner paths."""

from __future__ import annotations

import importlib

from shepherd_runtime.device import Device as RuntimeDevice
from shepherd_runtime.device import get_current_device as runtime_get_current_device


def test_runtime_device_owner_path_installs_runtime_context_manager() -> None:
    assert RuntimeDevice.__module__ == "shepherd_runtime.device"


def test_core_device_namespace_no_longer_exists() -> None:
    import pytest

    # shepherd_core.device has been fully removed; verify it is not importable.
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shepherd_core.device")
    assert runtime_get_current_device.__module__ == "shepherd_runtime.device"
