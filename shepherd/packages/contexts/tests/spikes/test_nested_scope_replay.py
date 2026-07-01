"""Capability spikes for nested scope effect replay.

These spikes document the ACTUAL scope behavior that the cache replay
system is designed around. See DESIGN-effect-replay.md.

Key behaviors (verified):
1. Child emit() propagates to parent stream: YES
2. Child emit() updates parent context state: YES (via _receive_layer)
3. Skipping _receive_layer() prevents propagation: YES
4. ImmutableScope.apply_effect() only updates LOCAL bindings: YES
   (inherited bindings require shadowing first - see Spike 4b and replay_effects)

Design rationale:
- emit() propagates to parent so task effects update user's scope
- replay_effects() deliberately skips _receive_layer() for cache isolation
- Binding shadowing enables isolated state derivation in child scopes

The distinction between emit() and replay_effects() is critical:
- emit(): Full propagation (live execution path)
- replay_effects(): No propagation (cache replay, isolation needed)
"""

import pytest
from shepherd_contexts.workspace import WorkspaceRef
from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
from shepherd_core.effects import DiffPatch
from shepherd_core.scope.stream import EffectLayer
from shepherd_runtime.scope import Scope

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_patch() -> DiffPatch:
    """Create a sample DiffPatch for testing."""
    return DiffPatch(
        patch="diff --git a/test.py b/test.py\n+print('hello')",
        files_changed=("test.py",),
    )


# =============================================================================
# Spike 1: Child emit() propagates to parent stream
# =============================================================================


class TestSpike1ChildEmitPropagatesToParent:
    """Verify that child scope emit() adds effects to parent stream."""

    def test_child_emit_appears_in_parent_stream(self, git_workspace, sample_patch):
        """Effects emitted in child should appear in parent's stream."""
        with Scope(root=True) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            parent.bind("workspace", workspace)

            # Record parent stream length before child
            parent_stream_len_before = len(parent.effects)

            # Create child scope and emit effect
            child = parent.child()
            with child:
                effect = WorkspacePatchCaptured(
                    context_id=workspace.context_id,
                    binding_name="workspace",
                    patch=sample_patch,
                    files_changed=sample_patch.files_changed,
                )
                child.emit(effect)

                # Effect should be in child stream
                child_effects = list(child.effects.query(WorkspacePatchCaptured))
                assert len(child_effects) == 1, "Effect should be in child stream"

            # After child exits, effect should ALSO be in parent stream
            parent_effects = list(parent.effects.query(WorkspacePatchCaptured))
            assert len(parent_effects) == 1, "Effect should propagate to parent stream"

            # Verify it's the same effect
            assert parent_effects[0].effect.patch == sample_patch

        print("SPIKE 1 PASSED: Child emit() propagates to parent stream")


# =============================================================================
# Spike 2: Child emit() DOES update parent's context state (via _receive_layer)
# =============================================================================


class TestSpike2ChildEmitUpdatesParentState:
    """Verify that child emit() DOES update parent's context state.

    This is intentional: when a task runs in a child scope and emits
    effects, those effects should update the parent's bindings so the
    user sees the state changes. The mechanism is _receive_layer(),
    which both records the effect AND applies it to parent bindings.

    For isolated replay (cache hits), use replay_effects() which
    deliberately skips _receive_layer() - see Spike 3 and Spike 7.
    """

    def test_parent_context_state_updated_after_child_emit(self, git_workspace, sample_patch):
        """Parent's workspace.pending_patches SHOULD be updated by child emit."""
        with Scope(root=True) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            ws_ref = parent.bind("workspace", workspace)

            # Parent's workspace has no patches initially
            assert len(ws_ref.pending_patches) == 0

            # Create child scope and emit effect
            child = parent.child()
            with child:
                effect = WorkspacePatchCaptured(
                    context_id=workspace.context_id,
                    binding_name="workspace",
                    patch=sample_patch,
                    files_changed=sample_patch.files_changed,
                )
                child.emit(effect)

                # Child's emit() does two things:
                # 1. Applies effect locally (but child has no local workspace binding)
                # 2. Calls parent._receive_layer() which applies to parent's binding
                child_ws = child.get_context("workspace")
                print(f"Child workspace patches: {len(child_ws.pending_patches)}")

            # After child exits, check parent's view
            parent_ws = parent.get_context("workspace")
            parent_patches = len(parent_ws.pending_patches)
            print(f"Parent workspace patches after child: {parent_patches}")

            # Parent state IS updated because _receive_layer() applies the effect
            # This is intentional - task effects should reach the user's scope
            assert parent_patches == 1, (
                f"Parent context state SHOULD be updated by child emit (via _receive_layer). "
                f"Expected 1 patch, found {parent_patches}."
            )

        print("SPIKE 2 PASSED: Child emit() updates parent context state via _receive_layer")


