"""Cross-package integration tests.

These tests verify that components from different packages
work together correctly.
"""

from shepherd_contexts.session import SessionState
from shepherd_core.effects import TaskStarted
from shepherd_core.scope import Stream
from shepherd_runtime.scope import Scope
from shepherd_tests import MockProvider


class TestScopeWithContexts:
    """Test Scope works with contexts from shepherd-contexts."""

    def test_bind_session_state(self, integration_scope: Scope) -> None:
        """SessionState can be bound to scope."""
        session = SessionState()
        integration_scope.bind("session", session)

        retrieved = integration_scope.get_context("session")
        assert retrieved is session

    def test_bind_multiple_contexts(self, integration_scope: Scope) -> None:
        """Multiple contexts can be bound."""
        session1 = SessionState(session_id="sess-1")
        session2 = SessionState(session_id="sess-2")

        integration_scope.bind("session1", session1)
        integration_scope.bind("session2", session2)

        assert integration_scope.get_context("session1") is session1
        assert integration_scope.get_context("session2") is session2


class TestEffectStream:
    """Test effect stream works across packages."""

    def test_stream_from_scope(self, integration_scope: Scope) -> None:
        """Scope provides access to effect stream."""
        stream = integration_scope.effects
        assert isinstance(stream, Stream)

    def test_stream_accepts_core_effects(self, integration_scope: Scope) -> None:
        """Stream accepts effects from shepherd-core."""
        import time

        # Emit an effect
        effect = TaskStarted(
            timestamp=time.time(),
            task_name="test_task",
            provider_id="test-provider",
        )
        integration_scope.emit(effect)

        # Verify it's in the stream
        assert len(integration_scope.effects) == 1


class TestProviderRegistration:
    """Test provider registration works with providers from shepherd-providers."""

    def test_register_mock_provider(self) -> None:
        """MockProvider from shepherd-tests can be registered."""
        with Scope(root=True) as scope:
            provider = MockProvider(name="test")
            scope.register_provider("default", provider, default=True)

            retrieved = scope.get_provider()
            assert retrieved is provider

    def test_register_multiple_providers(self) -> None:
        """Multiple providers can be registered."""
        with Scope(root=True) as scope:
            mock1 = MockProvider(name="mock1")
            mock2 = MockProvider(name="mock2")

            scope.register_provider("primary", mock1, default=True)
            scope.register_provider("secondary", mock2)

            assert scope.get_provider() is mock1
            assert scope.get_provider("secondary") is mock2


class TestMetaPackageIntegration:
    """Test meta-package hard-cut facade boundaries."""

    def test_configure_reset_are_owner_path_only(self) -> None:
        """Legacy scope configuration helpers are not top-level facade names."""
        import shepherd

        assert not hasattr(shepherd, "configure")
        assert not hasattr(shepherd, "reset")
        assert not hasattr(shepherd, "get_global_scope")

    def test_scope_owner_path_works(self) -> None:
        """Runtime ``Scope`` remains available from its owner module."""
        provider = MockProvider(name="test")
        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            assert scope.get_provider() is provider
