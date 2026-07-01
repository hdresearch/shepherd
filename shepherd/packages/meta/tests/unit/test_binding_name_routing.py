"""Tests for binding_name effect routing (stable routing mechanism).

These tests validate the dual-mode effect routing system:
1. binding_name (stable routing) - Routes by binding name, unaffected by context state
2. context_id (semantic routing) - Routes by context identity (existing behavior)

The binding_name routing was introduced to fix the SessionState multi-effect bug
where context_id changes during effect application (e.g., when session_id is set).

Key scenarios tested:
- Effect base class binding_name field
- binding_name precedence over context_id in routing
- Multi-effect SessionState application (the bug fix)
- Stream query by binding_name
- by_binding() filter method
"""

from shepherd_contexts import SessionState, WorkspaceRef
from shepherd_contexts.session.effects import SessionCreated, SessionForked
from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
from shepherd_core.effects import (
    DiffPatch,
    FileCreate,
    FileRead,
    TaskStarted,
)
from shepherd_core.scope import Stream
from shepherd_runtime.scope import Scope

# =============================================================================
# Effect binding_name Field Tests
# =============================================================================


class TestEffectBindingName:
    """Test binding_name on Effect base class."""

    def test_effect_has_binding_name_field(self):
        """Effect should have binding_name field defaulting to None."""
        effect = TaskStarted(task_name="Test")
        assert hasattr(effect, "binding_name")
        assert effect.binding_name is None

    def test_effect_binding_name_can_be_set(self):
        """Effect should accept binding_name in constructor."""
        effect = FileCreate(
            path="foo.py",
            content_hash="abc123",
            binding_name="workspace",
        )
        assert effect.binding_name == "workspace"

    def test_effect_with_binding_method(self):
        """Effect.with_binding() should set binding_name."""
        effect = FileRead(path="foo.py")
        updated = effect.with_binding("workspace")

        assert effect.binding_name is None  # Original unchanged
        assert updated.binding_name == "workspace"

    def test_effect_with_attribution_sets_binding_name(self):
        """with_attribution() should accept binding_name parameter."""
        effect = FileRead(path="foo.py")
        updated = effect.with_attribution(
            task_name="MyTask",
            provider_id="provider:claude:test",
            context_id="workspace:/repo:abc",
            binding_name="workspace",
        )

        assert updated.task_name == "MyTask"
        assert updated.provider_id == "provider:claude:test"
        assert updated.context_id == "workspace:/repo:abc"
        assert updated.binding_name == "workspace"

    def test_effect_with_attribution_preserves_existing_binding_name(self):
        """with_attribution() with None should preserve existing binding_name."""
        effect = FileRead(path="foo.py", binding_name="original")
        updated = effect.with_attribution(task_name="Task")

        assert updated.binding_name == "original"

    def test_effect_binding_name_serializes(self):
        """binding_name should be included in serialization."""
        effect = FileRead(path="foo.py", binding_name="workspace")
        data = effect.model_dump()

        assert "binding_name" in data
        assert data["binding_name"] == "workspace"

    def test_effect_binding_name_survives_roundtrip(self):
        """binding_name should survive model_dump/model_validate roundtrip."""
        effect = FileCreate(
            path="test.py",
            content_hash="hash123",
            binding_name="workspace",
            context_id="workspace:/path:abc123",
        )

        # Serialize and deserialize via Pydantic
        data = effect.model_dump()
        restored = FileCreate.model_validate(data)

        assert restored.binding_name == "workspace"
        assert restored.context_id == "workspace:/path:abc123"
        assert restored.path == "test.py"


# =============================================================================
# Scope Dual-Mode Routing Tests
# =============================================================================


