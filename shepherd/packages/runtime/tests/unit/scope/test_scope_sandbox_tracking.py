"""Tests for scope-based container sandbox tracking.

This module tests the sandbox tracking functionality added to ScopeProxy
for workspace patch layering in container execution.

See: PLAN-workspace-patch-layering.md (Change 8)
"""

from dataclasses import dataclass

from shepherd_core.effects import ContainerExecutionCompleted
from shepherd_runtime.scope import Scope


@dataclass
class MockSandbox:
    """Mock sandbox for testing."""

    sandbox_id: str
    context_name: str = "workspace"
    cleaned_up: bool = False

    def cleanup(self):
        """Mark sandbox as cleaned up."""
        self.cleaned_up = True


class TestContainerExecutionCompletedEffect:
    """Tests for ContainerExecutionCompleted effect."""

    def test_effect_creation(self):
        """Effect can be created with required fields."""
        effect = ContainerExecutionCompleted(
            sandbox_id="sandbox-123",
            context_name="workspace",
            task_name="WriteCode",
            has_workspace_changes=True,
        )

        assert effect.sandbox_id == "sandbox-123"
        assert effect.context_name == "workspace"
        assert effect.task_name == "WriteCode"
        assert effect.has_workspace_changes is True
        assert effect.effect_type == "container_execution_completed"

    def test_effect_serialization(self):
        """Effect can be serialized and deserialized."""
        from shepherd_core.effects import effect_from_dict

        effect = ContainerExecutionCompleted(
            sandbox_id="sandbox-456",
            context_name="workspace",
            has_workspace_changes=False,
        )

        # Serialize
        data = effect.model_dump()
        assert data["sandbox_id"] == "sandbox-456"
        assert data["effect_type"] == "container_execution_completed"

        # Deserialize
        restored = effect_from_dict(data)
        assert isinstance(restored, ContainerExecutionCompleted)
        assert restored.sandbox_id == "sandbox-456"


class TestRegisterSandbox:
    """Tests for scope.register_sandbox()."""

    def test_register_sandbox_stores_sandbox(self):
        """register_sandbox() stores the sandbox in the scope."""
        with Scope() as scope:
            sandbox = MockSandbox(sandbox_id="test-sandbox")
            scope.register_sandbox(sandbox)

            assert "test-sandbox" in scope._sandbox_tracker._sandboxes
            assert scope._sandbox_tracker._sandboxes["test-sandbox"] is sandbox

    def test_register_sandbox_without_id_logs_warning(self, caplog):
        """register_sandbox() logs warning if sandbox has no sandbox_id."""
        with Scope() as scope:
            # Object without sandbox_id attribute
            bad_sandbox = object()
            scope.register_sandbox(bad_sandbox)

            assert "Cannot register sandbox without sandbox_id" in caplog.text
            assert len(scope._sandbox_tracker._sandboxes) == 0

    def test_register_multiple_sandboxes(self):
        """Multiple sandboxes can be registered."""
        with Scope() as scope:
            sandbox1 = MockSandbox(sandbox_id="sandbox-1")
            sandbox2 = MockSandbox(sandbox_id="sandbox-2")

            scope.register_sandbox(sandbox1)
            scope.register_sandbox(sandbox2)

            assert len(scope._sandbox_tracker._sandboxes) == 2
            assert scope._sandbox_tracker._sandboxes["sandbox-1"] is sandbox1
            assert scope._sandbox_tracker._sandboxes["sandbox-2"] is sandbox2


