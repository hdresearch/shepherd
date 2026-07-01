"""Tests for runtime-owned context sandbox entrypoints."""

from __future__ import annotations

import importlib

import pytest
from shepherd_runtime.context.sandbox import GitWorktreeSandbox, OrphanedWorktreeRegistry


def test_runtime_context_sandbox_owner_path_exposes_runtime_symbols() -> None:
    assert GitWorktreeSandbox.__module__ == "shepherd_runtime.context.sandbox"
    assert OrphanedWorktreeRegistry.__module__ == "shepherd_runtime.context.sandbox"


def test_core_context_sandbox_module_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shepherd_core.context.sandbox")
