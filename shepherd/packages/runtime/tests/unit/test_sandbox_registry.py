"""Tests for SandboxRegistry.

Covers:
- Factory registration and lookup
- Sandbox creation
- Warning when requires_sandbox() but no factory
- Registry copy and clear operations
- Default registry management
- Runtime sandbox-registry API functions
"""

import warnings
from dataclasses import dataclass

import pytest
from shepherd_core.types import ReversibilityLevel
from shepherd_runtime.context import BindableContext
from shepherd_runtime.sandbox_registry import (
    SandboxRegistry,
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
        return f"mock-sandbox:{self.name}"

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


@pytest.fixture
def registry() -> SandboxRegistry:
    """Create a fresh registry for each test."""
    return SandboxRegistry()


@pytest.fixture(autouse=True)
def reset_global_registry() -> None:
    """Give each test a fresh global registry, then restore the prior one.

    Saving and restoring (rather than leaving the registry reset to empty)
    keeps this module's reset from leaking into other test modules that share
    the same process. Under pytest-xdist an unrelated module can run on this
    worker afterward and rely on an import-time factory registration (e.g.
    SimpleWorkspace), which a bare ``reset_default_registry()`` would wipe.
    """
    import shepherd_runtime.sandbox_registry as _registry_module

    saved = _registry_module._default_registry
    reset_default_registry()
    yield
    _registry_module._default_registry = saved


# =============================================================================
# Tests: SandboxRegistry Registration
# =============================================================================


class TestSandboxRegistryRegistration:
    """Tests for SandboxRegistry.register() and has_factory()."""

    def test_register_adds_factory(self, registry: SandboxRegistry) -> None:
        """register() should add factory to registry."""

        def factory(ctx: BindableContext) -> MockSandbox:
            return MockSandbox(ctx)

        registry.register("TestContext", factory)

        assert registry.has_factory("TestContext")
        assert "TestContext" in registry
        assert len(registry) == 1

    def test_register_overwrites_existing(self, registry: SandboxRegistry) -> None:
        """register() with same name should overwrite existing factory."""

        def factory1(ctx: BindableContext) -> MockSandbox:
            return MockSandbox(ctx)

        def factory2(ctx: BindableContext) -> MockSandbox:
            return MockSandbox(ctx)

        registry.register("TestContext", factory1)
        registry.register("TestContext", factory2)

        assert len(registry) == 1

    def test_has_factory_returns_false_for_unregistered(self, registry: SandboxRegistry) -> None:
        """has_factory() should return False for unregistered type."""
        assert not registry.has_factory("UnknownContext")
        assert "UnknownContext" not in registry


# =============================================================================
# Tests: SandboxRegistry.create_for()
# =============================================================================


class TestSandboxRegistryCreateFor:
    """Tests for SandboxRegistry.create_for()."""

    def test_create_for_returns_sandbox_when_registered(self, registry: SandboxRegistry) -> None:
        """create_for() should return sandbox when factory is registered."""
        registry.register("MockContextWithSandbox", MockSandbox)

        ctx = MockContextWithSandbox(name="test")
        sandbox = registry.create_for(ctx)

        assert sandbox is not None
        assert isinstance(sandbox, MockSandbox)
        assert sandbox.context is ctx

    def test_create_for_returns_none_when_no_factory(self, registry: SandboxRegistry) -> None:
        """create_for() should return None when no factory registered."""

        @dataclass(frozen=True)
        class UnregisteredContext(BindableContext):
            @property
            def context_id(self) -> str:
                return "unregistered"

            @property
            def reversibility(self) -> ReversibilityLevel:
                return ReversibilityLevel.AUTO

        ctx = UnregisteredContext()
        sandbox = registry.create_for(ctx)

        assert sandbox is None

    def test_create_for_warns_when_requires_sandbox_true(self, registry: SandboxRegistry) -> None:
        """create_for() should warn when context requires sandbox but no factory."""
        ctx = MockContextWithSandbox(name="warn-test")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = registry.create_for(ctx)

            assert result is None
            assert len(w) == 1
            assert issubclass(w[0].category, UserWarning)
            assert "requires_sandbox()=True" in str(w[0].message)
            assert "MockContextWithSandbox" in str(w[0].message)
            assert "register_sandbox_factory" in str(w[0].message)

    def test_create_for_no_warning_when_requires_sandbox_false(self, registry: SandboxRegistry) -> None:
        """create_for() should not warn when requires_sandbox() is False."""
        ctx = MockContextWithoutSandbox(name="no-warn-test")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = registry.create_for(ctx)

            assert result is None
            assert len(w) == 0

    def test_create_for_no_warning_when_factory_registered(self, registry: SandboxRegistry) -> None:
        """create_for() should not warn when factory is registered."""
        registry.register("MockContextWithSandbox", MockSandbox)
        ctx = MockContextWithSandbox(name="no-warn-registered")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = registry.create_for(ctx)

            assert result is not None
            # Filter for our specific warning
            sandbox_warnings = [x for x in w if "requires_sandbox" in str(x.message)]
            assert len(sandbox_warnings) == 0


# =============================================================================
# Tests: SandboxRegistry.copy() and clear()
# =============================================================================


class TestSandboxRegistryCopyAndClear:
    """Tests for SandboxRegistry.copy() and clear()."""

    def test_copy_creates_independent_registry(self, registry: SandboxRegistry) -> None:
        """copy() should create an independent registry."""
        registry.register("Context1", MockSandbox)

        copied = registry.copy()

        # Both should have the factory
        assert copied.has_factory("Context1")
        assert registry.has_factory("Context1")

        # Modifying copy should not affect original
        copied.register("Context2", MockSandbox)
        assert copied.has_factory("Context2")
        assert not registry.has_factory("Context2")

        # Modifying original should not affect copy
        registry.register("Context3", MockSandbox)
        assert registry.has_factory("Context3")
        assert not copied.has_factory("Context3")

    def test_clear_removes_all_factories(self, registry: SandboxRegistry) -> None:
        """clear() should remove all registered factories."""
        registry.register("Context1", MockSandbox)
        registry.register("Context2", MockSandbox)

        assert len(registry) == 2

        registry.clear()

        assert len(registry) == 0
        assert not registry.has_factory("Context1")
        assert not registry.has_factory("Context2")


# =============================================================================
# Tests: Default Registry Management
# =============================================================================


class TestDefaultRegistryManagement:
    """Tests for get_default_registry() and reset_default_registry()."""

    def test_get_default_registry_returns_same_instance(self) -> None:
        """get_default_registry() should return the same instance."""
        registry1 = get_default_registry()
        registry2 = get_default_registry()

        assert registry1 is registry2

    def test_reset_default_registry_creates_new_instance(self) -> None:
        """reset_default_registry() should create fresh instance on next get."""
        registry1 = get_default_registry()
        registry1.register("TestContext", MockSandbox)

        reset_default_registry()

        registry2 = get_default_registry()
        assert registry2 is not registry1
        assert not registry2.has_factory("TestContext")


# =============================================================================
# Tests: Backward Compatible API
# =============================================================================


class TestBackwardCompatibleAPI:
    """Tests for the backward-compatible module-level functions."""

    def test_register_sandbox_factory_uses_default_registry(self) -> None:
        """register_sandbox_factory() should use the default registry."""
        register_sandbox_factory("TestContext", MockSandbox)

        registry = get_default_registry()
        assert registry.has_factory("TestContext")

    def test_create_sandbox_for_context_uses_default_registry(self) -> None:
        """create_sandbox_for_context() should use the default registry."""
        register_sandbox_factory("MockContextWithSandbox", MockSandbox)

        ctx = MockContextWithSandbox(name="test")
        sandbox = create_sandbox_for_context(ctx)

        assert sandbox is not None
        assert isinstance(sandbox, MockSandbox)
        assert sandbox.context is ctx

    def test_create_sandbox_for_context_returns_none_when_no_factory(self) -> None:
        """create_sandbox_for_context() should return None when no factory."""

        @dataclass(frozen=True)
        class NewUnregisteredContext(BindableContext):
            @property
            def context_id(self) -> str:
                return "new-unregistered"

            @property
            def reversibility(self) -> ReversibilityLevel:
                return ReversibilityLevel.AUTO

        ctx = NewUnregisteredContext()
        sandbox = create_sandbox_for_context(ctx)

        assert sandbox is None

    def test_backward_compat_warns_when_requires_sandbox(self) -> None:
        """create_sandbox_for_context() should warn via default registry."""
        ctx = MockContextWithSandbox(name="compat-warn-test")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = create_sandbox_for_context(ctx)

            assert result is None
            assert len(w) == 1
            assert "requires_sandbox()=True" in str(w[0].message)
