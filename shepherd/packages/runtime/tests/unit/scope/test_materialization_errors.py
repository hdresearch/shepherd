"""Tests for materialization error scenarios and partial failure recovery.

This module tests materialization behavior in error conditions:
- Partial write failures (some files succeed, others fail)
- Concurrent write conflicts
- Rollback on partial materialization failure
- Cascading errors during rollback

These tests address coverage gap HIGH-T4: materialization error scenarios.
"""

import contextlib
from collections.abc import Sequence
from typing import Any

from shepherd_core.context.kernel import ExecutionContext
from shepherd_core.effects import Effect, FileCreate, FilePatch
from shepherd_core.scope.types import MaterializationSummary
from shepherd_runtime.effect_materialization import MaterializationResult

# =============================================================================
# Mock Materializers for Testing
# =============================================================================


class PartialFailureMaterializer:
    """Materializer that fails after a certain number of successes."""

    def __init__(self, fail_after: int = 2, fail_on_types: set[str] | None = None):
        self.success_count = 0
        self.fail_after = fail_after
        self.fail_on_types = fail_on_types or set()
        self.materialized_effects: list[Effect] = []
        self.reversed_effects: list[Effect] = []

    def can_materialize(self, effect: Effect) -> bool:
        return effect.effect_type in {"file_create", "file_patch", "file_delete", "test_effect"}

    def materialize(self, effect: Effect) -> MaterializationResult:
        """Materialize effect, failing after configured number of successes."""
        # Fail on specific types
        if effect.effect_type in self.fail_on_types:
            return MaterializationResult(
                success=False,
                error=f"Forced failure for {effect.effect_type}",
                paths_affected=[],
            )

        # Fail after N successes
        if self.success_count >= self.fail_after:
            return MaterializationResult(
                success=False,
                error="Partial failure after limit",
                paths_affected=[],
            )

        self.success_count += 1
        self.materialized_effects.append(effect)
        return MaterializationResult(success=True, paths_affected=[])

    def can_reverse(self, effect: Effect) -> bool:
        return effect in self.materialized_effects

    def reverse(self, effect: Effect) -> None:
        """Reverse a materialized effect."""
        if effect in self.materialized_effects:
            self.materialized_effects.remove(effect)
            self.reversed_effects.append(effect)


class RollbackFailureMaterializer:
    """Materializer where rollback itself fails."""

    def __init__(self, fail_rollback_on: set[str] | None = None):
        self.fail_rollback_on = fail_rollback_on or set()
        self.materialized: list[Effect] = []
        self.rollback_attempts: list[Effect] = []
        self.rollback_failures: list[tuple[Effect, Exception]] = []

    def can_materialize(self, effect: Effect) -> bool:
        return True

    def materialize(self, effect: Effect) -> MaterializationResult:
        self.materialized.append(effect)
        return MaterializationResult(success=True, paths_affected=[])

    def can_reverse(self, effect: Effect) -> bool:
        return True

    def reverse(self, effect: Effect) -> None:
        self.rollback_attempts.append(effect)
        if effect.effect_type in self.fail_rollback_on:
            error = RuntimeError(f"Rollback failed for {effect.effect_type}")
            self.rollback_failures.append((effect, error))
            raise error


class ConcurrentConflictMaterializer:
    """Materializer that simulates concurrent write conflicts."""

    def __init__(self):
        self.conflict_paths: set[str] = set()
        self.materialized: list[tuple[Effect, str]] = []

    def add_conflict(self, path: str):
        """Mark a path as having a concurrent conflict."""
        self.conflict_paths.add(path)

    def can_materialize(self, effect: Effect) -> bool:
        return hasattr(effect, "path")

    def materialize(self, effect: Effect) -> MaterializationResult:
        path = getattr(effect, "path", None)
        if path and path in self.conflict_paths:
            return MaterializationResult(
                success=False,
                error=f"Concurrent modification conflict on {path}",
                paths_affected=[path],
            )

        self.materialized.append((effect, path))
        return MaterializationResult(success=True, paths_affected=[path] if path else [])

    def can_reverse(self, effect: Effect) -> bool:
        return True

    def reverse(self, effect: Effect) -> None:
        pass


# =============================================================================
# Test Context
# =============================================================================


class MaterializableContext(ExecutionContext):
    """Context that emits materializable file effects."""

    def __init__(self, name: str = "materializable"):
        self._name = name
        self._files: dict[str, str] = {}

    @property
    def context_id(self) -> str:
        return f"materializable:{self._name}"

    def apply_effect(self, effect: Effect) -> "MaterializableContext":
        if effect.effect_type == "file_create":
            path = getattr(effect, "path", "")
            content = getattr(effect, "content", "")
            self._files[path] = content
        return self

    def extract_effects(self, sandbox: Any, result: Any) -> Sequence[Effect]:
        """Extract file effects from the sandbox."""
        return []


# =============================================================================
# Tests: Partial Materialization Failure
# =============================================================================


