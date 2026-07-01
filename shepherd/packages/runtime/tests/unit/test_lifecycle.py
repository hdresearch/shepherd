"""Tests for ExecutionLifecycle sandbox factory and requires_sandbox() functionality.

Covers:
- Sandbox factory registration
- requires_sandbox() warning when no factory registered
- create_sandbox_for_context() behavior

NOTE: These tests use the runtime sandbox registry module directly for better isolation.
"""

import warnings
from dataclasses import dataclass

import pytest
from shepherd_core.types import ReversibilityLevel
from shepherd_runtime.context import BindableContext
from shepherd_runtime.sandbox_registry import (
    create_sandbox_for_context,
    get_default_registry,
    register_sandbox_factory,
    reset_default_registry,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@dataclass(frozen=True)
class MockContextWithSandbox(BindableContext):
    """Test context that requires a sandbox."""

    name: str = "test"

    @property
    def context_id(self) -> str:
        return f"mock:{self.name}"

    @property
    def reversibility(self) -> ReversibilityLevel:
        return ReversibilityLevel.AUTO

    @classmethod
    def requires_sandbox(cls) -> bool:
        return True


@dataclass(frozen=True)
class MockContextWithoutSandbox(BindableContext):
    """Test context that doesn't require a sandbox."""

    name: str = "test"

    @property
    def context_id(self) -> str:
        return f"mock-no-sandbox:{self.name}"

    @property
    def reversibility(self) -> ReversibilityLevel:
        return ReversibilityLevel.AUTO

    @classmethod
    def requires_sandbox(cls) -> bool:
        return False


class MockSandbox:
    """Minimal sandbox for testing."""

    def __init__(self, context: BindableContext) -> None:
        self.context = context


@pytest.fixture(autouse=True)
def reset_registry() -> None:
    """Give each test a fresh global registry, then restore the prior one.

    Saving and restoring (rather than leaving the registry reset to empty)
    keeps this module's reset from leaking into other test modules that share
    the same process under pytest-xdist (see test_sandbox_registry for detail).
    """
    import shepherd_runtime.sandbox_registry as _registry_module

    saved = _registry_module._default_registry
    reset_default_registry()
    yield
    _registry_module._default_registry = saved


# =============================================================================
# Tests: requires_sandbox() default behavior
# =============================================================================


class TestRequiresSandboxDefault:
    """Tests for BindableContext.requires_sandbox() default behavior."""

    def test_bindable_context_requires_sandbox_defaults_to_false(self) -> None:
        """BindableContext.requires_sandbox() should return False by default."""
        assert BindableContext.requires_sandbox() is False

    def test_context_without_override_returns_false(self) -> None:
        """Context without requires_sandbox override inherits False."""

        @dataclass(frozen=True)
        class PlainContext(BindableContext):
            @property
            def context_id(self) -> str:
                return "plain"

            @property
            def reversibility(self) -> ReversibilityLevel:
                return ReversibilityLevel.AUTO

        assert PlainContext.requires_sandbox() is False

    def test_context_with_override_returns_true(self) -> None:
        """Context with requires_sandbox override can return True."""
        assert MockContextWithSandbox.requires_sandbox() is True

    def test_context_with_explicit_false_returns_false(self) -> None:
        """Context can explicitly return False."""
        assert MockContextWithoutSandbox.requires_sandbox() is False


# =============================================================================
# Tests: Sandbox Factory Registration
# =============================================================================


class TestSandboxFactoryRegistration:
    """Tests for register_sandbox_factory() and factory lookup."""

    def test_register_sandbox_factory_adds_to_registry(self) -> None:
        """register_sandbox_factory() should add factory to registry."""
        factory_name = "TestRegistrationContext"

        def factory(ctx: BindableContext) -> MockSandbox:
            return MockSandbox(ctx)

        register_sandbox_factory(factory_name, factory)

        registry = get_default_registry()
        assert registry.has_factory(factory_name)

    def test_register_sandbox_factory_overwrites_existing(self) -> None:
        """Registering same name twice overwrites the factory."""
        factory_name = "TestOverwriteContext"

        def factory1(ctx: BindableContext) -> MockSandbox:
            return MockSandbox(ctx)

        def factory2(ctx: BindableContext) -> MockSandbox:
            return MockSandbox(ctx)

        register_sandbox_factory(factory_name, factory1)
        register_sandbox_factory(factory_name, factory2)

        # Both registrations should succeed (second overwrites first)
        registry = get_default_registry()
        assert registry.has_factory(factory_name)


# =============================================================================
# Tests: create_sandbox_for_context()
# =============================================================================


class TestCreateSandboxForContext:
    """Tests for create_sandbox_for_context() behavior."""

    def test_returns_sandbox_when_factory_registered(self) -> None:
        """Should return sandbox instance when factory is registered."""
        factory_name = "MockContextWithSandbox"

        register_sandbox_factory(factory_name, MockSandbox)

        ctx = MockContextWithSandbox(name="test")
        sandbox = create_sandbox_for_context(ctx)

        assert sandbox is not None
        assert isinstance(sandbox, MockSandbox)
        assert sandbox.context is ctx

    def test_returns_none_when_no_factory_registered(self) -> None:
        """Should return None when no factory is registered."""

        @dataclass(frozen=True)
        class UnregisteredContext(BindableContext):
            @property
            def context_id(self) -> str:
                return "unregistered"

            @property
            def reversibility(self) -> ReversibilityLevel:
                return ReversibilityLevel.AUTO

        ctx = UnregisteredContext()
        sandbox = create_sandbox_for_context(ctx)

        assert sandbox is None

    def test_warns_when_requires_sandbox_true_but_no_factory(self) -> None:
        """Should warn when context requires sandbox but no factory registered."""
        ctx = MockContextWithSandbox(name="warn-test")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = create_sandbox_for_context(ctx)

            assert result is None
            assert len(w) == 1
            assert issubclass(w[0].category, UserWarning)
            assert "requires_sandbox()=True" in str(w[0].message)
            assert "MockContextWithSandbox" in str(w[0].message)
            assert "register_sandbox_factory" in str(w[0].message)

    def test_no_warning_when_requires_sandbox_false(self) -> None:
        """Should not warn when context doesn't require sandbox."""
        ctx = MockContextWithoutSandbox(name="no-warn-test")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = create_sandbox_for_context(ctx)

            assert result is None
            assert len(w) == 0

    def test_no_warning_when_factory_registered(self) -> None:
        """Should not warn when factory is registered (even if requires_sandbox=True)."""
        factory_name = "MockContextWithSandbox"

        register_sandbox_factory(factory_name, MockSandbox)

        ctx = MockContextWithSandbox(name="no-warn-registered")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = create_sandbox_for_context(ctx)

            assert result is not None
            # Filter for our specific warning (ignore other warnings)
            sandbox_warnings = [x for x in w if "requires_sandbox" in str(x.message)]
            assert len(sandbox_warnings) == 0
