"""Unit tests for context state serialization.

Tests WorkspaceState, SessionStateData, and the context registry
for container transfer functionality.
"""

import pytest
from shepherd_core.foundation.protocols.device import ContextStateBase
from shepherd_runtime.device.container.context_registry import (
    _CONTEXT_DESERIALIZERS,
    ContextDeserializationError,
    deserialize_all_contexts,
    deserialize_context,
    get_context_deserializer,
    list_registered_types,
    register_context_deserializer,
)

# =============================================================================
# Test Fixtures
# =============================================================================


class MockContextState(ContextStateBase):
    """Mock context state for testing."""

    def __init__(self, value: str = "test"):
        self._value = value

    @property
    def context_type(self) -> str:
        return "mock"

    def rebind(self, env):
        new_value = env.get("MOCK_PATH", self._value)
        return MockContextState(new_value)

    @classmethod
    def from_dict(cls, data: dict) -> "MockContextState":
        return cls(value=data.get("value", "test"))

    def to_dict(self) -> dict:
        return {"context_type": "mock", "value": self._value}


@pytest.fixture(autouse=True)
def clean_registry():
    """Clean up registry after each test."""
    original = _CONTEXT_DESERIALIZERS.copy()
    yield
    _CONTEXT_DESERIALIZERS.clear()
    _CONTEXT_DESERIALIZERS.update(original)


# =============================================================================
# Context Registry Tests
# =============================================================================


class TestContextRegistration:
    """Tests for context deserializer registration."""

    def test_register_deserializer(self):
        """Test registering a context deserializer."""
        register_context_deserializer("mock", MockContextState.from_dict)

        assert "mock" in _CONTEXT_DESERIALIZERS
        # Note: bound methods are different objects, so we test functionality
        deserializer = get_context_deserializer("mock")
        assert deserializer is not None
        result = deserializer({"value": "test"})
        assert isinstance(result, MockContextState)

    def test_register_multiple_deserializers(self):
        """Test registering multiple deserializers."""
        register_context_deserializer("type_a", lambda d: MockContextState("a"))
        register_context_deserializer("type_b", lambda d: MockContextState("b"))

        types = list_registered_types()
        assert "type_a" in types
        assert "type_b" in types

    def test_get_unregistered_deserializer(self):
        """Test getting an unregistered deserializer returns None."""
        result = get_context_deserializer("nonexistent")
        assert result is None

    def test_list_registered_types(self):
        """Test listing registered types."""
        register_context_deserializer("test_type", MockContextState.from_dict)

        types = list_registered_types()
        assert "test_type" in types


class TestContextDeserialization:
    """Tests for context deserialization."""

    def test_deserialize_context_success(self):
        """Test successful context deserialization."""
        register_context_deserializer("mock", MockContextState.from_dict)

        state_data = {"context_type": "mock", "value": "hello"}
        state = deserialize_context(state_data)

        assert isinstance(state, MockContextState)
        assert state._value == "hello"

    def test_deserialize_context_with_rebind(self):
        """Test context deserialization with path rebinding."""
        register_context_deserializer("mock", MockContextState.from_dict)

        state_data = {"context_type": "mock", "value": "/host/path"}
        rebind_env = {"MOCK_PATH": "/container/path"}

        state = deserialize_context(state_data, rebind_env)

        assert state._value == "/container/path"

    def test_deserialize_context_missing_type(self):
        """Test deserialization fails without context_type."""
        state_data = {"value": "hello"}

        with pytest.raises(ContextDeserializationError) as exc_info:
            deserialize_context(state_data)

        assert "missing 'context_type'" in str(exc_info.value)

    def test_deserialize_context_unknown_type(self):
        """Test deserialization fails for unknown type."""
        state_data = {"context_type": "unknown", "value": "hello"}

        with pytest.raises(ContextDeserializationError) as exc_info:
            deserialize_context(state_data)

        assert "no deserializer registered" in str(exc_info.value)
        assert "unknown" in str(exc_info.value)

    def test_deserialize_context_factory_error(self):
        """Test deserialization handles factory errors."""

        def bad_factory(data):
            raise ValueError("Factory error")

        register_context_deserializer("bad", bad_factory)
        state_data = {"context_type": "bad"}

        with pytest.raises(ContextDeserializationError) as exc_info:
            deserialize_context(state_data)

        assert "deserializer raised" in str(exc_info.value)