# =============================================================================
# Spike 3: Skipping _receive_layer() prevents propagation
# =============================================================================


class TestSpike3SkipReceiveLayerPreventsPropagatation:
    """Verify that not calling _receive_layer() prevents propagation."""

    def test_direct_scope_modification_does_not_propagate(self, git_workspace, sample_patch):
        """Directly modifying child._scope should NOT propagate to parent."""
        with Scope(root=True) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            parent.bind("workspace", workspace)

            parent_stream_len_before = len(parent.effects)

            child = parent.child()
            with child:
                # Create effect and layer manually (like replay_effects would)
                effect = WorkspacePatchCaptured(
                    context_id=workspace.context_id,
                    binding_name="workspace",
                    patch=sample_patch,
                    files_changed=sample_patch.files_changed,
                )

                layer = EffectLayer(
                    effect=effect,
                    sequence=len(child._scope._stream._layers),
                    source_context=effect.context_id,
                    scope_id=child._scope._id,
                    scope_depth=child._depth,
                )

                # Directly modify child's scope WITHOUT calling _receive_layer
                child._scope = child._scope.with_layer(layer)
                child._scope = child._scope.apply_effect(effect)

                # Effect should be in child stream
                child_effects = list(child.effects.query(WorkspacePatchCaptured))
                assert len(child_effects) == 1, "Effect should be in child stream"

            # After child exits, effect should NOT be in parent stream
            parent_stream_len_after = len(parent.effects)
            parent_effects = list(parent.effects.query(WorkspacePatchCaptured))

            assert len(parent_effects) == 0, (
                f"Effect should NOT propagate when _receive_layer is skipped. "
                f"Found {len(parent_effects)} effects in parent."
            )

            print(f"Parent stream: {parent_stream_len_before} -> {parent_stream_len_after}")

        print("SPIKE 3 PASSED: Skipping _receive_layer() prevents propagation")


# =============================================================================
# Spike 4: ImmutableScope.apply_effect() only updates LOCAL bindings
# =============================================================================


class TestSpike4ApplyEffectOnlyAffectsLocalBindings:
    """Verify that ImmutableScope.apply_effect() only updates LOCAL bindings.

    This is by design: apply_effect() iterates over self._bindings, not
    inherited parent bindings. This enables:
    - Clear ownership: each scope owns its bindings
    - Isolation: replay_effects() can shadow then apply without affecting parent

    To update inherited bindings, either:
    - Use emit() which propagates via _receive_layer (see Spike 2)
    - Shadow locally first, then apply_effect (see Spike 4b - the replay_effects pattern)
    """

    def test_apply_effect_does_not_update_inherited_binding(self, git_workspace, sample_patch):
        """apply_effect() should NOT update inherited bindings - only local ones."""
        with Scope(root=True) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            parent.bind("workspace", workspace)

            child = parent.child()
            with child:
                # Verify child can see parent's binding (via inheritance)
                child_ws_before = child.get_context("workspace")
                assert child_ws_before is not None
                assert len(child_ws_before.pending_patches) == 0

                # Child has NO local bindings - workspace is inherited
                assert len(child._scope._bindings) == 0, "Child should have no local bindings"

                # Create effect targeting the workspace
                effect = WorkspacePatchCaptured(
                    context_id=workspace.context_id,
                    binding_name="workspace",
                    patch=sample_patch,
                    files_changed=sample_patch.files_changed,
                )

                # Manually apply effect to child's ImmutableScope
                # This simulates what happens inside emit() or replay_effects()
                layer = EffectLayer(
                    effect=effect,
                    sequence=len(child._scope._stream._layers),
                    source_context=effect.context_id,
                    scope_id=child._scope._id,
                    scope_depth=child._depth,
                )

                child._scope = child._scope.with_layer(layer)
                child._scope = child._scope.apply_effect(effect)

                # apply_effect() only iterates over LOCAL _bindings
                # Since child has no local bindings, nothing is updated
                child_ws_after = child.get_context("workspace")
                child_patches = len(child_ws_after.pending_patches)

                print(f"Child workspace patches after apply_effect: {child_patches}")

                # apply_effect() does NOT update inherited bindings
                # This is intentional - see Spike 4b for the workaround (shadow locally)
                assert child_patches == 0, (
                    f"apply_effect() should NOT update inherited bindings. "
                    f"Expected 0 patches (binding is inherited, not local), found {child_patches}."
                )

        print("SPIKE 4 PASSED: apply_effect() only affects local bindings, not inherited")


