"""Tests for reversibility functionality (Phase 2: Reversibility Declaration).

These tests validate the reversibility abstraction:
1. ReversibilityLevel enum values and composition
2. ExecutionContext.reversibility property
3. compute_composite_reversibility helper
4. TaskStarted.composite_reversibility field
"""

from shepherd_contexts import SessionState, WorkspaceRef
from shepherd_core.context import compute_composite_reversibility, is_execution_context
from shepherd_core.effects import Effect, TaskStarted
from shepherd_core.scope import Stream
from shepherd_core.types import ExecutionResult, ProviderBinding, ReversibilityLevel

# =============================================================================
# ReversibilityLevel Enum Tests
# =============================================================================


class TestReversibilityLevelEnum:
    """Test ReversibilityLevel enum basics."""

    def test_enum_values(self):
        """ReversibilityLevel should have three distinct levels."""
        # Enum uses auto(), so values are integers
        assert ReversibilityLevel.AUTO is not None
        assert ReversibilityLevel.COMPENSABLE is not None
        assert ReversibilityLevel.NONE is not None
        # Each has a distinct value
        assert ReversibilityLevel.AUTO != ReversibilityLevel.COMPENSABLE
        assert ReversibilityLevel.COMPENSABLE != ReversibilityLevel.NONE
        assert ReversibilityLevel.AUTO != ReversibilityLevel.NONE

    def test_all_levels_exist(self):
        """All three levels should be accessible."""
        levels = list(ReversibilityLevel)
        assert len(levels) == 3
        assert ReversibilityLevel.AUTO in levels
        assert ReversibilityLevel.COMPENSABLE in levels
        assert ReversibilityLevel.NONE in levels


class TestReversibilityLevelComposition:
    """Test ReversibilityLevel.compose() method."""

    def test_auto_with_auto(self):
        """AUTO + AUTO = AUTO."""
        result = ReversibilityLevel.AUTO.compose(ReversibilityLevel.AUTO)
        assert result == ReversibilityLevel.AUTO

    def test_auto_with_compensable(self):
        """AUTO + COMPENSABLE = COMPENSABLE (weakest wins)."""
        result = ReversibilityLevel.AUTO.compose(ReversibilityLevel.COMPENSABLE)
        assert result == ReversibilityLevel.COMPENSABLE

        # Commutative
        result = ReversibilityLevel.COMPENSABLE.compose(ReversibilityLevel.AUTO)
        assert result == ReversibilityLevel.COMPENSABLE

    def test_auto_with_none(self):
        """AUTO + NONE = NONE (weakest wins)."""
        result = ReversibilityLevel.AUTO.compose(ReversibilityLevel.NONE)
        assert result == ReversibilityLevel.NONE

        # Commutative
        result = ReversibilityLevel.NONE.compose(ReversibilityLevel.AUTO)
        assert result == ReversibilityLevel.NONE

    def test_compensable_with_compensable(self):
        """COMPENSABLE + COMPENSABLE = COMPENSABLE."""
        result = ReversibilityLevel.COMPENSABLE.compose(ReversibilityLevel.COMPENSABLE)
        assert result == ReversibilityLevel.COMPENSABLE

    def test_compensable_with_none(self):
        """COMPENSABLE + NONE = NONE (weakest wins)."""
        result = ReversibilityLevel.COMPENSABLE.compose(ReversibilityLevel.NONE)
        assert result == ReversibilityLevel.NONE

        # Commutative
        result = ReversibilityLevel.NONE.compose(ReversibilityLevel.COMPENSABLE)
        assert result == ReversibilityLevel.NONE

    def test_none_with_none(self):
        """NONE + NONE = NONE."""
        result = ReversibilityLevel.NONE.compose(ReversibilityLevel.NONE)
        assert result == ReversibilityLevel.NONE


