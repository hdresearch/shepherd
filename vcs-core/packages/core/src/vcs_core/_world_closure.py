"""Private recursive retention closure helpers for v2 world storage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pygit2

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._pygit2_helpers import require_blob, require_commit, require_tree
from vcs_core._transition_kernel_records import (
    HeadSelectionEvidence,
    HeadSelectionRecord,
    RetentionPolicyRequirement,
)
from vcs_core._world_refs import child_world_retention_ref
from vcs_core._world_types import WORLD_REF_SUBSTRATE_KIND, SubstrateHead, WorldCommit, WorldRefPayload

if TYPE_CHECKING:
    from collections.abc import Mapping

    from vcs_core._substrate_store import SubstrateStore
    from vcs_core._world_store import WorldStore


@dataclass(frozen=True)
class ClosureWorld:
    """One retained world in a root world's recursive closure."""

    oid: str
    path: str
    edge_kind: str
    binding: str | None = None
    retention_ref: str | None = None


@dataclass(frozen=True)
class ClosureHead:
    """One selected substrate head retained by a closure world."""

    world_oid: str
    path: str
    binding: str
    store_id: str
    head: str


@dataclass(frozen=True)
class ClosureEvidenceRef:
    """One concrete evidence ref cited by selection evidence in the closure."""

    world_oid: str
    path: str
    binding: str
    ref: str
    evidence_digest: str


@dataclass(frozen=True)
class WorldClosure:
    """The retained world graph and selected heads required by one root world.

    Evidence refs are integrity inputs for closure receipts. They are coordinator
    records, not selected-head reachability roots, so pinning a closure does not
    create separate retention refs for them.
    """

    root_world_oid: str
    worlds: tuple[ClosureWorld, ...]
    heads: tuple[ClosureHead, ...]
    evidence_refs: tuple[ClosureEvidenceRef, ...]


@dataclass(frozen=True)
class _PendingWorld:
    oid: str
    path: str
    edge_kind: str
    binding: str | None
    depth: int


def compute_world_closure(
    world_store: WorldStore,
    root_world_oid: str,
    bound_stores: Mapping[str, SubstrateStore],
    *,
    closure_mode: str = "semantic",
    max_depth: int | None = None,
    max_worlds: int = 100_000,
    include_git_parents: bool = False,
    create_retention_refs: bool = True,
) -> WorldClosure:
    """Compute retained worlds, heads, and evidence refs reachable from a root world."""
    if closure_mode not in {"semantic", "publish", "authority", "resume"}:
        raise ValueError(f"unsupported world closure mode: {closure_mode!r}")
    resolved_include_git_parents = include_git_parents
    resolved_include_input_worlds = closure_mode in {"publish", "authority", "resume"}
    resolved_include_root_input_world = closure_mode in {"authority", "resume"}
    builder = _ClosureBuilder(
        world_store=world_store,
        stores_by_id={store.identity.store_id: store for store in bound_stores.values()},
        root_world_oid=root_world_oid,
        max_depth=max_depth if max_depth is not None else max_worlds,
        max_worlds=max_worlds,
        include_git_parents=resolved_include_git_parents,
        create_retention_refs=create_retention_refs,
        include_input_worlds=resolved_include_input_worlds,
        include_root_input_world=resolved_include_root_input_world,
    )
    builder.build(root_world_oid)
    return builder.closure()