class TestGetSandbox:
    """Tests for scope.get_sandbox()."""

    def test_get_sandbox_returns_registered_sandbox(self):
        """get_sandbox() returns a sandbox by ID."""
        with Scope() as scope:
            sandbox = MockSandbox(sandbox_id="lookup-test")
            scope.register_sandbox(sandbox)

            result = scope.get_sandbox("lookup-test")
            assert result is sandbox

    def test_get_sandbox_returns_none_for_unknown_id(self):
        """get_sandbox() returns None for unknown ID."""
        with Scope() as scope:
            result = scope.get_sandbox("nonexistent")
            assert result is None

    def test_get_sandbox_searches_parent_scope(self):
        """get_sandbox() searches up the scope hierarchy."""
        with Scope() as parent:
            sandbox = MockSandbox(sandbox_id="parent-sandbox")
            parent.register_sandbox(sandbox)

            with Scope() as child:
                # Child scope should find parent's sandbox
                result = child.get_sandbox("parent-sandbox")
                assert result is sandbox

    def test_get_sandbox_local_shadows_parent(self):
        """Local sandbox shadows parent sandbox with same ID."""
        with Scope() as parent:
            parent_sandbox = MockSandbox(sandbox_id="shared-id", context_name="parent")
            parent.register_sandbox(parent_sandbox)

            with Scope() as child:
                child_sandbox = MockSandbox(sandbox_id="shared-id", context_name="child")
                child.register_sandbox(child_sandbox)

                # Child's sandbox should be returned
                result = child.get_sandbox("shared-id")
                assert result is child_sandbox
                assert result.context_name == "child"


class TestGetLatestSandboxForContext:
    """Tests for scope.get_latest_sandbox_for_context()."""

    def test_returns_most_recent_sandbox(self):
        """Returns the most recent sandbox for the given context."""
        with Scope() as scope:
            # Register sandboxes
            sandbox1 = MockSandbox(sandbox_id="sandbox-1", context_name="workspace")
            sandbox2 = MockSandbox(sandbox_id="sandbox-2", context_name="workspace")
            scope.register_sandbox(sandbox1)
            scope.register_sandbox(sandbox2)

            # Emit completion effects
            scope.emit(
                ContainerExecutionCompleted(
                    sandbox_id="sandbox-1",
                    context_name="workspace",
                )
            )
            scope.emit(
                ContainerExecutionCompleted(
                    sandbox_id="sandbox-2",
                    context_name="workspace",
                )
            )

            # Should return sandbox-2 (most recent)
            result = scope.get_latest_sandbox_for_context("workspace")
            assert result is sandbox2

    def test_filters_by_context_name(self):
        """Only returns sandboxes matching the context name."""
        with Scope() as scope:
            workspace_sandbox = MockSandbox(sandbox_id="ws-sandbox", context_name="workspace")
            session_sandbox = MockSandbox(sandbox_id="sess-sandbox", context_name="session")
            scope.register_sandbox(workspace_sandbox)
            scope.register_sandbox(session_sandbox)

            scope.emit(
                ContainerExecutionCompleted(
                    sandbox_id="ws-sandbox",
                    context_name="workspace",
                )
            )
            scope.emit(
                ContainerExecutionCompleted(
                    sandbox_id="sess-sandbox",
                    context_name="session",
                )
            )

            # Should only return workspace sandbox
            result = scope.get_latest_sandbox_for_context("workspace")
            assert result is workspace_sandbox

    def test_returns_none_when_no_matching_sandbox(self):
        """Returns None when no sandbox matches the context."""
        with Scope() as scope:
            result = scope.get_latest_sandbox_for_context("workspace")
            assert result is None

    def test_searches_parent_scope(self):
        """Searches parent scope if no local match found."""
        with Scope() as parent:
            sandbox = MockSandbox(sandbox_id="parent-ws", context_name="workspace")
            parent.register_sandbox(sandbox)
            parent.emit(
                ContainerExecutionCompleted(
                    sandbox_id="parent-ws",
                    context_name="workspace",
                )
            )

            with Scope() as child:
                # Child should find parent's sandbox
                result = child.get_latest_sandbox_for_context("workspace")
                assert result is sandbox


