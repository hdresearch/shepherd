"""Tests for fork origin tracking — validates that parent_scope_id resolves
through fork boundaries for profiler call-tree reconstruction.

The key scenario: Orchestrator -> retry(parallel(TaskA, TaskB))
Without _origin_id, tasks inside forks have dangling parent_scope_id references.
With _origin_id, the full call tree is reconstructable.
"""

from __future__ import annotations

from shepherd_runtime.scope import Scope


class TestForkOriginTracking:
    """Verify _origin_id propagation through fork/child chains."""

    def test_fork_records_callers_scope_id(self) -> None:
        """fork() should record the calling scope's id as _origin_id."""
        with Scope(root=True) as root:
            fork = root.fork()
            assert fork._scope._origin_id == root.id

    def test_child_has_no_origin_id(self) -> None:
        """child() scopes should NOT have _origin_id (they have proper parent links)."""
        with Scope(root=True) as root:
            child = root.child()
            assert child._scope._origin_id is None

    def test_nested_fork_propagates_origin(self) -> None:
        """Forking a fork should propagate the original (non-fork) ancestor's id."""
        with Scope(root=True) as root:
            fork1 = root.fork()
            # fork1._origin_id = root.id
            assert fork1._scope._origin_id == root.id

            fork2 = fork1.fork()
            # fork2 should propagate fork1's origin, NOT fork1's own id
            assert fork2._scope._origin_id == root.id

    def test_fork_of_child_of_fork(self) -> None:
        """fork(child(fork)) — the common combinator nesting pattern.

        Scenario: retry creates fork1, parallel runs as child of fork1,
        then parallel creates fork2a for TaskA.
        """
        with Scope(root=True) as root:
            child = root.child()  # Simulates the retry combinator's scope

            fork1 = child.fork()  # retry's fork
            assert fork1._scope._origin_id == child.id

            # parallel runs as a child of fork1 (via _execute_sync -> child())
            parallel_child = fork1.child()
            assert parallel_child._scope._origin_id is None  # regular child

            # parallel creates fork2a for TaskA
            fork2a = parallel_child.fork()
            assert fork2a._scope._origin_id == parallel_child.id

            # TaskA runs as a child of fork2a
            taskA_child = fork2a.child()
            assert taskA_child._scope._origin_id is None  # regular child

    def test_parent_scope_id_resolution_through_fork(self) -> None:
        """Verify that the profiler's parent resolution logic works.

        Simulates what lifecycle.py does: if parent is a fork, use _origin_id.
        """
        with Scope(root=True) as root:
            retry_child = root.child()
            fork1 = retry_child.fork()
            parallel_child = fork1.child()
            fork2a = parallel_child.fork()
            taskA_child = fork2a.child()

            def resolve_parent(scope: Scope) -> str | None:
                """Same logic as lifecycle.py's parent_scope_id resolution."""
                parent_proxy = scope._parent_proxy
                if parent_proxy is not None:
                    origin = parent_proxy._scope._origin_id
                    return origin if origin is not None else parent_proxy.id
                return None

            # root -> no parent
            assert resolve_parent(root) is None

            # retry_child -> root (normal child, no fork involved)
            assert resolve_parent(retry_child) == root.id

            # parallel_child -> parent is fork1, which has _origin_id = retry_child.id
            assert resolve_parent(parallel_child) == retry_child.id

            # taskA_child -> parent is fork2a, which has _origin_id = parallel_child.id
            assert resolve_parent(taskA_child) == parallel_child.id

    def test_full_call_tree_reconstruction(self) -> None:
        """End-to-end: build the call tree from scope_id + parent_scope_id pairs.

        Verifies the tree: Orchestrator -> retry -> parallel -> TaskA, TaskB
        """
        with Scope(root=True) as root:
            # Orchestrator's scope
            orch = root.child()

            # retry combinator's scope (child of orch)
            retry_scope = orch.child()

            # retry's fork for the attempt
            fork1 = retry_scope.fork()

            # parallel combinator's scope (child of fork1)
            par_scope = fork1.child()

            # parallel's forks for each task
            fork_a = par_scope.fork()
            fork_b = par_scope.fork()

            # TaskA and TaskB scopes
            task_a = fork_a.child()
            task_b = fork_b.child()

            def resolve_parent(scope: Scope) -> str | None:
                parent_proxy = scope._parent_proxy
                if parent_proxy is not None:
                    origin = parent_proxy._scope._origin_id
                    return origin if origin is not None else parent_proxy.id
                return None

            # Build the tree: (scope_id, parent_scope_id, label) tuples
            tree = {
                "orch": (orch.id, resolve_parent(orch)),
                "retry": (retry_scope.id, resolve_parent(retry_scope)),
                "parallel": (par_scope.id, resolve_parent(par_scope)),
                "taskA": (task_a.id, resolve_parent(task_a)),
                "taskB": (task_b.id, resolve_parent(task_b)),
            }

            # Verify the complete chain
            assert tree["orch"][1] == root.id  # orch -> root
            assert tree["retry"][1] == orch.id  # retry -> orch
            assert tree["parallel"][1] == retry_scope.id  # parallel -> retry (through fork!)
            assert tree["taskA"][1] == par_scope.id  # taskA -> parallel (through fork!)
            assert tree["taskB"][1] == par_scope.id  # taskB -> parallel (through fork!)

            # Verify all parent_scope_id values resolve to existing scope_ids
            all_scope_ids = {root.id, orch.id, retry_scope.id, par_scope.id, task_a.id, task_b.id}
            for label, (_, parent_id) in tree.items():
                assert parent_id in all_scope_ids, f"{label}.parent_scope_id={parent_id} is a dangling reference"