class TestBatchDeserialization:
    """Tests for batch context deserialization."""

    def test_deserialize_all_contexts(self):
        """Test deserializing multiple contexts."""
        register_context_deserializer("mock", MockContextState.from_dict)

        states_data = {
            "workspace": {"context_type": "mock", "value": "ws"},
            "session": {"context_type": "mock", "value": "sess"},
        }

        contexts = deserialize_all_contexts(states_data)

        assert len(contexts) == 2
        assert "workspace" in contexts
        assert "session" in contexts
        assert contexts["workspace"]._value == "ws"
        assert contexts["session"]._value == "sess"

    def test_deserialize_all_with_rebind(self):
        """Test batch deserialization with rebinding."""
        register_context_deserializer("mock", MockContextState.from_dict)

        states_data = {
            "workspace": {"context_type": "mock", "value": "/host/ws"},
        }
        rebind_env = {"MOCK_PATH": "/container/ws"}

        contexts = deserialize_all_contexts(states_data, rebind_env)

        assert contexts["workspace"]._value == "/container/ws"

    def test_deserialize_all_empty(self):
        """Test batch deserialization with empty input."""
        contexts = deserialize_all_contexts({})
        assert contexts == {}


# =============================================================================
# WorkspaceState Tests (if available)
# =============================================================================


class TestWorkspaceStateSerialization:
    """Tests for WorkspaceState serialization."""

    def test_workspace_state_roundtrip(self):
        """Test WorkspaceState serialization roundtrip."""
        try:
            from shepherd_contexts.workspace.ref import WorkspaceState
        except ImportError:
            pytest.skip("shepherd-contexts not installed")

        original = WorkspaceState(
            path="/test/workspace",
            base_commit="abc123",
            pending_patches=(),
            capabilities=frozenset({"read", "write"}),
        )

        # Serialize
        data = original.to_dict()
        assert data["context_type"] == "workspace"
        assert data["path"] == "/test/workspace"
        assert data["base_commit"] == "abc123"

        # Deserialize
        restored = WorkspaceState.from_dict(data)
        assert restored.path == original.path
        assert restored.base_commit == original.base_commit
        assert restored.capabilities == original.capabilities

    def test_workspace_state_rebind(self):
        """Test WorkspaceState path rebinding."""
        try:
            from shepherd_contexts.workspace.ref import WorkspaceState
        except ImportError:
            pytest.skip("shepherd-contexts not installed")

        state = WorkspaceState(path="/host/workspace")
        rebound = state.rebind({"WORKSPACE_PATH": "/container/workspace"})

        assert rebound.path == "/container/workspace"
        # Original unchanged (immutable)
        assert state.path == "/host/workspace"


class TestSessionStateDataSerialization:
    """Tests for SessionStateData serialization."""

    def test_session_state_roundtrip(self):
        """Test SessionStateData serialization roundtrip."""
        try:
            from shepherd_contexts.session.state import SessionStateData
        except ImportError:
            pytest.skip("shepherd-contexts not installed")

        original = SessionStateData(
            session_id="sess-123",
            transcript_path="/host/.claude/transcript",
        )

        # Serialize
        data = original.to_dict()
        assert data["context_type"] == "session"
        assert data["session_id"] == "sess-123"
        assert data["transcript_path"] == "/host/.claude/transcript"

        # Deserialize
        restored = SessionStateData.from_dict(data)
        assert restored.session_id == original.session_id
        assert restored.transcript_path == original.transcript_path

    def test_session_state_rebind(self):
        """Test SessionStateData path rebinding."""
        try:
            from shepherd_contexts.session.state import SessionStateData
        except ImportError:
            pytest.skip("shepherd-contexts not installed")

        state = SessionStateData(
            session_id="sess-123",
            transcript_path="/host/.claude",
        )
        rebound = state.rebind({"SESSION_PATH": "/container/.claude"})

        assert rebound.transcript_path == "/container/.claude"
        assert rebound.session_id == "sess-123"  # Preserved


# =============================================================================
# ContextDeserializationError Tests
# =============================================================================


class TestContextDeserializationError:
    """Tests for ContextDeserializationError."""

    def test_error_message_format(self):
        """Test error message includes context type."""
        error = ContextDeserializationError("workspace", "test message")

        assert "workspace" in str(error)
        assert "test message" in str(error)

    def test_error_attributes(self):
        """Test error has correct attributes."""
        error = ContextDeserializationError("session", "details")

        assert error.context_type == "session"
