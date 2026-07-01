"""Tests for runtime-only handler owner paths."""

from __future__ import annotations

import importlib

import pytest
from shepherd_runtime.effect_materialization import GitWorkspacePatchMaterializer
from shepherd_runtime.handlers import (
    CompositeHandler as RuntimeCompositeHandler,
)
from shepherd_runtime.handlers import (
    HandlerRegistry as RuntimeHandlerRegistry,
)
from shepherd_runtime.handlers import (
    LoggingHandler as RuntimeLoggingHandler,
)
from shepherd_runtime.handlers import (
    PassthroughHandler as RuntimePassthroughHandler,
)
from shepherd_runtime.handlers import (
    SimpleHandlerContext as RuntimeSimpleHandlerContext,
)


def test_runtime_handler_owner_paths_install_runtime_classes() -> None:
    assert RuntimeCompositeHandler.__module__ == "shepherd_runtime.handlers.builtin.composite"
    assert RuntimeHandlerRegistry.__module__ == "shepherd_runtime.handlers.registry"
    assert RuntimeLoggingHandler.__module__ == "shepherd_runtime.handlers.builtin.logging"
    assert RuntimePassthroughHandler.__module__ == "shepherd_runtime.handlers.builtin.passthrough"
    assert RuntimeSimpleHandlerContext.__module__ == "shepherd_runtime.handlers.testing"
    assert GitWorkspacePatchMaterializer.__module__ == "shepherd_runtime.effect_materialization"


def test_core_handler_package_is_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shepherd_core.handlers")