class TestScopeBindingNameRouting:
    """Test binding_name routing precedence in Scope.apply_effect()."""

    def test_binding_name_routes_to_correct_binding(self):
        """Effects with binding_name should route to matching binding."""
        with Scope() as scope:
            session = SessionState(session_id=None)
            scope.bind("session", session)

            # Effect with binding_name routes by name
            effect = SessionCreated(
                session_id="sess_123",
                binding_name="session",
            )

            # Apply via scope's internal mechanism
            new_scope = scope._scope.apply_effect(effect)
            updated_session = new_scope.get_context("session")

            assert updated_session.session_id == "sess_123"

    def test_binding_name_takes_precedence_over_context_id(self):
        """binding_name should take precedence when both are set."""
        with Scope() as scope:
            session = SessionState(session_id=None)
            scope.bind("session", session)

            # Effect with BOTH binding_name and wrong context_id
            # binding_name should win
            effect = SessionCreated(
                session_id="sess_123",
                binding_name="session",
                context_id="session:wrong_id",  # This would NOT match
            )

            new_scope = scope._scope.apply_effect(effect)
            updated_session = new_scope.get_context("session")

            # Should still apply because binding_name matched
            assert updated_session.session_id == "sess_123"

    def test_context_id_routing_still_works(self):
        """Effects without binding_name should still route by context_id."""
        with Scope() as scope:
            # Use a context with stable context_id
            # Note: base_commit must be a valid 40-char SHA
            workspace = WorkspaceRef(
                path="/repo",
                base_commit="a" * 40,
                frozen_context_id="workspace:/repo:aaaaaaaa",
            )
            scope.bind("workspace", workspace)

            effect = WorkspacePatchCaptured(
                context_id="workspace:/repo:aaaaaaaa",  # Matches frozen_context_id
                patch=DiffPatch(patch="diff content", files_changed=("file.py",)),
                files_changed=("file.py",),
            )

            new_scope = scope._scope.apply_effect(effect)
            updated_workspace = new_scope.get_context("workspace")

            # Should apply via context_id routing
            assert len(updated_workspace.pending_patches) == 1

    def test_no_routing_when_neither_set(self):
        """Effects with neither binding_name nor context_id should not route."""
        with Scope() as scope:
            session = SessionState(session_id="existing")
            scope.bind("session", session)

            # Effect with neither routing field
            effect = SessionCreated(
                session_id="new_id",
                binding_name=None,
                context_id=None,
            )

            new_scope = scope._scope.apply_effect(effect)
            unchanged_session = new_scope.get_context("session")

            # Should NOT apply - no routing match
            assert unchanged_session.session_id == "existing"


# =============================================================================
# SessionState Multi-Effect Bug Fix Tests
# =============================================================================


class TestSessionStateMultiEffect:
    """Test that SessionState correctly applies multiple effects.

    This is the critical bug fix: SessionState's context_id is derived from
    session_id, which changes during effect application. Without binding_name
    routing, the second effect would fail to apply because context_id changed.
    """

    def test_session_create_then_fork_both_apply(self):
        """Both SessionCreated and SessionForked should apply in sequence."""
        with Scope() as scope:
            session = SessionState(session_id=None)
            scope.bind("session", session)

            # Initial context_id is "session:new"
            assert session.context_id == "session:new"

            # Two effects in sequence (simulating what lifecycle produces)
            effects = [
                SessionCreated(
                    session_id="sess_1",
                    binding_name="session",
                    context_id="session:new",  # Original context_id
                ),
                SessionForked(
                    parent_session_id="sess_1",
                    new_session_id="sess_2",
                    binding_name="session",
                    context_id="session:new",  # Same original context_id
                ),
            ]

            # Apply both effects
            current_scope = scope._scope
            for effect in effects:
                current_scope = current_scope.apply_effect(effect)

            final_session = current_scope.get_context("session")

            # BOTH effects should have applied
            assert final_session.session_id == "sess_2"

    def test_session_multiple_forks_all_apply(self):
        """Multiple sequential forks should all apply correctly."""
        with Scope() as scope:
            session = SessionState(session_id="sess_0")
            scope.bind("session", session)

            # Chain of forks
            effects = [
                SessionForked(
                    parent_session_id="sess_0",
                    new_session_id="sess_1",
                    binding_name="session",
                ),
                SessionForked(
                    parent_session_id="sess_1",
                    new_session_id="sess_2",
                    binding_name="session",
                ),
                SessionForked(
                    parent_session_id="sess_2",
                    new_session_id="sess_3",
                    binding_name="session",
                ),
            ]

            current_scope = scope._scope
            for effect in effects:
                current_scope = current_scope.apply_effect(effect)

            final_session = current_scope.get_context("session")

            # All forks should have applied
            assert final_session.session_id == "sess_3"

    def test_session_apply_effect_no_context_id_filter(self):
        """SessionState.apply_effect() should not filter by context_id."""
        session = SessionState(session_id=None)

        # Effect with mismatched context_id but correct binding_name
        # SessionState should apply it anyway (no context_id filtering)
        effect = SessionCreated(
            session_id="new_session",
            context_id="session:wrong",  # Doesn't match "session:new"
        )

        # SessionState.apply_effect() doesn't filter by context_id
        # It trusts that lifecycle routed correctly
        new_session = session.apply_effect(effect)

        assert new_session.session_id == "new_session"


# =============================================================================
# Stream Query by binding_name Tests
# =============================================================================


