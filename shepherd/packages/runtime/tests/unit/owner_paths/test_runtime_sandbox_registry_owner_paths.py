"""Tests for hardened runtime sandbox-registry owner paths."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from shepherd_core.types import ReversibilityLevel
from shepherd_runtime.context import BindableContext
from shepherd_runtime.lifecycle import register_sandbox_factory
from shepherd_runtime.sandbox_registry import (
    create_sandbox_for_context,
    reset_default_registry,
    sandbox_factories,
)
from shepherd_runtime.sandbox_registry import (
    get_default_registry as get_runtime_default_registry,
)


@dataclass(frozen=True)
class _RuntimeRegistryContext(BindableContext):
    @property
    def context_id(self) -> str:
        return "runtime-registry"

    @property
    def reversibility(self) -> ReversibilityLevel:
        return ReversibilityLevel.AUTO


class _RuntimeRegistrySandbox:
    def __init__(self, context: _RuntimeRegistryContext) -> None:
        self.context = context


@pytest.fixture(autouse=True)
def reset_runtime_registry() -> None:
    original_factories = dict(get_runtime_default_registry().factories)
    reset_default_registry()
    yield
    reset_default_registry()
    for context_type_name, factory in original_factories.items():
        register_sandbox_factory(context_type_name, factory)


def test_runtime_register_sandbox_factory_updates_runtime_registry() -> None:
    register_sandbox_factory(_RuntimeRegistryContext.__name__, _RuntimeRegistrySandbox)

    assert _RuntimeRegistryContext.__name__ in get_runtime_default_registry().factories
    assert _RuntimeRegistryContext.__name__ in sandbox_factories


def test_runtime_create_sandbox_for_context_uses_shared_registry() -> None:
    register_sandbox_factory(_RuntimeRegistryContext.__name__, _RuntimeRegistrySandbox)

    context = _RuntimeRegistryContext()
    sandbox = create_sandbox_for_context(context)

    assert isinstance(sandbox, _RuntimeRegistrySandbox)
    assert sandbox.context is context


def test_runtime_sandbox_factories_view_tracks_registry_reset() -> None:
    register_sandbox_factory(_RuntimeRegistryContext.__name__, _RuntimeRegistrySandbox)
    assert _RuntimeRegistryContext.__name__ in sandbox_factories

    reset_default_registry()

    assert _RuntimeRegistryContext.__name__ not in sandbox_factories