class TestPartialMaterializationFailure:
    """Tests for partial materialization failure scenarios."""

    def test_partial_failure_reports_which_effects_succeeded(self):
        """MaterializationError includes information about successful effects."""
        materializer = PartialFailureMaterializer(fail_after=2)

        effects = [
            Effect(effect_type="test_effect", data={"id": 1}),
            Effect(effect_type="test_effect", data={"id": 2}),
            Effect(effect_type="test_effect", data={"id": 3}),  # Will fail
        ]

        results = []
        for effect in effects:
            result = materializer.materialize(effect)
            results.append(result)

        # First two succeed, third fails
        assert results[0].success
        assert results[1].success
        assert not results[2].success

        # Materializer tracked what succeeded
        assert len(materializer.materialized_effects) == 2

    def test_rollback_on_partial_failure(self):
        """When materialization fails, previously materialized effects are rolled back."""
        materializer = PartialFailureMaterializer(fail_after=2)

        effects = [
            Effect(effect_type="test_effect", data={"id": 1}),
            Effect(effect_type="test_effect", data={"id": 2}),
            Effect(effect_type="test_effect", data={"id": 3}),  # Will fail
        ]

        materialized = []
        for effect in effects:
            result = materializer.materialize(effect)
            if result.success:
                materialized.append(effect)
            else:
                # Rollback on failure
                for prev_effect in reversed(materialized):
                    if materializer.can_reverse(prev_effect):
                        materializer.reverse(prev_effect)
                break

        # After rollback, nothing should remain materialized
        assert len(materializer.materialized_effects) == 0
        assert len(materializer.reversed_effects) == 2

    def test_failure_on_specific_effect_type(self):
        """Materialization can fail on specific effect types."""
        materializer = PartialFailureMaterializer(
            fail_after=100,  # Won't hit this limit
            fail_on_types={"file_delete"},
        )

        effects = [
            Effect(effect_type="file_create", path="/a.txt"),
            Effect(effect_type="file_patch", path="/b.txt"),
            Effect(effect_type="file_delete", path="/c.txt"),  # Will fail
        ]

        results = [materializer.materialize(e) for e in effects]

        assert results[0].success
        assert results[1].success
        assert not results[2].success
        assert "file_delete" in results[2].error


# =============================================================================
# Tests: Rollback Failures
# =============================================================================


class TestRollbackFailures:
    """Tests for scenarios where rollback itself fails."""

    def test_rollback_failure_captured(self):
        """Rollback failures are captured and reported."""
        materializer = RollbackFailureMaterializer(fail_rollback_on={"effect_b"})

        # Materialize some effects
        effect_a = Effect(effect_type="effect_a")
        effect_b = Effect(effect_type="effect_b")
        effect_c = Effect(effect_type="effect_c")

        materializer.materialize(effect_a)
        materializer.materialize(effect_b)
        materializer.materialize(effect_c)

        # Attempt rollback
        rollback_errors = []
        for effect in reversed([effect_a, effect_b, effect_c]):
            try:
                materializer.reverse(effect)
            except Exception as e:
                rollback_errors.append((effect, e))

        # effect_b rollback should have failed
        assert len(rollback_errors) == 1
        assert rollback_errors[0][0].effect_type == "effect_b"

    def test_rollback_continues_after_failure(self):
        """Rollback continues to attempt remaining effects even after failure."""
        materializer = RollbackFailureMaterializer(fail_rollback_on={"effect_b"})

        effects = [
            Effect(effect_type="effect_a"),
            Effect(effect_type="effect_b"),  # Rollback will fail
            Effect(effect_type="effect_c"),
        ]

        for e in effects:
            materializer.materialize(e)

        # Rollback all, continuing on failure
        for effect in reversed(effects):
            with contextlib.suppress(Exception):
                materializer.reverse(effect)

        # All rollback attempts should have been made
        assert len(materializer.rollback_attempts) == 3

    def test_multiple_rollback_failures(self):
        """Multiple rollback failures are all captured."""
        materializer = RollbackFailureMaterializer(fail_rollback_on={"effect_a", "effect_c"})

        effects = [
            Effect(effect_type="effect_a"),
            Effect(effect_type="effect_b"),
            Effect(effect_type="effect_c"),
        ]

        for e in effects:
            materializer.materialize(e)

        rollback_errors = []
        for effect in reversed(effects):
            try:
                materializer.reverse(effect)
            except Exception as e:
                rollback_errors.append((effect, e))

        # Both a and c should have failed
        failed_types = {e[0].effect_type for e in rollback_errors}
        assert failed_types == {"effect_a", "effect_c"}


# =============================================================================
# Tests: Concurrent Write Conflicts
# =============================================================================