# =============================================================================
# Spike 4b: Workaround - bind locally before apply_effect
# =============================================================================


class TestSpike4bLocalBindingWorkaround:
    """Test workaround: copy binding to child scope before apply_effect."""

    def test_local_binding_allows_apply_effect(self, git_workspace, sample_patch):
        """If we bind locally in child, apply_effect should work."""
        with Scope(root=True) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            parent.bind("workspace", workspace)

            child = parent.child()
            with child:
                # Get workspace from parent (via inheritance)
                inherited_ws = child.get_context("workspace")

                # Bind it LOCALLY to child scope
                # This creates a local binding that apply_effect can find
                child.bind("workspace", inherited_ws)

                # Now create and apply effect
                effect = WorkspacePatchCaptured(
                    context_id=inherited_ws.context_id,
                    binding_name="workspace",
                    patch=sample_patch,
                    files_changed=sample_patch.files_changed,
                )

                layer = EffectLayer(
                    effect=effect,
                    sequence=len(child._scope._stream._layers),
                    source_context=effect.context_id,
                    scope_id=child._scope._id,
                    scope_depth=child._depth,
                )

                child._scope = child._scope.with_layer(layer)
                child._scope = child._scope.apply_effect(effect)

                # Now child should see updated workspace
                child_ws_after = child.get_context("workspace")
                child_patches = len(child_ws_after.pending_patches)

                print(f"Child workspace patches with local binding: {child_patches}")

                assert child_patches == 1, (
                    f"Child should see updated workspace with local binding. Expected 1 patch, found {child_patches}."
                )

        print("SPIKE 4b PASSED: Local binding workaround works")


# =============================================================================
# Spike 4c: Check if apply_effect should delegate to parent
# =============================================================================


class TestSpike4cApplyEffectParentDelegation:
    """Understand current behavior - does apply_effect check parent bindings?"""

    def test_apply_effect_only_checks_local_bindings(self, git_workspace, sample_patch):
        """Confirm apply_effect() only looks at local _bindings, not parent."""
        with Scope(root=True) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            parent.bind("workspace", workspace)

            child = parent.child()
            with child:
                # Child has no local bindings
                assert len(child._scope._bindings) == 0, "Child should have no local bindings"

                # But can access parent binding via get_binding
                binding = child._scope.get_binding("workspace")
                assert binding is not None, "Should find parent binding via get_binding"

                # Now apply effect
                effect = WorkspacePatchCaptured(
                    context_id=workspace.context_id,
                    binding_name="workspace",
                    patch=sample_patch,
                    files_changed=sample_patch.files_changed,
                )

                # apply_effect returns self if no local binding matches
                new_scope = child._scope.apply_effect(effect)
                assert new_scope is child._scope, "apply_effect should return self unchanged when binding is in parent"

        print("SPIKE 4c CONFIRMED: apply_effect() only checks local bindings")


# =============================================================================
# Spike 4d: Complete replay workflow simulation
# =============================================================================


