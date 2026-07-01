"""Tests for hardened runtime materialization owner paths."""

from __future__ import annotations

import importlib

import pytest
from shepherd_runtime.materialization import MaterializationIntent


def test_runtime_materialization_owner_path_is_the_public_intent_class() -> None:
    assert MaterializationIntent.__module__ == "shepherd_runtime.materialization"


def test_core_materialization_module_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shepherd_core.scope.materialization")


def test_core_scope_package_no_longer_exports_runtime_materialization_apis() -> None:
    core_package = importlib.reload(importlib.import_module("shepherd_core.scope"))

    for symbol_name in (
        "ContextMaterializer",
        "Materializable",
        "MaterializationIntent",
        "MaterializationResult",
        "Materializer",
        "clear_context_materializer_registry",
        "clear_materializer_registry",
        "get_context_materializer",
        "get_materializer",
        "is_materializable",
        "register_context_materializer",
        "register_materializer",
    ):
        assert not hasattr(core_package, symbol_name), f"{symbol_name} should not be exported from shepherd_core.scope"