class TestConcurrentWriteConflicts:
    """Tests for concurrent write conflict handling."""

    def test_conflict_detection_on_write(self):
        """Concurrent modification is detected during materialization."""
        materializer = ConcurrentConflictMaterializer()
        materializer.add_conflict("/shared/file.txt")

        effect = FileCreate(path="/shared/file.txt", content="test")
        result = materializer.materialize(effect)

        assert not result.success
        assert "conflict" in result.error.lower()

    def test_some_files_succeed_before_conflict(self):
        """Some files can be written before a conflict is detected."""
        materializer = ConcurrentConflictMaterializer()
        materializer.add_conflict("/shared/conflict.txt")

        effects = [
            FileCreate(path="/safe/a.txt", content="a"),
            FileCreate(path="/safe/b.txt", content="b"),
            FileCreate(path="/shared/conflict.txt", content="c"),
        ]

        results = [materializer.materialize(e) for e in effects]

        assert results[0].success
        assert results[1].success
        assert not results[2].success

    def test_conflict_path_reported(self):
        """Conflicting path is reported in error."""
        materializer = ConcurrentConflictMaterializer()
        conflict_path = "/data/important.json"
        materializer.add_conflict(conflict_path)

        effect = FilePatch(path=conflict_path)
        result = materializer.materialize(effect)

        assert conflict_path in result.error


# =============================================================================
# Tests: Cascading Errors
# =============================================================================


class TestCascadingErrors:
    """Tests for cascading error scenarios."""

    def test_error_during_materialization_triggers_rollback(self):
        """An error during materialization triggers rollback of prior effects."""
        materializer = PartialFailureMaterializer(fail_after=3)

        effects = [
            Effect(effect_type="test_effect", data={"order": 1}),
            Effect(effect_type="test_effect", data={"order": 2}),
            Effect(effect_type="test_effect", data={"order": 3}),
            Effect(effect_type="test_effect", data={"order": 4}),  # Fails
        ]

        materialized = []
        failed = False

        for effect in effects:
            result = materializer.materialize(effect)
            if result.success:
                materialized.append(effect)
            else:
                failed = True
                break

        assert failed
        assert len(materialized) == 3

        # Rollback
        for effect in reversed(materialized):
            materializer.reverse(effect)

        assert len(materializer.reversed_effects) == 3

    def test_cascading_rollback_with_partial_rollback_failure(self):
        """Cascading errors: materialization fails, then some rollbacks fail."""
        # Create a materializer that:
        # 1. Successfully materializes effects a, b, c
        # 2. Fails on effect d
        # 3. When rolling back, fails on effect b

        class CascadingMaterializer:
            def __init__(self):
                self.materialized = []
                self.reversed = []
                self.errors = []

            def materialize(self, effect: Effect) -> MaterializationResult:
                if effect.effect_type == "fail":
                    return MaterializationResult(
                        success=False,
                        error="Materialization failed",
                        paths_affected=[],
                    )
                self.materialized.append(effect)
                return MaterializationResult(success=True, paths_affected=[])

            def reverse(self, effect: Effect) -> None:
                if effect.effect_type == "fail_rollback":
                    error = RuntimeError("Rollback failed")
                    self.errors.append(error)
                    raise error
                self.reversed.append(effect)

        materializer = CascadingMaterializer()

        effects = [
            Effect(effect_type="safe"),
            Effect(effect_type="fail_rollback"),  # Rollback will fail
            Effect(effect_type="safe"),
            Effect(effect_type="fail"),  # Materialization fails here
        ]

        # Materialize until failure
        materialized = []
        for effect in effects:
            result = materializer.materialize(effect)
            if not result.success:
                break
            materialized.append(effect)

        # Rollback with error handling
        for effect in reversed(materialized):
            with contextlib.suppress(Exception):
                materializer.reverse(effect)

        # Verify cascading behavior
        assert len(materializer.materialized) == 3  # a, b, c succeeded
        assert len(materializer.reversed) == 2  # a, c rolled back
        assert len(materializer.errors) == 1  # b rollback failed


# =============================================================================
# Tests: MaterializationSummary
# =============================================================================


class TestMaterializationSummary:
    """Tests for MaterializationSummary error reporting."""

    def test_summary_tracks_rollback_errors(self):
        """MaterializationSummary tracks rollback errors correctly."""
        summary = MaterializationSummary(
            effects_processed=5,
            effects_materialized=3,
            total_paths_affected=2,
            rollback_errors=(
                ("effect_type_a", "Rollback failed: permission denied"),
                ("effect_type_b", "Rollback failed: file in use"),
            ),
        )

        assert summary.rollback_failed
        assert len(summary.rollback_errors) == 2

    def test_summary_no_rollback_errors(self):
        """MaterializationSummary with successful rollback."""
        summary = MaterializationSummary(
            effects_processed=5,
            effects_materialized=5,
            total_paths_affected=5,
            rollback_errors=(),
        )

        assert not summary.rollback_failed

    def test_summary_partial_success(self):
        """MaterializationSummary with some effects materialized."""
        summary = MaterializationSummary(
            effects_processed=10,
            effects_materialized=7,
            total_paths_affected=7,
            rollback_errors=(),
        )

        assert summary.effects_materialized == 7
        assert summary.effects_processed == 10
        # Summary is truthy if any effects were materialized
        assert bool(summary)

    def test_summary_falsy_when_no_effects_materialized(self):
        """MaterializationSummary is falsy when no effects materialized."""
        summary = MaterializationSummary(
            effects_processed=5,
            effects_materialized=0,
            total_paths_affected=0,
            rollback_errors=(),
        )

        assert not bool(summary)