class TestSpike4dCompleteReplayWorkflow:
    """Test the complete replay workflow with local binding."""

    def test_replay_with_shadowed_binding(self, git_workspace, sample_patch):
        """Simulate cache replay: shadow binding, apply effects, verify isolation."""
        with Scope(root=True) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            parent.bind("workspace", workspace)

            # Parent has workspace with 0 patches
            parent_ws = parent.get_context("workspace")
            assert len(parent_ws.pending_patches) == 0

            child = parent.child()
            with child:
                # Step 1: Get inherited binding
                inherited_ws = child.get_context("workspace")
                assert len(inherited_ws.pending_patches) == 0

                # Step 2: Shadow it locally (this is what replay_effects would do)
                child.bind("workspace", inherited_ws)

                # Step 3: Create effect (simulating cached effect)
                effect = WorkspacePatchCaptured(
                    context_id="different:context:id",  # Mismatched - like cache replay
                    binding_name="workspace",
                    patch=sample_patch,
                    files_changed=sample_patch.files_changed,
                )

                # Step 4: Apply effect (simulating replay_effects)
                layer = EffectLayer(
                    effect=effect,
                    sequence=len(child._scope._stream._layers),
                    source_context=effect.context_id,
                    scope_id=child._scope._id,
                    scope_depth=child._depth,
                )
                child._scope = child._scope.with_layer(layer)
                child._scope = child._scope.apply_effect(effect)

                # Step 5: Child sees updated workspace
                child_ws = child.get_context("workspace")
                assert len(child_ws.pending_patches) == 1, "Child should see 1 patch"

                # Step 6: Effect is in child stream
                child_effects = list(child.effects.query(WorkspacePatchCaptured))
                assert len(child_effects) == 1

            # Step 7: After child exits, parent still has 0 patches (isolation)
            parent_ws_after = parent.get_context("workspace")
            assert len(parent_ws_after.pending_patches) == 0, "Parent workspace should be unchanged (child shadowed)"

            # Step 8: Effect is NOT in parent stream (no propagation)
            parent_effects = list(parent.effects.query(WorkspacePatchCaptured))
            assert len(parent_effects) == 0, "Effect should NOT be in parent stream (no _receive_layer call)"

        print("SPIKE 4d PASSED: Complete replay workflow with shadowed binding works")


# =============================================================================
# Spike 4e: How does emit() work during real task execution?
# =============================================================================


class TestSpike4eEmitDuringTaskExecution:
    """Check how emit() works when context is bound in child (like task.py does)."""

    def test_emit_with_locally_bound_context(self, git_workspace, sample_patch):
        """When context is bound locally (like task.py), emit works correctly."""
        with Scope(root=True) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            # Note: NOT binding in parent

            child = parent.child()
            with child:
                # Bind workspace in CHILD scope (like task.py does)
                child.bind("workspace", workspace)

                # Emit effect
                effect = WorkspacePatchCaptured(
                    context_id=workspace.context_id,
                    binding_name="workspace",
                    patch=sample_patch,
                    files_changed=sample_patch.files_changed,
                )
                child.emit(effect)

                # Child sees updated workspace
                child_ws = child.get_context("workspace")
                assert len(child_ws.pending_patches) == 1, (
                    f"Child should see 1 patch, got {len(child_ws.pending_patches)}"
                )

            # Effect propagates to parent stream (via _receive_layer)
            parent_effects = list(parent.effects.query(WorkspacePatchCaptured))
            assert len(parent_effects) == 1, "Effect should propagate to parent stream"

        print("SPIKE 4e PASSED: emit() works correctly with locally bound context")


# =============================================================================
# Spike 6: Use update_context() instead of apply_effect()
# =============================================================================


class TestSpike6UpdateContextApproach:
    """Test cleaner approach: apply to context directly, then update_context()."""

    def test_direct_apply_then_update_context(self, git_workspace, sample_patch):
        """Apply effects directly to context, then use update_context()."""
        with Scope(root=True) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            parent.bind("workspace", workspace)

            # Parent workspace has 0 patches
            assert len(parent.get_context("workspace").pending_patches) == 0

            child = parent.child()
            with child:
                # Step 1: Get current context (from parent via inheritance)
                current_ws = child.get_context("workspace")
                assert len(current_ws.pending_patches) == 0

                # Step 2: Create effect (simulating cached effect)
                effect = WorkspacePatchCaptured(
                    context_id="different:context:id",  # Mismatched - like cache replay
                    binding_name="workspace",
                    patch=sample_patch,
                    files_changed=sample_patch.files_changed,
                )

                # Step 3: Apply effect DIRECTLY to context (like ApplyPhase does)
                new_ws = current_ws.apply_effect(effect)
                assert len(new_ws.pending_patches) == 1, "Direct apply_effect should work"

                # Step 4: Add effect to stream (for querying)
                layer = EffectLayer(
                    effect=effect,
                    sequence=len(child._scope._stream._layers),
                    source_context=effect.context_id,
                    scope_id=child._scope._id,
                    scope_depth=child._depth,
                )
                child._scope = child._scope.with_layer(layer)

                # Step 5: Update context via scope (delegates to parent)
                child.update_context("workspace", new_ws)

                # Step 6: Child should now see updated workspace
                child_ws_after = child.get_context("workspace")
                assert len(child_ws_after.pending_patches) == 1, (
                    f"Child should see updated workspace. Got {len(child_ws_after.pending_patches)}"
                )

            # Step 7: IMPORTANT - Parent should ALSO see the update!
            # Because update_context() delegated to parent
            parent_ws_after = parent.get_context("workspace")
            parent_patches = len(parent_ws_after.pending_patches)
            print(f"Parent patches after child update_context: {parent_patches}")

            # NOTE: This may or may not be desired behavior for cache replay!
            # If we want isolation, we should shadow locally instead

        print("SPIKE 6 PASSED: Direct apply + update_context() works")

    def test_update_context_affects_parent_binding(self, git_workspace, sample_patch):
        """Confirm that update_context() for inherited binding updates parent."""
        with Scope(root=True) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            parent.bind("workspace", workspace)

            child = parent.child()
            with child:
                current_ws = child.get_context("workspace")

                effect = WorkspacePatchCaptured(
                    context_id=current_ws.context_id,
                    binding_name="workspace",
                    patch=sample_patch,
                    files_changed=sample_patch.files_changed,
                )

                new_ws = current_ws.apply_effect(effect)
                child.update_context("workspace", new_ws)

            # After child exits, check parent
            parent_ws = parent.get_context("workspace")
            assert len(parent_ws.pending_patches) == 1, "update_context on inherited binding should update parent"

        print("SPIKE 6b CONFIRMED: update_context() updates parent binding")


