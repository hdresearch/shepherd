"""Tests for stream query methods (Phase 1: Effect Attribution).

These tests validate the stream query methods for context filtering:
1. by_context() - Filter by exact context_id
2. by_context_type() - Filter by context_id prefix
3. contexts() - Get all unique context IDs
4. Stream display shows context breakdown
"""

from shepherd_contexts import BashCommand
from shepherd_core.effects import (
    FileCreate,
    FilePatch,
    FileRead,
    TaskCompleted,
    TaskStarted,
)
from shepherd_core.scope import Stream


class TestByContext:
    """Test Stream.by_context() method."""

    def test_by_context_filters_exactly(self):
        """by_context should return only effects with matching context_id."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="workspace:/repo1:abc"))
        stream = stream.append(FileRead(path="b.py", context_id="workspace:/repo2:def"))
        stream = stream.append(FilePatch(path="c.py", context_id="workspace:/repo1:abc"))

        filtered = stream.by_context("workspace:/repo1:abc")

        assert len(filtered) == 2
        assert all(layer.effect.context_id == "workspace:/repo1:abc" for layer in filtered)

    def test_by_context_returns_new_stream(self):
        """by_context should return a new Stream, not modify original."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="ctx1"))

        filtered = stream.by_context("ctx1")

        assert filtered is not stream
        assert isinstance(filtered, Stream)

    def test_by_context_no_match_returns_empty(self):
        """by_context should return empty stream if no matches."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="ctx1"))

        filtered = stream.by_context("ctx2")

        assert len(filtered) == 0

    def test_by_context_ignores_none_context(self):
        """by_context should not match effects with None context_id."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id=None))
        stream = stream.append(FileRead(path="b.py", context_id="ctx1"))

        filtered = stream.by_context("ctx1")

        assert len(filtered) == 1
        assert filtered.layers[0].effect.path == "b.py"


class TestByContextType:
    """Test Stream.by_context_type() method."""

    def test_by_context_type_filters_by_prefix(self):
        """by_context_type should return effects with matching prefix."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="workspace:/repo1:abc"))
        stream = stream.append(FileRead(path="b.py", context_id="workspace:/repo2:def"))
        stream = stream.append(TaskStarted(task_name="Test", context_id="session:sess123"))

        filtered = stream.by_context_type("workspace:")

        assert len(filtered) == 2
        assert all(layer.effect.context_id.startswith("workspace:") for layer in filtered)

    def test_by_context_type_session_prefix(self):
        """by_context_type should work for session contexts."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="T1", context_id="session:sess1"))
        stream = stream.append(TaskStarted(task_name="T2", context_id="session:sess2"))
        stream = stream.append(FileRead(path="a.py", context_id="workspace:/repo:abc"))

        filtered = stream.by_context_type("session:")

        assert len(filtered) == 2
        assert all(layer.effect.context_id.startswith("session:") for layer in filtered)

    def test_by_context_type_no_match_returns_empty(self):
        """by_context_type should return empty stream if no matches."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="workspace:/repo:abc"))

        filtered = stream.by_context_type("session:")

        assert len(filtered) == 0

    def test_by_context_type_ignores_none_context(self):
        """by_context_type should not match effects with None context_id."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id=None))
        stream = stream.append(FileRead(path="b.py", context_id="workspace:/repo:abc"))

        filtered = stream.by_context_type("workspace:")

        assert len(filtered) == 1