class TestDiscardCleansSandboxes:
    """Tests for sandbox cleanup during scope.discard()."""

    def test_discard_cleans_up_sandboxes(self):
        """discard() cleans up all registered sandboxes."""
        with Scope() as scope:
            child = scope.fork()

            sandbox1 = MockSandbox(sandbox_id="sandbox-1")
            sandbox2 = MockSandbox(sandbox_id="sandbox-2")
            child.register_sandbox(sandbox1)
            child.register_sandbox(sandbox2)

            assert not sandbox1.cleaned_up
            assert not sandbox2.cleaned_up

            child.discard()

            assert sandbox1.cleaned_up
            assert sandbox2.cleaned_up
            assert len(child._sandbox_tracker._sandboxes) == 0

    def test_discard_continues_on_cleanup_error(self, caplog):
        """discard() continues cleanup even if one sandbox fails."""

        @dataclass
        class FailingSandbox:
            sandbox_id: str

            def cleanup(self):
                raise RuntimeError("Cleanup failed!")

        with Scope() as scope:
            child = scope.fork()

            failing = FailingSandbox(sandbox_id="failing")
            working = MockSandbox(sandbox_id="working")
            child.register_sandbox(failing)
            child.register_sandbox(working)

            # Should not raise
            child.discard()

            # Working sandbox should still be cleaned up
            assert working.cleaned_up
            assert "Cleanup failed!" in caplog.text
            assert len(child._sandbox_tracker._sandboxes) == 0

    def test_discard_clears_sandbox_dict(self):
        """discard() clears the sandbox dictionary."""
        with Scope() as scope:
            child = scope.fork()

            sandbox = MockSandbox(sandbox_id="test")
            child.register_sandbox(sandbox)
            assert len(child._sandbox_tracker._sandboxes) == 1

            child.discard()

            assert len(child._sandbox_tracker._sandboxes) == 0


class TestSandboxTrackingIntegration:
    """Integration tests for sandbox tracking with scope lifecycle."""

    def test_sandbox_tracking_with_fork_merge(self):
        """Sandbox tracking works correctly with fork/merge pattern."""
        with Scope() as parent:
            # Fork for speculative execution
            child = parent.fork()

            # Register and complete container execution in child
            sandbox = MockSandbox(sandbox_id="speculative-sandbox")
            child.register_sandbox(sandbox)
            child.emit(
                ContainerExecutionCompleted(
                    sandbox_id="speculative-sandbox",
                    context_name="workspace",
                    has_workspace_changes=True,
                )
            )

            # Child can find its own sandbox
            assert child.get_latest_sandbox_for_context("workspace") is sandbox

            # Merge to parent
            parent.merge(child)

            # Parent now has both the effect (via merge) and the sandbox
            # (via merge_from() absorbing sandbox registrations).
            # The effect records what happened for audit/debugging.
            effects = [e.effect for e in parent.effects]
            completion_effects = [e for e in effects if isinstance(e, ContainerExecutionCompleted)]
            assert len(completion_effects) == 1

    def test_sandbox_tracking_with_fork_discard(self):
        """Sandbox is cleaned up when fork is discarded."""
        with Scope() as parent:
            child = parent.fork()

            sandbox = MockSandbox(sandbox_id="discarded-sandbox")
            child.register_sandbox(sandbox)
            child.emit(
                ContainerExecutionCompleted(
                    sandbox_id="discarded-sandbox",
                    context_name="workspace",
                )
            )

            # Discard the fork
            child.discard()

            # Sandbox should be cleaned up
            assert sandbox.cleaned_up

            # Parent should have no trace of the sandbox
            assert parent.get_latest_sandbox_for_context("workspace") is None
            # Discarded fork must leave no stale references in parent tracker
            assert "will-discard" not in parent._sandbox_tracker._sandboxes

    def test_fork_sandbox_registration_stays_local(self):
        """Sandbox registered in a fork does NOT propagate to the parent tracker.

        Fork trackers use read-only parent links.  Registrations stay local
        until an explicit merge() calls merge_from().  This preserves fork
        isolation — discarded forks leave no trace in the parent.
        """
        with Scope() as parent:
            fork = parent.fork()
            sandbox = MockSandbox(sandbox_id="from-fork")
            fork.register_sandbox(sandbox)

            # Should NOT propagate to parent (fork tracker is read-only)
            assert "from-fork" not in parent._sandbox_tracker._sandboxes

            # But should be in fork's local tracker
            assert "from-fork" in fork._sandbox_tracker._sandboxes

    def test_merge_absorbs_sandbox_registrations(self):
        """After merge, parent tracker contains sandboxes from the merged fork."""
        with Scope() as parent:
            fork = parent.fork()
            sandbox = MockSandbox(sandbox_id="absorbed")
            fork.register_sandbox(sandbox)

            # Before merge: parent doesn't have it
            assert "absorbed" not in parent._sandbox_tracker._sandboxes

            parent.merge(fork)

            # After merge: parent has it via merge_from()
            assert "absorbed" in parent._sandbox_tracker._sandboxes
            assert parent._sandbox_tracker._sandboxes["absorbed"] is sandbox

    def test_propagation_skipped_for_fork_tracker(self):
        """Fork trackers have _propagate_on_register=False."""
        with Scope() as parent:
            fork = parent.fork()
            assert fork._sandbox_tracker._propagate_on_register is False

    def test_merge_propagates_context_updates(self):
        """Merge propagates context binding updates from fork to parent.

        After merge, parent's bindings should reflect updates made in the fork.
        This uses a mock context that satisfies the ExecutionContext protocol.
        """

        @dataclass
        class _MockContext:
            context_id: str = "mock-ctx"
            value: int = 0

        with Scope() as parent:
            parent.bind("test_ctx", _MockContext(value=1))
            fork = parent.fork()

            # Update context in fork
            fork.update_context("test_ctx", _MockContext(value=2))

            parent.merge(fork)

            # Parent's binding should now reflect the fork's update
            binding = parent.get_context("test_ctx")
            assert binding.value == 2