# =============================================================================
# Spike 5: Verify persistence behavior (root only)
# =============================================================================


class TestSpike5PersistenceBehavior:
    """Verify that persistence only happens at root scope."""

    def test_child_emit_persists_at_root(self, git_workspace, sample_patch, tmp_path):
        """Child emit should persist to disk via root scope."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        with Scope(root=True, project_path=project_path) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            parent.bind("workspace", workspace)

            child = parent.child()
            with child:
                effect = WorkspacePatchCaptured(
                    context_id=workspace.context_id,
                    binding_name="workspace",
                    patch=sample_patch,
                    files_changed=sample_patch.files_changed,
                )
                child.emit(effect)

        # Resume and check if effect was persisted
        resumed = Scope.resume(project_path)
        with resumed:
            persisted_effects = list(resumed.effects.query(WorkspacePatchCaptured))
            assert len(persisted_effects) == 1, "Effect should be persisted"

        print("SPIKE 5a PASSED: Child emit() persists via root")

    def test_skip_receive_layer_does_not_persist(self, git_workspace, sample_patch, tmp_path):
        """Skipping _receive_layer should NOT persist to disk."""
        project_path = tmp_path / "project"
        project_path.mkdir()

        with Scope(root=True, project_path=project_path) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            parent.bind("workspace", workspace)

            child = parent.child()
            with child:
                effect = WorkspacePatchCaptured(
                    context_id=workspace.context_id,
                    binding_name="workspace",
                    patch=sample_patch,
                    files_changed=sample_patch.files_changed,
                )

                # Skip _receive_layer (simulating replay_effects)
                layer = EffectLayer(
                    effect=effect,
                    sequence=len(child._scope._stream._layers),
                    source_context=effect.context_id,
                    scope_id=child._scope._id,
                    scope_depth=child._depth,
                )
                child._scope = child._scope.with_layer(layer)
                child._scope = child._scope.apply_effect(effect)

        # Resume and check - effect should NOT be persisted
        resumed = Scope.resume(project_path)
        with resumed:
            persisted_effects = list(resumed.effects.query(WorkspacePatchCaptured))
            assert len(persisted_effects) == 0, (
                f"Effect should NOT be persisted when _receive_layer skipped. "
                f"Found {len(persisted_effects)} persisted effects."
            )

        print("SPIKE 5b PASSED: Skipping _receive_layer() does NOT persist")


# =============================================================================
# Spike 7: RECOMMENDED APPROACH - Shadow binding + replay_effects implementation
# =============================================================================


class TestSpike7RecommendedReplayApproach:
    """Test the recommended replay_effects() implementation.

    Based on spike findings, the recommended approach is:
    1. Shadow parent bindings locally (binding exists in child)
    2. Add effect layers to child stream (for querying)
    3. Call scope.apply_effect() which finds local binding
    4. No propagation to parent (no _receive_layer)
    5. No persistence (effects are from cache)

    This provides:
    - Child sees updated context state
    - Parent is isolated (doesn't see cached effects)
    - No re-persistence of cached effects
    """

    def test_recommended_replay_implementation(self, git_workspace, sample_patch):
        """Simulate the recommended replay_effects() implementation."""
        with Scope(root=True) as parent:
            workspace = WorkspaceRef.from_path(git_workspace)
            parent.bind("workspace", workspace)

            # Parent starts with 0 patches
            assert len(parent.get_context("workspace").pending_patches) == 0

            child = parent.child()
            with child:
                # === RECOMMENDED replay_effects() IMPLEMENTATION ===

                # Step 1: Identify binding names from effects
                effects = [
                    WorkspacePatchCaptured(
                        context_id="cached:context:id",  # From cache - different!
                        binding_name="workspace",
                        patch=sample_patch,
                        files_changed=sample_patch.files_changed,
                    ),
                ]

                binding_names = {getattr(e, "binding_name", None) for e in effects} - {None}

                # Step 2: Shadow parent bindings locally
                for name in binding_names:
                    # Check if local binding exists
                    has_local = any(b.name == name for b in child._scope._bindings)
                    if not has_local:
                        try:
                            inherited_ctx = child.get_context(name)
                            child.bind(name, inherited_ctx)
                        except Exception:
                            pass  # Binding doesn't exist anywhere

                # Step 3: Replay each effect
                for effect in effects:
                    layer = EffectLayer(
                        effect=effect,
                        sequence=len(child._scope._stream._layers),
                        source_context=getattr(effect, "context_id", None),
                        scope_id=child._scope._id,
                        scope_depth=child._depth,
                        # replayed=True,  # Would add this field
                    )

                    # Add to stream
                    child._scope = child._scope.with_layer(layer)

                    # Derive state (now finds local binding)
                    child._scope = child._scope.apply_effect(effect)

                # === END IMPLEMENTATION ===

                # Verify: Child sees updated state
                child_ws = child.get_context("workspace")
                assert len(child_ws.pending_patches) == 1, (
                    f"Child should see 1 patch, got {len(child_ws.pending_patches)}"
                )

                # Verify: Effect is in child stream
                child_effects = list(child.effects.query(WorkspacePatchCaptured))
                assert len(child_effects) == 1

            # Verify: Parent is isolated (no patches)
            parent_ws = parent.get_context("workspace")
            assert len(parent_ws.pending_patches) == 0, "Parent should NOT see replayed effects"

            # Verify: Effect is NOT in parent stream
            parent_effects = list(parent.effects.query(WorkspacePatchCaptured))
            assert len(parent_effects) == 0, "Effect should NOT propagate to parent stream"

        print("SPIKE 7 PASSED: Recommended replay_effects() approach works")

    def test_replay_works_at_any_depth(self, git_workspace, sample_patch):
        """Verify replay works for deeply nested scopes (not just direct child)."""
        with Scope(root=True) as root:
            workspace = WorkspaceRef.from_path(git_workspace)
            root.bind("workspace", workspace)

            # Create nested scope hierarchy
            level1 = root.child()
            with level1:
                level2 = level1.child()
                with level2:
                    level3 = level2.child()
                    with level3:
                        # Replay at level 3 (great-grandchild of root)

                        # Shadow binding locally
                        inherited_ws = level3.get_context("workspace")
                        level3.bind("workspace", inherited_ws)

                        # Create and replay effect
                        effect = WorkspacePatchCaptured(
                            context_id="cached:id",
                            binding_name="workspace",
                            patch=sample_patch,
                            files_changed=sample_patch.files_changed,
                        )

                        layer = EffectLayer(
                            effect=effect,
                            sequence=len(level3._scope._stream._layers),
                            source_context=effect.context_id,
                            scope_id=level3._scope._id,
                            scope_depth=level3._depth,
                        )
                        level3._scope = level3._scope.with_layer(layer)
                        level3._scope = level3._scope.apply_effect(effect)

                        # Level 3 sees update
                        assert len(level3.get_context("workspace").pending_patches) == 1

                    # Level 2 is isolated
                    assert len(level2.get_context("workspace").pending_patches) == 0

                # Level 1 is isolated
                assert len(level1.get_context("workspace").pending_patches) == 0

            # Root is isolated
            assert len(root.get_context("workspace").pending_patches) == 0

        print("SPIKE 7b PASSED: Replay works at any nesting depth")


# =============================================================================
# Run all spikes
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
