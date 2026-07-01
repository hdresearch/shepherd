"""Focused tests for the binding collaborator."""

from shepherd_runtime.scope import Scope
from shepherd_tests.contexts import SimpleContext


class TestBindingService:
    def test_service_uses_live_parent_lookup_and_updates_declaring_scope(self) -> None:
        with Scope(root=True) as parent:
            parent.bind("workspace", SimpleContext(name="workspace", value=1))
            child = parent.child()

            service = child._binding_service

            parent.update_context("workspace", SimpleContext(name="workspace", value=5))
            assert service.get_context("workspace").value == 5

            service.update_context("workspace", SimpleContext(name="workspace", value=9))
            assert parent.get_context("workspace").value == 9
            assert service.get_binding("workspace").context.value == 9

    def test_service_local_bindings_preserve_lifecycle_wrappers(self) -> None:
        with Scope(root=True) as scope:
            scope.bind("workspace", SimpleContext(name="workspace", value=1))
            scope.mark_binding_lifecycle("workspace", is_prepared=True, in_lifecycle=True)

            bindings = scope._binding_service.local_bindings()

            assert [binding.name for binding in bindings] == ["workspace"]
            assert bindings[0].is_prepared is True
            assert bindings[0].in_lifecycle is True

    def test_service_fork_hooks_reset_copied_lifecycle_state(self) -> None:
        with Scope(root=True) as scope:
            scope.bind("workspace", SimpleContext(name="workspace", value=1))
            scope.mark_binding_lifecycle("workspace", is_prepared=True, in_lifecycle=True)

            fork = scope.fork()
            fork_binding = fork._binding_service.get_binding("workspace")

            assert fork_binding.is_prepared is False
            assert fork_binding.in_lifecycle is False
