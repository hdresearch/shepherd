"""Tests for hardened runtime scope owner paths."""

from __future__ import annotations

import importlib
import sys

import pytest


def _load_scope_symbols() -> tuple[object, object, object, object, object]:
    runtime_module = importlib.reload(importlib.import_module("shepherd_runtime.scope"))
    core_package = importlib.reload(importlib.import_module("shepherd_core.scope"))

    return (
        runtime_module.Scope,
        runtime_module.ScopeProxy,
        runtime_module.current_scope,
        runtime_module.require_scope,
        core_package,
    )


def test_runtime_scope_owner_path_installs_runtime_scope_class() -> None:
    RuntimeScope, RuntimeScopeProxy, _, _, _ = _load_scope_symbols()

    assert RuntimeScope.__module__ == "shepherd_runtime.scope"
    assert RuntimeScopeProxy.__module__ == "shepherd_runtime.scope"


def test_core_scope_package_no_longer_exports_mutable_scope_shell() -> None:
    _, _, _, _, core_package = _load_scope_symbols()

    assert not hasattr(core_package, "Scope")
    assert not hasattr(core_package, "ScopeProxy")
    assert not hasattr(core_package, "current_scope")
    assert not hasattr(core_package, "require_scope")


def test_core_scope_module_path_is_removed() -> None:
    sys.modules.pop("shepherd_core.scope.scope", None)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shepherd_core.scope.scope")
