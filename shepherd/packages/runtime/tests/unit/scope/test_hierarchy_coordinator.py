"""Focused tests for the hierarchy collaborator."""

from __future__ import annotations

import pytest
from shepherd_core.effects import Effect
from shepherd_runtime._scope._hierarchy import HierarchyCoordinator
from shepherd_runtime.scope import Scope
from shepherd_tests import MockProvider
from shepherd_tests.contexts import SimpleContext


class _MockSandbox:
    def __init__(self, sandbox_id: str) -> None:
        self.sandbox_id = sandbox_id
        self.cleaned_up = False

    def cleanup(self) -> None:
        self.cleaned_up = True


class TestHierarchyCoordinator:
    def test_child_keeps_live_parent_lookup_and_sandbox_ancestry(self) -> None:
        with Scope(root=True) as parent:
            parent.bind("workspace", SimpleContext(name="workspace", value=1))

            child = parent._hierarchy.child()

            assert isinstance(parent._hierarchy, HierarchyCoordinator)
            assert child._parent_proxy is parent
            assert child._depth == 1
            assert child._sandbox_tracker.parent_tracker is parent._sandbox_tracker

            parent.update_context("workspace", SimpleContext(name="workspace", value=5))
            assert child.get_context("workspace").value == 5

    def test_validate_and_initialize_root_persistence_preserve_root_boundary(self, tmp_path, monkeypatch) -> None:
        root_scope = Scope(project_path=tmp_path)
        initialize_calls: list[str] = []
        monkeypatch.setattr(root_scope._persistence_manager, "initialize", lambda: initialize_calls.append("root"))

        root_scope._hierarchy.initialize_root_persistence()
        assert initialize_calls == ["root"]

        nested_scope = Scope(project_path=tmp_path)
        with pytest.raises(ValueError, match="Nested Scope\\(\\) cannot set project_path or enable persistence"):
            nested_scope._hierarchy.validate_auto_nesting_configuration()

        nested_scope = Scope()
        nested_scope._attach_to_parent(root_scope)
        nested_calls: list[str] = []
        monkeypatch.setattr(nested_scope._persistence_manager, "initialize", lambda: nested_calls.append("nested"))
        nested_scope._hierarchy.initialize_root_persistence()
        assert nested_calls == []

        global_scope = Scope(project_path=tmp_path, _is_global=True)
        global_calls: list[str] = []
        monkeypatch.setattr(global_scope._persistence_manager, "initialize", lambda: global_calls.append("global"))
        global_scope._hierarchy.initialize_root_persistence()
        assert global_calls == []

    def test_fork_copies_provider_snapshot_and_resets_lifecycle_state(self) -> None:
        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)
            scope.bind("workspace", SimpleContext(name="workspace", value=1))
            scope.mark_binding_lifecycle("workspace", is_prepared=True, in_lifecycle=True)

            fork = scope._hierarchy.fork()

            fork_binding = fork.get_binding("workspace")
            assert fork._parent_proxy is None
            assert fork.has_provider("default") is True
            assert fork_binding.context.value == 1
            assert fork_binding.is_prepared is False
            assert fork_binding.in_lifecycle is False

    def test_child_provider_lookup_tracks_live_parent_registry(self) -> None:
        with Scope(root=True) as parent:
            child = parent._hierarchy.child()
            provider = MockProvider()

            parent.register_provider("default", provider, default=True)

            assert child.has_provider("default") is True
            assert child.get_provider() is provider

    def test_fork_flattens_inherited_provider_state_into_local_snapshot(self) -> None:
        with Scope(root=True) as parent:
            inherited_provider = MockProvider()
            later_provider = MockProvider()
            parent.register_provider("default", inherited_provider, default=True)

            child = parent._hierarchy.child()
            fork = child._hierarchy.fork()

            parent.register_provider("later", later_provider)

            assert fork.get_provider() is inherited_provider
            assert fork.has_provider("later") is False

    def test_discard_cleans_sandboxes_and_preserves_provider_configuration(self) -> None:
        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)
            fork = scope._hierarchy.fork()
            sandbox = _MockSandbox("fork-sandbox")
            fork.register_sandbox(sandbox)
            fork.emit(Effect(effect_type="forked"))

            fork._hierarchy.discard()

            assert sandbox.cleaned_up is True
            assert fork.is_discarded is True
            assert len(fork.effects) == 0
            assert fork.has_provider("default") is True