class TestReversibilityLevelComposeAll:
    """Test ReversibilityLevel.compose_all() class method."""

    def test_compose_all_empty(self):
        """Empty iterable should return AUTO (identity element)."""
        result = ReversibilityLevel.compose_all([])
        assert result == ReversibilityLevel.AUTO

    def test_compose_all_single(self):
        """Single element should return that element."""
        assert ReversibilityLevel.compose_all([ReversibilityLevel.AUTO]) == ReversibilityLevel.AUTO
        assert ReversibilityLevel.compose_all([ReversibilityLevel.COMPENSABLE]) == ReversibilityLevel.COMPENSABLE
        assert ReversibilityLevel.compose_all([ReversibilityLevel.NONE]) == ReversibilityLevel.NONE

    def test_compose_all_multiple_auto(self):
        """Multiple AUTO should return AUTO."""
        result = ReversibilityLevel.compose_all(
            [
                ReversibilityLevel.AUTO,
                ReversibilityLevel.AUTO,
                ReversibilityLevel.AUTO,
            ]
        )
        assert result == ReversibilityLevel.AUTO

    def test_compose_all_mixed(self):
        """Mixed levels should return weakest."""
        result = ReversibilityLevel.compose_all(
            [
                ReversibilityLevel.AUTO,
                ReversibilityLevel.COMPENSABLE,
                ReversibilityLevel.AUTO,
            ]
        )
        assert result == ReversibilityLevel.COMPENSABLE

    def test_compose_all_with_none(self):
        """Any NONE should result in NONE."""
        result = ReversibilityLevel.compose_all(
            [
                ReversibilityLevel.AUTO,
                ReversibilityLevel.COMPENSABLE,
                ReversibilityLevel.NONE,
            ]
        )
        assert result == ReversibilityLevel.NONE

    def test_compose_all_generator(self):
        """Should work with generators."""
        levels = (level for level in [ReversibilityLevel.AUTO, ReversibilityLevel.COMPENSABLE])
        result = ReversibilityLevel.compose_all(levels)
        assert result == ReversibilityLevel.COMPENSABLE


# =============================================================================
# Context Reversibility Tests
# =============================================================================


class TestWorkspaceRefReversibility:
    """Test WorkspaceRef.reversibility property."""

    def test_workspace_has_reversibility_property(self, git_workspace):
        """WorkspaceRef should have reversibility property."""
        workspace = WorkspaceRef.from_path(git_workspace)
        assert hasattr(workspace, "reversibility")

    def test_workspace_reversibility_is_auto(self, git_workspace):
        """WorkspaceRef.reversibility should be AUTO (git is mechanically reversible)."""
        workspace = WorkspaceRef.from_path(git_workspace)
        assert workspace.reversibility == ReversibilityLevel.AUTO

    def test_workspace_reversibility_type(self, git_workspace):
        """WorkspaceRef.reversibility should return ReversibilityLevel."""
        workspace = WorkspaceRef.from_path(git_workspace)
        assert isinstance(workspace.reversibility, ReversibilityLevel)


class TestSessionStateReversibility:
    """Test SessionState.reversibility property."""

    def test_session_has_reversibility_property(self):
        """SessionState should have reversibility property."""
        session = SessionState(session_id="sess_test123")
        assert hasattr(session, "reversibility")

    def test_session_reversibility_is_auto(self):
        """SessionState.reversibility should be AUTO (sessions support replay)."""
        session = SessionState(session_id="sess_test123")
        assert session.reversibility == ReversibilityLevel.AUTO

    def test_session_reversibility_type(self):
        """SessionState.reversibility should return ReversibilityLevel."""
        session = SessionState(session_id="sess_test123")
        assert isinstance(session.reversibility, ReversibilityLevel)


# =============================================================================
# Composite Reversibility Tests
# =============================================================================


class TestComputeCompositeReversibility:
    """Test compute_composite_reversibility helper function."""

    def test_empty_contexts(self):
        """Empty context list should return AUTO."""
        result = compute_composite_reversibility([])
        assert result == ReversibilityLevel.AUTO

    def test_single_workspace(self, git_workspace):
        """Single WorkspaceRef should return AUTO."""
        workspace = WorkspaceRef.from_path(git_workspace)
        result = compute_composite_reversibility([workspace])
        assert result == ReversibilityLevel.AUTO

    def test_single_session(self):
        """Single SessionState should return AUTO."""
        session = SessionState(session_id="sess_test")
        result = compute_composite_reversibility([session])
        assert result == ReversibilityLevel.AUTO

    def test_workspace_and_session(self, git_workspace):
        """WorkspaceRef + SessionState should return AUTO (both are AUTO)."""
        workspace = WorkspaceRef.from_path(git_workspace)
        session = SessionState(session_id="sess_test")

        result = compute_composite_reversibility([workspace, session])
        assert result == ReversibilityLevel.AUTO

    def test_multiple_workspaces(self, git_workspace):
        """Multiple WorkspaceRefs should return AUTO."""
        ws1 = WorkspaceRef.from_path(git_workspace)
        ws2 = WorkspaceRef.from_path(git_workspace)

        result = compute_composite_reversibility([ws1, ws2])
        assert result == ReversibilityLevel.AUTO


