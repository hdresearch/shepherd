"""Shared fixtures for lifecycle phase tests.

Provides mock implementations for testing the 7-phase execution lifecycle:
- MockContext: Minimal BindableContext for testing prepare/cleanup
- MockSandbox: Minimal sandbox for testing setup/discard
- Fixtures for scope, provider, emitter, binding, and PhaseContext
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from shepherd_core.types import (
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ReversibilityLevel,
)
from shepherd_runtime._lifecycle import Attribution, PhaseContext
from shepherd_runtime.context import BindableContext

# =============================================================================
# Mock Implementations
# =============================================================================


@dataclass(frozen=True)
class MockContext(BindableContext):
    """Minimal mock context for testing."""

    name: str = "mock"
    _prepared: bool = False
    _cleaned: bool = False

    @property
    def context_id(self) -> str:
        return f"mock:{self.name}"

    @property
    def reversibility(self) -> ReversibilityLevel:
        return ReversibilityLevel.AUTO

    def configure(self, capabilities: ProviderCapabilities) -> ProviderBinding:
        return ProviderBinding(context_ids=[self.context_id])

    def prepare(self) -> "MockContext":
        return MockContext(name=self.name, _prepared=True)

    def cleanup(self, error: Exception | None = None) -> None:
        pass

    def extract_effects(self, sandbox: Any, result: Any) -> Iterator[Any]:
        return iter([])

    def apply_effect(self, effect: Any) -> "MockContext":
        return self


class MockSandbox:
    """Minimal sandbox for testing."""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.setup_called = False
        self.discard_called = False

    def setup(self, context: Any) -> None:
        self.setup_called = True

    def discard(self) -> None:
        self.discard_called = True


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_scope() -> MagicMock:
    """Create a mock scope."""
    scope = MagicMock()
    scope.emit = MagicMock()
    scope.update_context = MagicMock()
    scope.mark_binding_lifecycle = MagicMock()
    return scope


@pytest.fixture
def mock_provider() -> MagicMock:
    """Create a mock provider."""
    provider = MagicMock()
    provider.provider_id = "test-provider"
    provider.capabilities = ProviderCapabilities(provider_type="test")
    provider.validate_binding = MagicMock()
    provider.execute_sdk = AsyncMock(return_value=ExecutionResult(output_text="Test output"))
    return provider


@pytest.fixture
def mock_emitter() -> MagicMock:
    """Create a mock effect emitter."""
    emitter = MagicMock()
    emitter.emit = MagicMock()
    return emitter


@pytest.fixture
def mock_binding() -> MagicMock:
    """Create a mock binding with state."""
    context = MockContext(name="workspace")
    binding = MagicMock()
    binding.name = "workspace"
    binding.context = context
    return binding


@pytest.fixture
def basic_context(
    mock_scope: MagicMock,
    mock_provider: MagicMock,
    mock_binding: MagicMock,
) -> PhaseContext:
    """Create a basic PhaseContext for testing."""
    return PhaseContext(
        scope=mock_scope,
        provider=mock_provider,
        attribution=Attribution(
            task_name="test-task",
            provider_id="test-provider",
            source="llm",
        ),
        task_name="test-task",
        prompt="Test prompt",
        bindings=(mock_binding,),
    )
