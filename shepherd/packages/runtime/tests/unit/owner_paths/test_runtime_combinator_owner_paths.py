"""Tests for hardened runtime combinator owner paths."""

from __future__ import annotations

import importlib

import pytest
from shepherd_runtime.combinators import gate as runtime_gate


def test_runtime_combinator_owner_path_installs_runtime_gating_symbol() -> None:
    assert runtime_gate.__module__ == "shepherd_runtime.combinators.gating"


def test_core_combinator_package_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shepherd_core.combinators")
