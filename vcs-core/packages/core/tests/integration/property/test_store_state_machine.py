"""Property-based tests for vcs-core Store using Hypothesis stateful testing.

Generates random sequences of fork/merge/discard/_emit_effect operations
and verifies core invariants after every step:

  I1  -- Append-only: all previously observed commit OIDs remain reachable
  I3  -- Acyclic: topological walk encounters no repeated OIDs
  Stack discipline: stack never underflows; ground always at position 0
  No duplicate scope refs: no two live scope refs with the same name

Store.fork() is a low-level topology primitive; product live-child
admission is enforced by the VcsCore coordinator.
"""

from __future__ import annotations

import pygit2
import pytest
from hypothesis import settings
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)
from vcs_core.store import GROUND_REF, Store
from vcs_core.types import ScopeInfo

pytestmark = pytest.mark.slow


class VcsCoreStateMachine(RuleBasedStateMachine):
    """Stateful property-based test for Store operations.

    Maintains a scope stack mirroring the branch hierarchy and tracks
    all observed commit OIDs for invariant verification.
    """

    def __init__(self) -> None:
        super().__init__()
        self.store: Store | None = None
        self.scope_stack: list[tuple[ScopeInfo, str]] = []
        self.observed_oids: set[str] = set()
        self._name_counter: int = 0

    @initialize()
    def init_store(self) -> None:
        import tempfile

        self._tmpdir = tempfile.mkdtemp()
        repo_path = self._tmpdir + "/.vcscore"
        self.store = Store(repo_path)
        root_oid = self.store.create_root_commit()
        self.observed_oids.add(root_oid)
        ground = ScopeInfo(
            name="ground",
            ref=GROUND_REF,
            instance_id="ground",
            creation_oid="",
        )
        self.scope_stack = [(ground, "")]

    def _next_name(self) -> str:
        self._name_counter += 1
        return f"scope-{self._name_counter}"

    def _current_scope(self) -> ScopeInfo:
        return self.scope_stack[-1][0]

    def _current_ref(self) -> str:
        return self._current_scope().ref

    @rule()
    def fork(self) -> None:
        assert self.store is not None
        parent_ref = self._current_ref()
        name = self._next_name()
        scope = self.store.fork(parent_ref, name)
        self.scope_stack.append((scope, parent_ref))

    @rule()
    def merge(self) -> None:
        assert self.store is not None
        if len(self.scope_stack) <= 1:
            return
        scope, parent_ref = self.scope_stack[-1]
        oid = self.store.merge(scope, parent_ref)
        self.observed_oids.add(oid)
        self.scope_stack.pop()

    @rule()
    def discard(self) -> None:
        assert self.store is not None
        if len(self.scope_stack) <= 1:
            return
        scope, _parent_ref = self.scope_stack[-1]
        self.store.discard(scope)
        self.scope_stack.pop()

    @rule()
    def emit_effect_metadata_only(self) -> None:
        assert self.store is not None
        if len(self.scope_stack) <= 1:
            return
        scope = self._current_scope()
        oid = self.store._emit_effect(
            scope,
            "Marker",
            {"label": "test-marker"},
            substrate="marker",
        )
        self.observed_oids.add(oid)

    @rule()
    def emit_effect_with_file(self) -> None:
        assert self.store is not None
        if len(self.scope_stack) <= 1:
            return
        scope = self._current_scope()
        fname = f"file-{self._name_counter}.txt"
        oid = self.store._emit_effect(
            scope,
            "FileCreate",
            {"path": fname},
            workspace_changes=[(fname, b"content-" + fname.encode())],
            substrate="filesystem",
        )
        self.observed_oids.add(oid)

    @invariant()
    def i1_append_only(self) -> None:
        if self.store is None:
            return
        repo = self.store._repo

        reachable: set[str] = set()
        for ref_name in repo.references:
            tip = repo.references[ref_name].peel(pygit2.Commit)
            for commit in repo.walk(tip.id, pygit2.GIT_SORT_TOPOLOGICAL):
                reachable.add(str(commit.id))

        missing = self.observed_oids - reachable
        assert not missing, f"I1 violated: OIDs no longer reachable: {missing}"

    @invariant()
    def i3_no_repeated_oids_in_walk(self) -> None:
        if self.store is None:
            return
        repo = self.store._repo

        if GROUND_REF not in repo.references:
            return
        tip = repo.references[GROUND_REF].peel(pygit2.Commit)
        seen: set[str] = set()
        for commit in repo.walk(tip.id, pygit2.GIT_SORT_TOPOLOGICAL):
            oid = str(commit.id)
            assert oid not in seen, f"I3 violated: repeated OID {oid} in topological walk"
            seen.add(oid)

    @invariant()
    def i6b_stack_semantics(self) -> None:
        assert len(self.scope_stack) >= 1, "stack underflow"
        ground_scope, _ = self.scope_stack[0]
        assert ground_scope.name == "ground", f"position 0 is {ground_scope.name!r}, expected 'ground'"

    @invariant()
    def no_duplicate_scope_refs(self) -> None:
        if self.store is None:
            return
        repo = self.store._repo
        scope_refs = [r for r in repo.references if r.startswith("refs/vcscore/scopes/")]
        names = [r.split("/")[-1] for r in scope_refs]
        assert len(names) == len(set(names)), f"Duplicate scope refs detected: {names}"


TestStandard = VcsCoreStateMachine.TestCase
TestStandard.settings = settings(max_examples=200, stateful_step_count=30)


class TestStress(VcsCoreStateMachine.TestCase):  # type: ignore[misc]
    settings = settings(max_examples=50, stateful_step_count=80)