class TestCompositeReversibilityWithMockContexts:
    """Test composite reversibility with mock contexts of different levels."""

    def test_with_compensable_context(self):
        """Mock context with COMPENSABLE should affect composite."""
        from collections.abc import Sequence

        class MockCompensableContext:
            """Mock context that is only compensable (e.g., email)."""

            @property
            def context_id(self) -> str:
                return "mock:compensable"

            @property
            def reversibility(self) -> ReversibilityLevel:
                return ReversibilityLevel.COMPENSABLE

            def configure(self, capabilities):
                return ProviderBinding(context_id=self.context_id)

            def prepare(self):
                return self

            def extract_effects(self, sandbox, result: ExecutionResult) -> Sequence[Effect]:
                return []

            def apply_effect(self, effect: Effect):
                return self

            def cleanup(self, error=None):
                pass

            def __str__(self) -> str:
                return ""

        mock = MockCompensableContext()
        session = SessionState(session_id="sess_test")

        # Session is AUTO, mock is COMPENSABLE -> composite is COMPENSABLE
        result = compute_composite_reversibility([session, mock])
        assert result == ReversibilityLevel.COMPENSABLE

    def test_with_none_context(self):
        """Mock context with NONE should make composite NONE."""
        from collections.abc import Sequence

        class MockIrreversibleContext:
            """Mock context that cannot be reversed (e.g., tweet)."""

            @property
            def context_id(self) -> str:
                return "mock:irreversible"

            @property
            def reversibility(self) -> ReversibilityLevel:
                return ReversibilityLevel.NONE

            def configure(self, capabilities):
                return ProviderBinding(context_id=self.context_id)

            def prepare(self):
                return self

            def extract_effects(self, sandbox, result: ExecutionResult) -> Sequence[Effect]:
                return []

            def apply_effect(self, effect: Effect):
                return self

            def cleanup(self, error=None):
                pass

            def __str__(self) -> str:
                return ""

        mock = MockIrreversibleContext()
        session = SessionState(session_id="sess_test")

        # Session is AUTO, mock is NONE -> composite is NONE
        result = compute_composite_reversibility([session, mock])
        assert result == ReversibilityLevel.NONE


# =============================================================================
# TaskStarted Tests
# =============================================================================


class TestTaskStartedEffect:
    """Test TaskStarted effect structure.

    Note: In v2, composite reversibility is computed at runtime via
    compute_composite_reversibility() or scope.composite_reversibility(),
    not stored on the TaskStarted effect.
    """

    def test_task_started_has_task_name(self):
        """TaskStarted should have task_name field."""
        effect = TaskStarted(task_name="TestTask")
        assert effect.task_name == "TestTask"

    def test_task_started_has_inputs(self):
        """TaskStarted should have inputs field."""
        effect = TaskStarted(task_name="TestTask", inputs={"key": "value"})
        assert effect.inputs == {"key": "value"}

    def test_task_started_inputs_default_empty(self):
        """TaskStarted.inputs should default to empty dict."""
        effect = TaskStarted(task_name="TestTask")
        assert effect.inputs == {}

    def test_task_started_serialization(self):
        """TaskStarted should survive JSON roundtrip."""
        stream = Stream()
        stream = stream.append(
            TaskStarted(
                task_name="TestTask",
                inputs={"prompt": "Hello"},
            )
        )

        json_str = stream.to_json()
        restored = Stream.from_json(json_str)

        assert len(restored) == 1
        effect = restored[0]  # Stream supports indexing
        assert effect.task_name == "TestTask"
        assert effect.inputs == {"prompt": "Hello"}


# =============================================================================
# Protocol Compliance Tests
# =============================================================================


class TestReversibilityProtocolCompliance:
    """Test that contexts properly implement reversibility in the protocol."""

    def test_workspace_is_execution_context_with_reversibility(self, git_workspace):
        """WorkspaceRef should satisfy ExecutionContext and have reversibility."""
        workspace = WorkspaceRef.from_path(git_workspace)

        # Should be an ExecutionContext
        assert is_execution_context(workspace)

        # Should have reversibility
        assert hasattr(workspace, "reversibility")
        assert isinstance(workspace.reversibility, ReversibilityLevel)

    def test_session_is_execution_context_with_reversibility(self):
        """SessionState should satisfy ExecutionContext and have reversibility."""
        session = SessionState(session_id="sess_test")

        # Should be an ExecutionContext
        assert is_execution_context(session)

        # Should have reversibility
        assert hasattr(session, "reversibility")
        assert isinstance(session.reversibility, ReversibilityLevel)