class TestStreamBindingNameQuery:
    """Test Stream query methods with binding_name parameter."""

    def test_query_by_binding_name(self):
        """query() should filter by binding_name."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name="workspace"))
        stream = stream.append(FileRead(path="b.py", binding_name="session"))
        stream = stream.append(FileRead(path="c.py", binding_name="workspace"))

        results = list(stream.query(binding_name="workspace"))

        assert len(results) == 2
        assert all(r.effect.binding_name == "workspace" for r in results)

    def test_query_by_binding_name_and_type(self):
        """query() should filter by both binding_name and effect type."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name="workspace"))
        stream = stream.append(FileCreate(path="b.py", binding_name="workspace"))
        stream = stream.append(FileRead(path="c.py", binding_name="session"))

        results = list(stream.query(FileRead, binding_name="workspace"))

        assert len(results) == 1
        assert results[0].effect.path == "a.py"

    def test_query_binding_name_no_match(self):
        """query() should return empty when no binding_name match."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name="workspace"))

        results = list(stream.query(binding_name="nonexistent"))

        assert len(results) == 0

    def test_query_ignores_none_binding_name(self):
        """query() should not match effects with None binding_name."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name=None))
        stream = stream.append(FileRead(path="b.py", binding_name="workspace"))

        results = list(stream.query(binding_name="workspace"))

        assert len(results) == 1
        assert results[0].effect.path == "b.py"

    def test_first_with_binding_name(self):
        """first() should accept binding_name parameter."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name="session"))
        stream = stream.append(FileRead(path="b.py", binding_name="workspace"))
        stream = stream.append(FileRead(path="c.py", binding_name="workspace"))

        result = stream.first(binding_name="workspace")

        assert result is not None
        assert result.effect.path == "b.py"

    def test_last_with_binding_name(self):
        """last() should accept binding_name parameter."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name="workspace"))
        stream = stream.append(FileRead(path="b.py", binding_name="session"))
        stream = stream.append(FileRead(path="c.py", binding_name="workspace"))

        result = stream.last(binding_name="workspace")

        assert result is not None
        assert result.effect.path == "c.py"

    def test_count_with_binding_name(self):
        """count() should accept binding_name parameter."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name="workspace"))
        stream = stream.append(FileRead(path="b.py", binding_name="session"))
        stream = stream.append(FileRead(path="c.py", binding_name="workspace"))

        count = stream.count(binding_name="workspace")

        assert count == 2


# =============================================================================
# Stream by_binding() Method Tests
# =============================================================================


class TestStreamByBinding:
    """Test Stream.by_binding() filter method."""

    def test_by_binding_filters_correctly(self):
        """by_binding() should return only effects with matching binding_name."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name="workspace"))
        stream = stream.append(FileRead(path="b.py", binding_name="session"))
        stream = stream.append(FileRead(path="c.py", binding_name="workspace"))

        filtered = stream.by_binding("workspace")

        assert len(filtered) == 2
        assert all(layer.effect.binding_name == "workspace" for layer in filtered)

    def test_by_binding_returns_new_stream(self):
        """by_binding() should return a new Stream instance."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name="workspace"))

        filtered = stream.by_binding("workspace")

        assert filtered is not stream
        assert isinstance(filtered, Stream)

    def test_by_binding_no_match_returns_empty(self):
        """by_binding() should return empty stream if no matches."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name="workspace"))

        filtered = stream.by_binding("nonexistent")

        assert len(filtered) == 0

    def test_by_binding_ignores_none(self):
        """by_binding() should not match effects with None binding_name."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name=None))
        stream = stream.append(FileRead(path="b.py", binding_name="workspace"))

        filtered = stream.by_binding("workspace")

        assert len(filtered) == 1
        assert filtered.layers[0].effect.path == "b.py"

    def test_by_binding_preserves_sequence(self):
        """by_binding() should preserve original sequence numbers."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name="ws"))  # seq 0
        stream = stream.append(FileRead(path="b.py", binding_name="other"))  # seq 1
        stream = stream.append(FileRead(path="c.py", binding_name="ws"))  # seq 2

        filtered = stream.by_binding("ws")

        assert len(filtered) == 2
        assert filtered.layers[0].sequence == 0
        assert filtered.layers[1].sequence == 2

    def test_by_binding_chainable_with_query(self):
        """by_binding() result should be queryable."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", binding_name="workspace"))
        stream = stream.append(FileCreate(path="b.py", binding_name="workspace"))
        stream = stream.append(FileRead(path="c.py", binding_name="session"))

        # Chain: filter by binding, then query by type
        filtered = stream.by_binding("workspace")
        creates = list(filtered.query(FileCreate))

        assert len(creates) == 1
        assert creates[0].effect.path == "b.py"


# =============================================================================
# Combined context_id and binding_name Tests
# =============================================================================


class TestDualRoutingFields:
    """Test effects with both context_id and binding_name."""

    def test_effect_can_have_both_fields(self):
        """Effects can have both context_id and binding_name."""
        effect = FileRead(
            path="foo.py",
            context_id="workspace:/repo:abc",
            binding_name="workspace",
        )

        assert effect.context_id == "workspace:/repo:abc"
        assert effect.binding_name == "workspace"

    def test_stream_query_by_both(self):
        """Stream can query by both context_id and binding_name."""
        stream = Stream()
        stream = stream.append(
            FileRead(
                path="a.py",
                context_id="ctx1",
                binding_name="ws1",
            )
        )
        stream = stream.append(
            FileRead(
                path="b.py",
                context_id="ctx1",
                binding_name="ws2",
            )
        )
        stream = stream.append(
            FileRead(
                path="c.py",
                context_id="ctx2",
                binding_name="ws1",
            )
        )

        # Query by context_id
        by_ctx = list(stream.query(context_id="ctx1"))
        assert len(by_ctx) == 2

        # Query by binding_name
        by_binding = list(stream.query(binding_name="ws1"))
        assert len(by_binding) == 2

        # Query by both (AND)
        by_both = list(stream.query(context_id="ctx1", binding_name="ws1"))
        assert len(by_both) == 1
        assert by_both[0].effect.path == "a.py"

    def test_by_context_and_by_binding_independent(self):
        """by_context() and by_binding() filter independently."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="ctx1", binding_name="ws1"))
        stream = stream.append(FileRead(path="b.py", context_id="ctx1", binding_name="ws2"))

        by_ctx = stream.by_context("ctx1")
        by_bind = stream.by_binding("ws1")

        assert len(by_ctx) == 2  # Both have ctx1
        assert len(by_bind) == 1  # Only one has ws1


