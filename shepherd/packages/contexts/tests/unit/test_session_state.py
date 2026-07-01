"""Tests for SessionState and SessionStateData.

Tests for session resumption changes from PLAN-session-resumption-containers.md:
- SessionCreated effect cwd field (Change 1)
- SessionStateData host_cwd roundtrip (Change 2)
- SessionState.apply_effect() host_cwd propagation (Change 2)
"""

from shepherd_contexts.session.effects import SessionCreated, SessionForked
from shepherd_contexts.session.state import SessionState, SessionStateData
from shepherd_core.types import ExecutionResult


class TestSessionCreatedEffect:
    """Tests for SessionCreated effect cwd field (Change 1)."""

    def test_session_created_has_cwd_field(self):
        """SessionCreated effect should have cwd field."""
        effect = SessionCreated(
            session_id="abc123",
            context_id="session:new",
            transcript_path="/path/to/transcript.jsonl",
            cwd="/Users/alice/project",
        )

        assert effect.cwd == "/Users/alice/project"
        assert effect.session_id == "abc123"
        assert effect.transcript_path == "/path/to/transcript.jsonl"

    def test_session_created_cwd_default_none(self):
        """SessionCreated cwd should default to None."""
        effect = SessionCreated(
            session_id="abc123",
            context_id="session:new",
        )

        assert effect.cwd is None


class TestSessionStateData:
    """Tests for SessionStateData host_cwd field (Change 2)."""

    def test_host_cwd_in_to_dict(self):
        """host_cwd should be included in to_dict()."""
        state = SessionStateData(
            session_id="abc123",
            transcript_path="/path/to/transcript.jsonl",
            host_cwd="/Users/alice/project",
        )

        data = state.to_dict()

        assert data["host_cwd"] == "/Users/alice/project"
        assert data["session_id"] == "abc123"
        assert data["transcript_path"] == "/path/to/transcript.jsonl"
        assert data["context_type"] == "session"

    def test_host_cwd_in_from_dict(self):
        """host_cwd should be restored from from_dict()."""
        data = {
            "session_id": "abc123",
            "transcript_path": "/path/to/transcript.jsonl",
            "host_cwd": "/Users/alice/project",
            "context_type": "session",
        }

        state = SessionStateData.from_dict(data)

        assert state.host_cwd == "/Users/alice/project"
        assert state.session_id == "abc123"
        assert state.transcript_path == "/path/to/transcript.jsonl"

    def test_host_cwd_roundtrip(self):
        """host_cwd should survive serialization roundtrip."""
        original = SessionStateData(
            session_id="abc123",
            transcript_path="/path/to/transcript.jsonl",
            host_cwd="/Users/alice/my_project",
        )

        data = original.to_dict()
        restored = SessionStateData.from_dict(data)

        assert restored.host_cwd == original.host_cwd
        assert restored.session_id == original.session_id
        assert restored.transcript_path == original.transcript_path

    def test_host_cwd_default_none(self):
        """host_cwd should default to None."""
        state = SessionStateData(
            session_id="abc123",
        )

        assert state.host_cwd is None


class TestSessionStateApplyEffect:
    """Tests for SessionState.apply_effect() host_cwd propagation (Change 2)."""

    def test_apply_session_created_sets_host_cwd(self):
        """Applying SessionCreated should set host_cwd from effect.cwd."""
        state = SessionState()

        effect = SessionCreated(
            session_id="abc123",
            context_id="session:new",
            transcript_path="/path/to/transcript.jsonl",
            cwd="/Users/alice/project",
        )

        new_state = state.apply_effect(effect)

        assert new_state.host_cwd == "/Users/alice/project"
        assert new_state.session_id == "abc123"
        assert new_state.transcript_path == "/path/to/transcript.jsonl"

    def test_apply_session_forked_preserves_host_cwd(self):
        """Applying SessionForked should preserve original host_cwd."""
        # Start with a session that has host_cwd
        state = SessionState(
            session_id="abc123",
            transcript_path="/path/to/abc123.jsonl",
            host_cwd="/Users/alice/project",
        )

        # Fork to new session
        effect = SessionForked(
            parent_session_id="abc123",
            new_session_id="def456",
            context_id="session:abc123",
            transcript_path="/path/to/def456.jsonl",
        )

        new_state = state.apply_effect(effect)

        # host_cwd should be preserved from original session
        assert new_state.host_cwd == "/Users/alice/project"
        assert new_state.session_id == "def456"
        assert new_state.transcript_path == "/path/to/def456.jsonl"


class TestSessionStateToState:
    """Tests for SessionState.to_state() host_cwd propagation."""

    def test_to_state_includes_host_cwd(self):
        """to_state() should include host_cwd."""
        state = SessionState(
            session_id="abc123",
            transcript_path="/path/to/transcript.jsonl",
            host_cwd="/Users/alice/project",
        )

        state_data = state.to_state()

        assert state_data.host_cwd == "/Users/alice/project"
        assert state_data.session_id == "abc123"
        assert state_data.transcript_path == "/path/to/transcript.jsonl"


class TestSessionStateFromState:
    """Tests for SessionState.from_state() host_cwd propagation."""

    def test_from_state_restores_host_cwd(self):
        """from_state() should restore host_cwd."""
        state_data = SessionStateData(
            session_id="abc123",
            transcript_path="/path/to/transcript.jsonl",
            host_cwd="/Users/alice/project",
        )

        state = SessionState.from_state(state_data)

        assert state.host_cwd == "/Users/alice/project"
        assert state.session_id == "abc123"


class TestSessionStateExtractEffects:
    """Tests for SessionState.extract_effects() cwd propagation."""

    def test_extract_effects_includes_cwd(self):
        """extract_effects should include cwd from result.metadata."""
        state = SessionState()  # No existing session

        result = ExecutionResult(
            success=True,
            output_text="test",
            session_id="abc123",
            metadata={
                "transcript_path": "/path/to/abc123.jsonl",
                "cwd": "/Users/alice/project",
            },
        )

        effects = state.extract_effects(None, result)

        assert len(effects) == 1
        effect = effects[0]
        assert isinstance(effect, SessionCreated)
        assert effect.cwd == "/Users/alice/project"
        assert effect.session_id == "abc123"
