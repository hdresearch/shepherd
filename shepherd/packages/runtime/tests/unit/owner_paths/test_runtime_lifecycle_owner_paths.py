"""Tests for hardened runtime lifecycle owner paths."""

from __future__ import annotations

import importlib

import pytest
from shepherd_runtime.lifecycle import ExecutionLifecycle as RuntimeExecutionLifecycle


def test_runtime_lifecycle_owner_path_is_the_public_lifecycle_class() -> None:
    assert RuntimeExecutionLifecycle.__module__ == "shepherd_runtime.lifecycle"


def test_core_lifecycle_package_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shepherd_core.lifecycle")

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shepherd_core.lifecycle.lifecycle")
