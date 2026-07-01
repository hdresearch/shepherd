"""Tests for the fold functions (Layer 0).

The fold is the core invariant that defines Shepherd's semantics:
    state(t) = fold(apply, effects[0:t], initial)
"""

from shepherd_core.foundation import fold, fold_until, fold_with_index, scan


class TestFold:
    """Tests for the basic fold function."""

    def test_fold_empty_effects(self):
        """Fold with no effects returns initial state."""

        def apply(state, effect):
            return state + effect

        result = fold(apply, [], 0)
        assert result == 0

    def test_fold_basic(self):
        """Fold applies effects in order."""

        def apply(state, effect):
            return state + effect

        result = fold(apply, [1, 2, 3], 0)
        assert result == 6

    def test_fold_with_list(self):
        """Fold with list accumulation."""

        def apply(state, effect):
            return [*state, effect]

        result = fold(apply, ["a", "b", "c"], [])
        assert result == ["a", "b", "c"]

    def test_fold_with_dict(self):
        """Fold with dict updates (simulating state)."""

        def apply(state, effect):
            new_state = dict(state)
            new_state[effect["key"]] = effect["value"]
            return new_state

        effects = [
            {"key": "name", "value": "Alice"},
            {"key": "age", "value": 30},
            {"key": "name", "value": "Bob"},  # Override
        ]
        result = fold(apply, effects, {})
        assert result == {"name": "Bob", "age": 30}

    def test_fold_preserves_order(self):
        """Fold processes effects in order."""
        order = []

        def apply(state, effect):
            order.append(effect)
            return state + 1

        fold(apply, [1, 2, 3], 0)
        assert order == [1, 2, 3]

    def test_fold_with_generator(self):
        """Fold works with generators."""

        def effects_gen():
            yield 1
            yield 2
            yield 3

        result = fold(lambda s, e: s + e, effects_gen(), 0)
        assert result == 6


class TestFoldWithIndex:
    """Tests for fold_with_index."""

    def test_fold_with_index_basic(self):
        """fold_with_index provides correct indices."""
        indices = []

        def apply(state, effect, idx):
            indices.append(idx)
            return state + effect

        result = fold_with_index(apply, [10, 20, 30], 0)
        assert result == 60
        assert indices == [0, 1, 2]

    def test_fold_with_index_uses_index(self):
        """Index can be used in computation."""

        def apply(state, effect, idx):
            return state + effect * idx

        result = fold_with_index(apply, [10, 10, 10], 0)
        # 10*0 + 10*1 + 10*2 = 0 + 10 + 20 = 30
        assert result == 30

    def test_fold_with_index_empty(self):
        """fold_with_index with empty effects."""

        def apply(state, effect, idx):
            return state + effect

        result = fold_with_index(apply, [], 0)
        assert result == 0


class TestScan:
    """Tests for the scan function (yields intermediate states)."""

    def test_scan_yields_initial(self):
        """Scan yields initial state first."""

        def apply(state, effect):
            return state + effect

        states = list(scan(apply, [1, 2, 3], 0))
        assert states[0] == 0

    def test_scan_yields_intermediates(self):
        """Scan yields all intermediate states."""

        def apply(state, effect):
            return state + effect

        states = list(scan(apply, [1, 2, 3], 0))
        assert states == [0, 1, 3, 6]

    def test_scan_empty_effects(self):
        """Scan with empty effects yields only initial."""

        def apply(state, effect):
            return state + effect

        states = list(scan(apply, [], 0))
        assert states == [0]

    def test_scan_single_effect(self):
        """Scan with single effect yields two states."""

        def apply(state, effect):
            return state + effect

        states = list(scan(apply, [5], 0))
        assert states == [0, 5]

    def test_scan_is_lazy(self):
        """Scan is lazy (returns iterator)."""
        apply_count = 0

        def apply(state, effect):
            nonlocal apply_count
            apply_count += 1
            return state + effect

        scanner = scan(apply, [1, 2, 3], 0)
        # Iterator created but no processing yet (only initial)
        assert apply_count == 0

        next(scanner)  # yields initial
        assert apply_count == 0

        next(scanner)  # processes first effect
        assert apply_count == 1


class TestFoldUntil:
    """Tests for fold_until (early termination)."""

    def test_fold_until_stops_early(self):
        """fold_until stops when predicate is satisfied."""

        def apply(state, effect):
            return state + effect

        def predicate(state):
            return state >= 5

        state, count = fold_until(apply, [1, 2, 3, 4, 5], 0, predicate)
        assert state == 6  # 1 + 2 + 3 = 6 >= 5
        assert count == 3

    def test_fold_until_processes_all_if_never_satisfied(self):
        """fold_until processes all effects if predicate never satisfied."""

        def apply(state, effect):
            return state + effect

        def predicate(state):
            return state > 100  # Never true

        state, count = fold_until(apply, [1, 2, 3], 0, predicate)
        assert state == 6
        assert count == 3

    def test_fold_until_empty_effects(self):
        """fold_until with empty effects returns initial."""

        def apply(state, effect):
            return state + effect

        state, count = fold_until(apply, [], 0, lambda s: True)
        assert state == 0
        assert count == 0

    def test_fold_until_first_effect_satisfies(self):
        """fold_until stops immediately if first effect satisfies."""

        def apply(state, effect):
            return state + effect

        state, count = fold_until(apply, [10, 20, 30], 0, lambda s: s >= 10)
        assert state == 10
        assert count == 1

    def test_fold_until_with_error_state(self):
        """Example: find state when error occurred."""

        def apply(state, effect):
            new_state = dict(state)
            new_state["effects"].append(effect)
            if effect.get("error"):
                new_state["has_error"] = True
            return new_state

        effects = [
            {"type": "start"},
            {"type": "process"},
            {"type": "error", "error": True},
            {"type": "cleanup"},  # Should not be processed
        ]
        initial = {"effects": [], "has_error": False}

        state, count = fold_until(apply, effects, initial, lambda s: s.get("has_error", False))
        assert state["has_error"] is True
        assert count == 3
        assert len(state["effects"]) == 3


class TestFoldWithEffectLikeObjects:
    """Tests using effect-like objects to simulate real usage."""

    def test_fold_with_effect_objects(self):
        """Fold with effect-like objects (simulating Effect class)."""

        class MockEffect:
            def __init__(self, effect_type, value):
                self.effect_type = effect_type
                self.value = value

        class MockState:
            def __init__(self, total=0, effects_seen=None):
                self.total = total
                self.effects_seen = effects_seen or []

        def apply(state, effect):
            return MockState(
                total=state.total + effect.value,
                effects_seen=[*state.effects_seen, effect.effect_type],
            )

        effects = [
            MockEffect("add", 10),
            MockEffect("add", 20),
            MockEffect("subtract", -5),
        ]

        result = fold(apply, effects, MockState())
        assert result.total == 25
        assert result.effects_seen == ["add", "add", "subtract"]

    def test_scan_for_time_travel_debugging(self):
        """Example: using scan for time-travel debugging."""

        class Counter:
            def __init__(self, value=0):
                self.value = value

        def apply(state, effect):
            if effect == "increment":
                return Counter(state.value + 1)
            if effect == "decrement":
                return Counter(state.value - 1)
            return state

        effects = ["increment", "increment", "decrement", "increment"]
        history = list(scan(apply, effects, Counter()))

        # We can access any point in time
        assert history[0].value == 0  # Initial
        assert history[1].value == 1  # After first increment
        assert history[2].value == 2  # After second increment
        assert history[3].value == 1  # After decrement
        assert history[4].value == 2  # Final state