class _ClosureBuilder:
    def __init__(
        self,
        *,
        world_store: WorldStore,
        stores_by_id: Mapping[str, SubstrateStore],
        root_world_oid: str,
        max_depth: int,
        max_worlds: int,
        include_git_parents: bool,
        create_retention_refs: bool,
        include_input_worlds: bool,
        include_root_input_world: bool,
    ) -> None:
        self._world_store = world_store
        self._stores_by_id = dict(stores_by_id)
        self._root_world_oid = root_world_oid
        self._max_depth = max_depth
        self._max_worlds = max_worlds
        self._include_git_parents = include_git_parents
        self._create_retention_refs = create_retention_refs
        self._include_input_worlds = include_input_worlds
        self._include_root_input_world = include_root_input_world
        self._seen: set[str] = set()
        self._worlds: list[ClosureWorld] = []
        self._heads: list[ClosureHead] = []
        self._evidence_refs: list[ClosureEvidenceRef] = []

    def build(self, root_world_oid: str) -> None:
        pending = [_PendingWorld(root_world_oid, path="root", edge_kind="root", binding=None, depth=0)]
        while pending:
            current = pending.pop()
            next_worlds = self.visit(current)
            pending.extend(reversed(next_worlds))

    def closure(self) -> WorldClosure:
        return WorldClosure(
            root_world_oid=self._root_world_oid,
            worlds=tuple(self._worlds),
            heads=tuple(self._heads),
            evidence_refs=tuple(self._evidence_refs),
        )

    def visit(self, current: _PendingWorld) -> tuple[_PendingWorld, ...]:
        if current.depth > self._max_depth:
            raise InvalidRepositoryStateError("recursive world closure exceeded maximum depth")
        if current.oid in self._seen:
            return ()
        if len(self._seen) >= self._max_worlds:
            raise InvalidRepositoryStateError("recursive world closure exceeded maximum world count")
        self._seen.add(current.oid)
        world = self._world_store.read_world_commit(current.oid)
        self._worlds.append(
            ClosureWorld(
                oid=current.oid,
                path=current.path,
                edge_kind=current.edge_kind,
                binding=current.binding,
                retention_ref=(
                    None
                    if current.oid == self._root_world_oid
                    or not self._create_retention_refs
                    or current.edge_kind == "input_world"
                    else child_world_retention_ref(self._root_world_oid, current.path)
                ),
            )
        )
        for head in world.snapshot.heads:
            self._heads.append(
                ClosureHead(
                    world_oid=current.oid,
                    path=current.path,
                    binding=head.binding,
                    store_id=head.store_id,
                    head=head.head,
                )
            )
        selections = _selections_by_binding(world)
        self._record_evidence_refs(world, path=current.path)
        next_worlds = [
            *self._producer_world_edges(world, path=current.path, depth=current.depth),
            *self._retained_world_ref_edges(world, selections, path=current.path, depth=current.depth),
        ]
        if self._include_input_worlds and (self._include_root_input_world or current.edge_kind != "root"):
            next_worlds.extend(self._input_world_edges(world, path=current.path, depth=current.depth))
        if self._include_git_parents:
            next_worlds.extend(self._git_parent_edges(world, path=current.path, depth=current.depth))
        return tuple(next_worlds)

    def _input_world_edges(self, world: WorldCommit, *, path: str, depth: int) -> list[_PendingWorld]:
        input_world = world.transition.get("input_world")
        if input_world is None:
            return []
        if not isinstance(input_world, str) or not input_world:
            raise InvalidRepositoryStateError("world input_world edge is invalid")
        return [
            _PendingWorld(
                input_world,
                path=f"{path}.input_world",
                edge_kind="input_world",
                binding=None,
                depth=depth + 1,
            )
        ]

    def _git_parent_edges(self, world: WorldCommit, *, path: str, depth: int) -> list[_PendingWorld]:
        return [
            _PendingWorld(
                parent_oid,
                path=f"{path}.parent{index}",
                edge_kind="git_parent",
                binding=None,
                depth=depth + 1,
            )
            for index, parent_oid in enumerate(world.parent_oids)
        ]

    def _record_evidence_refs(self, world: WorldCommit, *, path: str) -> None:
        for evidence in _selection_evidence(world):
            for evidence_ref in evidence.evidence_refs:
                self._evidence_refs.append(
                    ClosureEvidenceRef(
                        world_oid=world.oid,
                        path=path,
                        binding=evidence.binding,
                        ref=evidence_ref.ref,
                        evidence_digest=evidence_ref.evidence_digest,
                    )
                )

    def _producer_world_edges(self, world: WorldCommit, *, path: str, depth: int) -> list[_PendingWorld]:
        outcomes = world.operation_final.get("candidate_outcomes", [])
        if not isinstance(outcomes, list):
            return []
        edges: list[_PendingWorld] = []
        for outcome in outcomes:
            if not isinstance(outcome, dict) or outcome.get("outcome") != "selected":
                continue
            producer_world_oid = outcome.get("producer_world_oid")
            binding = outcome.get("binding")
            if not isinstance(producer_world_oid, str) or not producer_world_oid:
                continue
            if not isinstance(binding, str) or not binding:
                raise InvalidRepositoryStateError("child-produced candidate outcome binding is required")
            edges.append(
                _PendingWorld(
                    producer_world_oid,
                    path=f"{path}.{binding}.producer",
                    edge_kind="producer_world",
                    binding=binding,
                    depth=depth + 1,
                )
            )
        return edges

    def _retained_world_ref_edges(
        self,
        world: WorldCommit,
        selections: Mapping[str, HeadSelectionRecord],
        *,
        path: str,
        depth: int,
    ) -> list[_PendingWorld]:
        heads = world.snapshot.by_binding()
        edges: list[_PendingWorld] = []
        for binding, selection in selections.items():
            requirements = tuple(
                requirement
                for requirement in selection.retention_policy_requirements
                if requirement.kind == "child-world-retention"
            )
            if not requirements:
                continue
            try:
                head = heads[binding]
            except KeyError as exc:
                raise InvalidRepositoryStateError("child-world retention binding is not selected") from exc
            payload = self._validated_world_ref_payload(head, requirements)
            edges.append(
                _PendingWorld(
                    payload.world_oid,
                    path=f"{path}.{binding}.world_ref",
                    edge_kind="world_ref",
                    binding=binding,
                    depth=depth + 1,
                )
            )
        return edges

    def _validated_world_ref_payload(
        self,
        head: SubstrateHead,
        requirements: tuple[RetentionPolicyRequirement, ...],
    ) -> WorldRefPayload:
        if head.kind != WORLD_REF_SUBSTRATE_KIND:
            raise InvalidRepositoryStateError("child-world retention requires a vcscore.world_ref selected head")
        if len(requirements) != 1:
            raise InvalidRepositoryStateError("child-world retention requires exactly one child-world-retention policy")
        requirement = requirements[0]
        if not requirement.target.startswith("world:"):
            raise InvalidRepositoryStateError("child-world retention target must be world:<oid>")
        try:
            store = self._stores_by_id[head.store_id]
        except KeyError as exc:
            raise InvalidRepositoryStateError("child-world retention selected head store is missing") from exc
        commit = require_commit(store.repo, pygit2.Oid(hex=head.head), context="world-ref substrate revision")
        tree = require_tree(store.repo, commit.tree.id, context="world-ref substrate revision tree")
        payload = WorldRefPayload.from_json(_read_json_blob(store.repo, tree, "revision.json"))
        if payload.world_store_id != self._world_store.world_store_id:
            raise InvalidRepositoryStateError("child-world retention world_store_id disagrees with coordinator")
        if requirement.target.removeprefix("world:") != payload.world_oid:
            raise InvalidRepositoryStateError("child-world retention target disagrees with world-ref payload")
        if requirement.digest != payload.snapshot_digest:
            raise InvalidRepositoryStateError("child-world retention digest disagrees with world-ref payload")
        referenced = self._world_store.read_world_commit(payload.world_oid)
        if referenced.snapshot.digest() != payload.snapshot_digest:
            raise InvalidRepositoryStateError("child-world retention snapshot digest disagrees with referenced world")
        return payload