class TestContexts:
    """Test Stream.contexts() method."""

    def test_contexts_returns_unique_ids(self):
        """contexts() should return set of unique context IDs."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="workspace:/repo1:abc"))
        stream = stream.append(FileRead(path="b.py", context_id="workspace:/repo1:abc"))
        stream = stream.append(FileRead(path="c.py", context_id="workspace:/repo2:def"))

        contexts = stream.contexts()

        assert isinstance(contexts, set)
        assert len(contexts) == 2
        assert "workspace:/repo1:abc" in contexts
        assert "workspace:/repo2:def" in contexts

    def test_contexts_excludes_none(self):
        """contexts() should exclude None context IDs."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id=None))
        stream = stream.append(FileRead(path="b.py", context_id="workspace:/repo:abc"))

        contexts = stream.contexts()

        assert len(contexts) == 1
        assert None not in contexts
        assert "workspace:/repo:abc" in contexts

    def test_contexts_empty_stream(self):
        """contexts() should return empty set for empty stream."""
        stream = Stream()
        contexts = stream.contexts()

        assert isinstance(contexts, set)
        assert len(contexts) == 0

    def test_contexts_all_none(self):
        """contexts() should return empty set if all effects have None context."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Test"))
        stream = stream.append(TaskCompleted(task_name="Test"))

        contexts = stream.contexts()

        assert len(contexts) == 0


class TestStreamDisplay:
    """Test Stream.__str__() with context breakdown."""

    def test_single_context_no_breakdown(self):
        """Single context should not show breakdown in display."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="workspace:/repo:abc"))
        stream = stream.append(FilePatch(path="b.py", context_id="workspace:/repo:abc"))

        display = str(stream)

        # Should not have "Contexts:" section for single context
        assert "Contexts:" not in display

    def test_multiple_contexts_shows_breakdown(self):
        """Multiple contexts should show breakdown in display."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="workspace:/repo1:abc"))
        stream = stream.append(FileRead(path="b.py", context_id="workspace:/repo2:def"))

        display = str(stream)

        assert "Contexts:" in display
        assert "workspace:/repo1:abc" in display
        assert "workspace:/repo2:def" in display

    def test_mixed_none_and_context_shows_breakdown(self):
        """Mix of None and context_id should show breakdown."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Test"))  # None context
        stream = stream.append(FileRead(path="a.py", context_id="workspace:/repo:abc"))

        display = str(stream)

        assert "Contexts:" in display
        assert "(no context)" in display
        assert "workspace:/repo:abc" in display


class TestFilterChaining:
    """Test chaining stream filter operations."""

    def test_filter_then_query(self):
        """Should be able to query after filtering by context."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="workspace:/repo:abc"))
        stream = stream.append(FilePatch(path="b.py", context_id="workspace:/repo:abc"))
        stream = stream.append(FileRead(path="c.py", context_id="workspace:/repo:def"))

        # Filter by context, then query by type
        filtered = stream.by_context("workspace:/repo:abc")
        patches = list(filtered.query(FilePatch))

        assert len(patches) == 1
        assert patches[0].effect.path == "b.py"

    def test_filter_preserves_sequence(self):
        """Filtered stream should preserve original sequence numbers."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="ctx1"))  # seq 0
        stream = stream.append(FileRead(path="b.py", context_id="ctx2"))  # seq 1
        stream = stream.append(FileRead(path="c.py", context_id="ctx1"))  # seq 2

        filtered = stream.by_context("ctx1")

        assert len(filtered) == 2
        assert filtered.layers[0].sequence == 0
        assert filtered.layers[1].sequence == 2


