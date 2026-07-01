"""Base test classes for provider and context implementations.

All provider and context implementations should inherit from these
base classes to ensure they conform to the expected interface.
"""

from abc import ABC, abstractmethod

from shepherd_core import (
    ExecutionContext,
    Provider,
    ProviderBinding,
    ProviderCapabilities,
    ReversibilityLevel,
)


class BaseProviderTests(ABC):
    """Base test class for provider implementations.

    All provider implementations should inherit from this class
    and implement get_provider() to return a configured provider instance.

    Example:
        class TestClaudeProvider(BaseProviderTests):
            def get_provider(self):
                return ClaudeProvider(name="test")

            def test_claude_specific_feature(self):
                # Provider-specific tests
                ...
    """

    @abstractmethod
    def get_provider(self) -> Provider:
        """Return a configured provider instance for testing."""
        ...

    def test_provider_has_name(self) -> None:
        """Provider should have a name."""
        provider = self.get_provider()
        assert provider.name is not None
        assert len(provider.name) > 0

    def test_provider_has_provider_id(self) -> None:
        """Provider should have a unique provider_id."""
        provider = self.get_provider()
        assert provider.provider_id is not None
        assert len(provider.provider_id) > 0

    def test_provider_has_capabilities(self) -> None:
        """Provider should declare its capabilities."""
        provider = self.get_provider()
        caps = provider.capabilities
        assert isinstance(caps, ProviderCapabilities)

    def test_provider_validates_binding(self) -> None:
        """Provider should validate bindings without error for valid bindings."""
        provider = self.get_provider()
        binding = ProviderBinding(context_id="test")
        # Should not raise
        provider.validate_binding(binding)

    def test_no_standard_tests_removed(self) -> None:
        """Ensure standard tests aren't silently removed by subclasses."""
        base_tests = {m for m in dir(BaseProviderTests) if m.startswith("test_")}
        impl_tests = {m for m in dir(self) if m.startswith("test_")}

        # Check that base tests are still present
        # (They should be inherited, not overridden with pass)
        for test_name in base_tests:
            if test_name == "test_no_standard_tests_removed":
                continue
            assert test_name in impl_tests, f"Standard test {test_name} was removed"


class BaseContextTests(ABC):
    """Base test class for context implementations.

    All context implementations should inherit from this class
    and implement get_context() to return a configured context instance.

    Example:
        class TestWorkspaceRef(BaseContextTests):
            def get_context(self):
                return WorkspaceRef.from_path("/tmp/test-repo")

            def test_workspace_specific_feature(self):
                # Context-specific tests
                ...
    """

    @abstractmethod
    def get_context(self) -> ExecutionContext:
        """Return a configured context instance for testing."""
        ...

    def test_context_has_context_id(self) -> None:
        """Context should have a stable context_id."""
        context = self.get_context()
        assert context.context_id is not None
        assert len(context.context_id) > 0

    def test_context_has_reversibility(self) -> None:
        """Context should declare its reversibility level."""
        context = self.get_context()
        assert context.reversibility in (
            ReversibilityLevel.AUTO,
            ReversibilityLevel.COMPENSABLE,
            ReversibilityLevel.NONE,
        )

    def test_context_configure_returns_binding(self) -> None:
        """Context.configure() should return a ProviderBinding."""
        context = self.get_context()
        caps = ProviderCapabilities()
        binding = context.configure(caps)
        assert isinstance(binding, ProviderBinding)

    def test_context_binding_has_context_id(self) -> None:
        """Binding from configure() should have the context's ID."""
        context = self.get_context()
        caps = ProviderCapabilities()
        binding = context.configure(caps)
        assert binding.context_id == context.context_id

    def test_context_lifecycle_completes(self) -> None:
        """Context should complete full lifecycle without error."""
        context = self.get_context()

        # Configure
        caps = ProviderCapabilities()
        binding = context.configure(caps)
        assert binding is not None

        # Prepare
        prepared = context.prepare()
        assert prepared is not None

        # Cleanup (should not raise)
        context.cleanup(None)

    def test_no_standard_tests_removed(self) -> None:
        """Ensure standard tests aren't silently removed by subclasses."""
        base_tests = {m for m in dir(BaseContextTests) if m.startswith("test_")}
        impl_tests = {m for m in dir(self) if m.startswith("test_")}

        for test_name in base_tests:
            if test_name == "test_no_standard_tests_removed":
                continue
            assert test_name in impl_tests, f"Standard test {test_name} was removed"