# =============================================================================
# Real-World Scenario Tests
# =============================================================================


class TestRealWorldScenarios:
    """Test binding_name routing in realistic scenarios."""

    def test_lifecycle_attribution_pattern(self):
        """Simulate how lifecycle attributes effects with both fields."""
        # This is what lifecycle.py does in the extract phase
        raw_effect = SessionCreated(session_id="sess_123")

        # Lifecycle attributes with both for routing and audit
        attributed = raw_effect.with_attribution(
            task_name="MyTask",
            provider_id="provider:claude:coder",
            context_id="session:new",  # For audit trail
            binding_name="session",  # For routing
        )

        assert attributed.task_name == "MyTask"
        assert attributed.context_id == "session:new"
        assert attributed.binding_name == "session"

    def test_cached_effect_replay_scenario(self):
        """Simulate cache hit replaying effects with binding_name."""
        with Scope() as scope:
            session = SessionState(session_id=None)
            scope.bind("session", session)

            # Cached effects from previous execution
            # These have binding_name set for reliable replay
            cached_effects = [
                SessionCreated(
                    session_id="cached_sess_1",
                    binding_name="session",
                    context_id="session:new",  # Original context_id from cache
                ),
                SessionForked(
                    parent_session_id="cached_sess_1",
                    new_session_id="cached_sess_2",
                    binding_name="session",
                    context_id="session:new",  # Same original context_id
                ),
            ]

            # Replay cached effects
            current_scope = scope._scope
            for effect in cached_effects:
                current_scope = current_scope.apply_effect(effect)

            final_session = current_scope.get_context("session")

            # Both effects replayed correctly via binding_name routing
            assert final_session.session_id == "cached_sess_2"

    def test_multi_context_effect_isolation(self):
        """Effects route to correct context even with multiple bindings."""
        with Scope() as scope:
            # Use different session_ids to avoid context_id collision
            session1 = SessionState(session_id="sess_primary")
            session2 = SessionState(session_id="sess_secondary")
            scope.bind("primary", session1)
            scope.bind("secondary", session2)

            # Effect targets primary only via binding_name
            effect = SessionForked(
                parent_session_id="sess_primary",
                new_session_id="sess_primary_forked",
                binding_name="primary",
            )

            new_scope = scope._scope.apply_effect(effect)

            primary = new_scope.get_context("primary")
            secondary = new_scope.get_context("secondary")

            # Only primary should be updated
            assert primary.session_id == "sess_primary_forked"
            assert secondary.session_id == "sess_secondary"  # Unchanged
