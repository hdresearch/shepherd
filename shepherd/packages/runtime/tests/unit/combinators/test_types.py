"""Tests for combinator types: Rejected, Budget, MergeStrategy."""

from shepherd_core.effects import Effect, FileCreate, FilePatch
from shepherd_core.scope.stream import Stream
from shepherd_runtime.combinators.types import (
    Budget,
    DisjointMerge,
    LastWriteWins,
    Rejected,
)


class TestRejected:
    """Tests for Rejected result container."""

    def test_rejected_stores_value_and_effects(self):
        """Rejected stores the rejected value and effects."""
        stream = Stream()
        rejected = Rejected(value="test_value", effects=stream, reason="test reason")

        assert rejected.value == "test_value"
        assert rejected.effects is stream
        assert rejected.reason == "test reason"

    def test_rejected_map_transforms_value(self):
        """map() transforms the rejected value."""
        stream = Stream()
        rejected = Rejected(value=5, effects=stream, reason="too small")

        mapped = rejected.map(lambda x: x * 2)

        assert mapped.value == 10
        assert mapped.effects is stream
        assert mapped.reason == "too small"

    def test_rejected_unwrap_or_returns_default(self):
        """unwrap_or() returns the default value."""
        stream = Stream()
        rejected = Rejected(value="bad", effects=stream)

        result = rejected.unwrap_or("good")

        assert result == "good"

    def test_rejected_or_else_returns_default(self):
        """or_else() is an alias for unwrap_or()."""
        stream = Stream()
        rejected = Rejected(value="bad", effects=stream)

        result = rejected.or_else("good")

        assert result == "good"

    def test_rejected_repr_without_reason(self):
        """repr() works without reason."""
        stream = Stream()
        rejected = Rejected(value="test", effects=stream)

        assert "Rejected" in repr(rejected)
        assert "test" in repr(rejected)

    def test_rejected_repr_with_reason(self):
        """repr() includes reason when present."""
        stream = Stream()
        rejected = Rejected(value="test", effects=stream, reason="failed check")

        assert "failed check" in repr(rejected)


class TestBudget:
    """Tests for Budget resource limits."""

    def test_budget_allows_within_limits(self):
        """Budget passes when within all limits."""
        budget = Budget(max_effects=10, max_duration_seconds=60)
        stream = Stream()
        for i in range(5):
            stream = stream.append(Effect(effect_type=f"effect_{i}"))

        within, reason = budget.check(stream, duration=30)

        assert within is True
        assert reason is None

    def test_budget_rejects_excess_effects(self):
        """Budget rejects when effect count exceeded."""
        budget = Budget(max_effects=3)
        stream = Stream()
        for i in range(5):
            stream = stream.append(Effect(effect_type=f"effect_{i}"))

        within, reason = budget.check(stream)

        assert within is False
        assert "Effect limit exceeded" in reason
        assert "5 > 3" in reason

    def test_budget_rejects_excess_duration(self):
        """Budget rejects when duration exceeded."""
        budget = Budget(max_duration_seconds=10)
        stream = Stream()

        within, reason = budget.check(stream, duration=15)

        assert within is False
        assert "Duration limit exceeded" in reason

    def test_budget_rejects_excess_files(self):
        """Budget rejects when file count exceeded."""
        budget = Budget(max_files=2)
        stream = Stream()
        stream = stream.append(FileCreate(path="/a.py", content=""))
        stream = stream.append(FileCreate(path="/b.py", content=""))
        stream = stream.append(FileCreate(path="/c.py", content=""))

        within, reason = budget.check(stream)

        assert within is False
        assert "File limit exceeded" in reason

    def test_budget_allows_duplicate_file_effects(self):
        """Budget counts unique files, not total file effects."""
        budget = Budget(max_files=2)
        stream = Stream()
        # Multiple effects on same file should count as 1
        stream = stream.append(FileCreate(path="/a.py", content="v1"))
        stream = stream.append(FilePatch(path="/a.py", patch=None))
        stream = stream.append(FileCreate(path="/b.py", content=""))

        within, _reason = budget.check(stream)

        assert within is True

    def test_budget_none_limits_are_ignored(self):
        """None limits are not checked."""
        budget = Budget()  # All None
        stream = Stream()
        for i in range(100):
            stream = stream.append(Effect(effect_type=f"effect_{i}"))

        within, reason = budget.check(stream, duration=1000)

        assert within is True
        assert reason is None


class TestDisjointMerge:
    """Tests for DisjointMerge strategy."""

    def test_disjoint_merge_allows_non_overlapping(self):
        """DisjointMerge allows streams with no overlapping files."""
        strategy = DisjointMerge()
        stream1 = Stream().append(FileCreate(path="/a.py", content=""))
        stream2 = Stream().append(FileCreate(path="/b.py", content=""))

        can_merge, reason = strategy.can_merge([stream1, stream2])

        assert can_merge is True
        assert reason is None

    def test_disjoint_merge_rejects_overlapping_files(self):
        """DisjointMerge rejects streams that modify the same file."""
        strategy = DisjointMerge()
        stream1 = Stream().append(FileCreate(path="/shared.py", content="v1"))
        stream2 = Stream().append(FileCreate(path="/shared.py", content="v2"))

        can_merge, reason = strategy.can_merge([stream1, stream2])

        assert can_merge is False
        assert "shared.py" in reason

    def test_disjoint_merge_merges_by_timestamp(self):
        """DisjointMerge merges streams sorted by timestamp."""
        strategy = DisjointMerge()

        # Create effects with explicit timestamps
        effect1 = FileCreate(path="/a.py", content="", timestamp=100.0)
        effect2 = FileCreate(path="/b.py", content="", timestamp=50.0)

        stream1 = Stream().append(effect1)
        stream2 = Stream().append(effect2)

        merged = strategy.merge([stream1, stream2])

        assert len(merged) == 2
        # Earlier timestamp comes first
        assert merged[0].effect.path == "/b.py"
        assert merged[1].effect.path == "/a.py"

    def test_disjoint_merge_handles_empty_streams(self):
        """DisjointMerge handles empty streams."""
        strategy = DisjointMerge()
        stream1 = Stream()
        stream2 = Stream()

        can_merge, _reason = strategy.can_merge([stream1, stream2])
        assert can_merge is True

        merged = strategy.merge([stream1, stream2])
        assert len(merged) == 0


class TestLastWriteWins:
    """Tests for LastWriteWins strategy."""

    def test_last_write_wins_always_allows_merge(self):
        """LastWriteWins always allows merging."""
        strategy = LastWriteWins()
        stream1 = Stream().append(FileCreate(path="/shared.py", content="v1"))
        stream2 = Stream().append(FileCreate(path="/shared.py", content="v2"))

        can_merge, reason = strategy.can_merge([stream1, stream2])

        assert can_merge is True
        assert reason is None

    def test_last_write_wins_orders_by_timestamp(self):
        """LastWriteWins orders effects by timestamp."""
        strategy = LastWriteWins()

        effect1 = Effect(effect_type="first", timestamp=100.0)
        effect2 = Effect(effect_type="second", timestamp=200.0)
        effect3 = Effect(effect_type="third", timestamp=150.0)

        stream1 = Stream().append(effect1)
        stream2 = Stream().append(effect2)
        stream3 = Stream().append(effect3)

        merged = strategy.merge([stream1, stream2, stream3])

        assert len(merged) == 3
        assert merged[0].effect.effect_type == "first"
        assert merged[1].effect.effect_type == "third"
        assert merged[2].effect.effect_type == "second"