class TestContextIdWithMultipleTypes:
    """Test context_id across different effect types."""

    def test_mixed_effect_types_same_context(self):
        """Different effect types with same context_id should filter together."""
        stream = Stream()
        ctx = "workspace:/repo:abc"

        stream = stream.append(FileRead(path="a.py", context_id=ctx))
        stream = stream.append(FilePatch(path="b.py", old_content="x", new_content="y", context_id=ctx))
        stream = stream.append(FileCreate(path="c.py", content="...", context_id=ctx))
        stream = stream.append(BashCommand(command="ls", output="files", context_id=ctx))

        filtered = stream.by_context(ctx)

        assert len(filtered) == 4
        effect_types = {type(layer.effect).__name__ for layer in filtered}
        assert effect_types == {"FileRead", "FilePatch", "FileCreate", "BashCommand"}

    def test_same_effect_type_different_contexts(self):
        """Same effect type with different contexts should filter separately."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="workspace:/repo1:abc"))
        stream = stream.append(FileRead(path="b.py", context_id="workspace:/repo2:def"))
        stream = stream.append(FileRead(path="c.py", context_id="workspace:/repo1:abc"))

        repo1_effects = stream.by_context("workspace:/repo1:abc")
        repo2_effects = stream.by_context("workspace:/repo2:def")

        assert len(repo1_effects) == 2
        assert len(repo2_effects) == 1


class TestRealWorldScenarios:
    """Test context_id in realistic scenarios."""

    def test_multi_workspace_task(self):
        """Simulate task that touches multiple workspaces."""
        stream = Stream()
        ws1 = "workspace:/source:abc"
        ws2 = "workspace:/target:def"

        # Read from source
        stream = stream.append(FileRead(path="src/lib.py", context_id=ws1))

        # Write to target
        stream = stream.append(FileCreate(path="dst/lib.py", content="...", context_id=ws2))

        # Verify separation
        source_ops = stream.by_context(ws1)
        target_ops = stream.by_context(ws2)

        assert len(source_ops) == 1
        assert len(target_ops) == 1
        assert isinstance(source_ops.layers[0].effect, FileRead)
        assert isinstance(target_ops.layers[0].effect, FileCreate)

    def test_context_based_rollback_selection(self):
        """Show how context_id enables targeted rollback."""
        stream = Stream()

        # Effects on workspace 1
        stream = stream.append(FileCreate(path="a.py", content="a", context_id="ws:1"))
        stream = stream.append(FilePatch(path="b.py", old_content="x", new_content="y", context_id="ws:1"))

        # Effects on workspace 2
        stream = stream.append(FileCreate(path="c.py", content="c", context_id="ws:2"))

        # To rollback only workspace 1:
        ws1_effects = stream.by_context("ws:1")

        # All effects from ws:1 can now be reversed
        assert len(ws1_effects) == 2
        for layer in ws1_effects:
            # Each should be reversible
            effect = layer.effect
            if hasattr(effect, "reverse"):
                reverse = effect.reverse()
                assert reverse is not None


class TestSourceContextDenormalization:
    """Test source_context field on EffectLayer for fast filtering."""

    def test_source_context_populated_on_append(self):
        """source_context should be populated from effect.context_id on append."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="workspace:/repo:abc123"))

        layer = stream.layers[0]
        assert layer.source_context == "workspace:/repo:abc123"
        assert layer.source_context == layer.effect.context_id

    def test_source_context_none_when_no_context_id(self):
        """source_context should be None when effect has no context_id."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Test"))

        layer = stream.layers[0]
        assert layer.source_context is None
        assert layer.effect.context_id is None

    def test_source_context_used_for_filtering(self):
        """by_context should use source_context for filtering."""
        stream = Stream()
        ctx_id = "workspace:/repo:abc123"

        stream = stream.append(FileRead(path="a.py", context_id=ctx_id))
        stream = stream.append(FileRead(path="b.py", context_id="other:ctx"))

        # Verify filtering works via source_context
        filtered = stream.by_context(ctx_id)
        assert len(filtered) == 1
        assert filtered.layers[0].source_context == ctx_id

    def test_source_context_survives_serialization(self):
        """source_context should survive JSON roundtrip."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="workspace:/repo:abc123"))

        json_str = stream.to_json()
        restored = Stream.from_json(json_str)

        assert restored.layers[0].source_context == "workspace:/repo:abc123"

    def test_contexts_method_uses_source_context(self):
        """contexts() should use source_context for fast access."""
        stream = Stream()
        stream = stream.append(FileRead(path="a.py", context_id="ctx:1"))
        stream = stream.append(FileRead(path="b.py", context_id="ctx:2"))
        stream = stream.append(TaskStarted(task_name="Test"))  # No context

        contexts = stream.contexts()

        assert contexts == {"ctx:1", "ctx:2"}
        assert None not in contexts