def _selections_by_binding(world: WorldCommit) -> dict[str, HeadSelectionRecord]:
    selections: dict[str, HeadSelectionRecord] = {}
    raw = world.operation_final.get("head_selections", [])
    if not isinstance(raw, list):
        return selections
    for item in raw:
        if not isinstance(item, dict):
            continue
        selection = HeadSelectionRecord.from_json(item)
        selections[selection.binding] = selection
    return selections


def _selection_evidence(world: WorldCommit) -> tuple[HeadSelectionEvidence, ...]:
    raw = world.operation_final.get("selection_evidence", [])
    if not isinstance(raw, list):
        return ()
    evidences: list[HeadSelectionEvidence] = []
    for item in raw:
        if isinstance(item, dict):
            evidences.append(HeadSelectionEvidence.from_json(item))
    return tuple(evidences)


def _read_json_blob(repo: pygit2.Repository, tree: pygit2.Tree, path: str) -> dict[str, object]:
    obj: pygit2.Object = tree
    for component in path.split("/"):
        if not isinstance(obj, pygit2.Tree):
            raise TypeError(f"{path!r} did not resolve to a blob")
        obj = repo[obj[component].id]
    blob = require_blob(repo, obj.id, context=path)
    value = json.loads(bytes(blob.data).decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path!r} must contain a JSON object")
    return value
