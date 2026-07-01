"""Tests for Effect Routing (Spikes I1/I2 Validation).

These tests validate that effects route correctly to context apply_effect() methods.

Design Decisions Validated:
- D9: Git effects MUST include binding_name="workspace" for stable routing
- I1: merge() re-emits effects to parent stream
- I2: Dual-mode routing (binding_name + context_id)

Test Status:
- Scope infrastructure (emit, fork, merge, discard) is IMPLEMENTED
- Effect routing to apply_effect() requires git effect handling in WorkspaceRef
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .fixtures import GitBranchCreated, GitCommitCreated

if TYPE_CHECKING:
    from pathlib import Path


class TestEffectBindingName:
    """Tests for D9: binding_name routing."""

    def test_git_effect_has_binding_name(self) -> None:
        """Git effects have binding_name='workspace' by default."""
        effect = GitBranchCreated(
            branch_name="test",
            from_commit="abc123" + "0" * 34,
        )

        assert effect.binding_name == "workspace"

    def test_commit_effect_has_binding_name(self) -> None:
        """GitCommitCreated also has binding_name."""
        effect = GitCommitCreated(
            sha="abc123" + "0" * 34,
            message="Test",
            author="Test",
            parent_shas=(),
        )

        assert effect.binding_name == "workspace"


class TestScopeEffectEmission:
    """Tests for effect emission and routing through scope.

    Scope infrastructure (emit) is IMPLEMENTED.
    Routing to apply_effect requires WorkspaceRef git field implementation.
    """

    def test_emitted_effect_reaches_stream(self, temp_git_repo: Path) -> None:
        """Emitted effect appears in scope.effects stream.

        This test validates that Scope.emit() works for custom effects.
        The scope infrastructure is already implemented.
        """
        from shepherd_contexts.workspace import WorkspaceRef
        from shepherd_runtime.scope import Scope

        scope = Scope()
        workspace = WorkspaceRef.from_path(temp_git_repo)
        scope.bind("workspace", workspace)

        effect = GitBranchCreated(
            branch_name="test",
            from_commit=workspace.base_commit,
        )
        scope.emit(effect)

        # Effect should be in the stream
        # Note: scope.effects returns EffectLayer objects, access .effect for the Effect
        assert any(getattr(layer.effect, "effect_type", None) == "git_branch_created" for layer in scope.effects)

    @pytest.mark.xfail(
        reason="WorkspaceRef.apply_effect() does not handle git effects yet",
        strict=True,
    )
    def test_effect_routes_to_apply_effect(self, temp_git_repo: Path) -> None:
        """Effect with binding_name routes to context.apply_effect().

        Requires WorkspaceRef to implement virtual_branches field and
        apply_effect() handling for GitBranchCreated.
        """
        from shepherd_contexts.workspace import WorkspaceRef
        from shepherd_runtime.scope import Scope

        scope = Scope()
        workspace = WorkspaceRef.from_path(temp_git_repo)
        ref = scope.bind("workspace", workspace)

        effect = GitBranchCreated(
            branch_name="routed-branch",
            from_commit=workspace.base_commit,
            binding_name="workspace",  # D9: explicit binding
        )
        scope.emit(effect)

        # ContextRef should reflect the updated state after apply_effect
        # This requires WorkspaceRef.apply_effect() to handle GitBranchCreated
        assert "routed-branch" in ref.current.virtual_branches


class TestCrossScopeVisibility:
    """Tests for I1: Cross-scope effect visibility on merge.

    Scope infrastructure (fork, merge, discard) is IMPLEMENTED.
    These tests validate the foundation primitives work correctly.
    """

    def test_child_effects_visible_after_merge(self, temp_git_repo: Path) -> None:
        """Effects from child scope are visible in parent after merge.

        Validates I1: merge() re-emits effects to parent stream.
        """
        from shepherd_contexts.workspace import WorkspaceRef
        from shepherd_runtime.scope import Scope

        parent = Scope()
        workspace = WorkspaceRef.from_path(temp_git_repo)
        parent.bind("workspace", workspace)

        # Fork child
        child = parent.fork()

        # Emit in child
        effect = GitBranchCreated(
            branch_name="child-branch",
            from_commit=workspace.base_commit,
            binding_name="workspace",
        )
        child.emit(effect)

        # Before merge: not in parent (fork creates independent scope)
        assert not any(getattr(layer.effect, "effect_type", None) == "git_branch_created" for layer in parent.effects)

        # Merge
        parent.merge(child)

        # After merge: effect is in parent stream
        branch_effects = [
            layer.effect
            for layer in parent.effects
            if getattr(layer.effect, "effect_type", None) == "git_branch_created"
        ]
        assert len(branch_effects) == 1
        assert branch_effects[0].branch_name == "child-branch"

    def test_child_effects_not_visible_after_discard(self, temp_git_repo: Path) -> None:
        """Effects from discarded child scope are NOT in parent.

        Note: discard() is called on the child, not parent.discard(child).
        """
        from shepherd_contexts.workspace import WorkspaceRef
        from shepherd_runtime.scope import Scope

        parent = Scope()
        workspace = WorkspaceRef.from_path(temp_git_repo)
        parent.bind("workspace", workspace)

        child = parent.fork()

        effect = GitBranchCreated(
            branch_name="discarded-branch",
            from_commit=workspace.base_commit,
            binding_name="workspace",
        )
        child.emit(effect)

        # Discard the child scope (not merged)
        child.discard()

        # Effect should NOT be in parent
        assert not any(getattr(layer.effect, "effect_type", None) == "git_branch_created" for layer in parent.effects)

    def test_merge_preserves_effect_order(self, temp_git_repo: Path) -> None:
        """Effects are merged in order (I1 ordering guarantee)."""
        from shepherd_contexts.workspace import WorkspaceRef
        from shepherd_runtime.scope import Scope

        parent = Scope()
        workspace = WorkspaceRef.from_path(temp_git_repo)
        parent.bind("workspace", workspace)

        child = parent.fork()

        # Emit multiple effects in order
        for i in range(3):
            child.emit(
                GitBranchCreated(
                    branch_name=f"branch-{i}",
                    from_commit=workspace.base_commit,
                    binding_name="workspace",
                )
            )

        parent.merge(child)

        # Get branch effects in order
        branch_effects = [
            layer.effect
            for layer in parent.effects
            if getattr(layer.effect, "effect_type", None) == "git_branch_created"
        ]

        # Should be in order
        names = [e.branch_name for e in branch_effects]
        assert names == ["branch-0", "branch-1", "branch-2"]


class TestDualModeRouting:
    """Tests for I2: Dual-mode routing (binding_name + context_id).

    These tests require WorkspaceRef git field implementation.
    """

    @pytest.mark.xfail(
        reason="WorkspaceRef.apply_effect() does not handle git effects yet",
        strict=True,
    )
    def test_binding_name_takes_precedence(self, temp_git_repo: Path) -> None:
        """binding_name routing takes precedence over context_id.

        Requires WorkspaceRef to implement virtual_branches field.
        """
        from shepherd_contexts.workspace import WorkspaceRef
        from shepherd_runtime.scope import Scope

        scope = Scope()
        workspace = WorkspaceRef.from_path(temp_git_repo)
        ref = scope.bind("workspace", workspace)

        # Effect with mismatched context_id but correct binding_name
        effect = GitBranchCreated(
            branch_name="binding-routed",
            from_commit=workspace.base_commit,
            binding_name="workspace",  # Correct
            context_id="wrong-context-id",  # Incorrect
        )
        scope.emit(effect)

        # Should still route to workspace via binding_name
        assert "binding-routed" in ref.current.virtual_branches