class TestSiblingScopeSandboxVisibility:
    """Tests for sibling scope sandbox visibility via parent propagation."""

    def test_sibling_scope_finds_sandbox_via_parent(self):
        """Sandbox registered in child_A is visible from child_B via the parent."""
        with Scope() as parent:
            with Scope() as child_a:
                sandbox = MockSandbox(sandbox_id="from-child-a")
                child_a.register_sandbox(sandbox)

            with Scope() as child_b:
                # child_b should find child_a's sandbox via the parent
                result = child_b.get_sandbox("from-child-a")
                assert result is sandbox

    def test_device_cleanup_finds_propagated_sandboxes(self):
        """Global/parent tracker has sandbox after child registration."""
        with Scope() as parent:
            with Scope() as child:
                sandbox = MockSandbox(sandbox_id="propagated")
                child.register_sandbox(sandbox)

            # Parent tracker should have the sandbox due to propagation
            assert "propagated" in parent._sandbox_tracker._sandboxes
            assert parent._sandbox_tracker._sandboxes["propagated"] is sandbox

    def test_propagation_idempotent_on_re_register(self):
        """Registering the same sandbox ID twice doesn't accumulate entries."""
        with Scope() as parent:
            with Scope() as child:
                sandbox1 = MockSandbox(sandbox_id="dup-id")
                sandbox2 = MockSandbox(sandbox_id="dup-id")
                child.register_sandbox(sandbox1)
                child.register_sandbox(sandbox2)

            # Parent should have exactly one entry for that ID (the latest)
            assert parent._sandbox_tracker._sandboxes["dup-id"] is sandbox2
            count = list(parent._sandbox_tracker._sandboxes.keys()).count("dup-id")
            assert count == 1

    def test_double_cleanup_is_safe(self):
        """Register in child, cleanup from parent, then cleanup from child; no errors."""
        with Scope() as parent:
            child = parent.fork()

            sandbox = MockSandbox(sandbox_id="double-clean")
            child.register_sandbox(sandbox)

            # Cleanup from parent tracker first
            parent._sandbox_tracker.cleanup()

            # Cleanup from child tracker second -- should not raise
            child._sandbox_tracker.cleanup()

            assert sandbox.cleaned_up
