"""Semantic layer: pure Git operations on a bare repository.

Store owns all pure Git operations on the bare repository. It accepts
declarative changesets from substrates, creates commits, manages branch
refs, and answers queries. It has no overlay knowledge. Returns library
DTOs, never pygit2 types.

Store._emit_effect() is the internal write primitive -- called only
by RecordingPipeline. Substrates and consumers do not call it
directly. The underscore prefix signals that this method is an
internal implementation detail, not a public or SPI surface.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pygit2

from vcs_core import _store_operation_queries, _store_workspace_queries
from vcs_core._errors import InvalidRepositoryStateError, MergePreconditionError, StaleScopeError
from vcs_core._identity import _new_world_id, initialize_ground_world_id
from vcs_core._operation_projection import (
    find_latest_matching_anchor,
    require_pointer_epoch_metadata,
)
from vcs_core._operation_projection import (
    operation_id_from_metadata as projected_operation_id_from_metadata,
)
from vcs_core._projection_store import (
    SCOPE_REGISTRY_CURRENT_REF,
    ScopeRegistryEntry,
    ScopeRegistryMismatch,
    ScopeRegistrySnapshot,
    ScopeRegistryStatus,
    load_scope_registry_snapshot,
    publish_scope_registry_snapshot,
    scope_registry_digest,
    scope_registry_frontier,
    scope_registry_mismatches,
)
from vcs_core._pygit2_helpers import topological_commits
from vcs_core._runtime_types import OperationRefInfo
from vcs_core._sibling_groups import (
    SiblingGroupListing,
    SiblingGroupRecord,
    SiblingGroupSnapshot,
    list_sibling_groups,
    load_sibling_group_snapshot,
    publish_sibling_group_snapshot,
    sibling_group_ref,
)
from vcs_core.git_store import (
    build_dual_tree,
    build_effect_meta_tree,
    build_tree,
    count_between,
    create_commit_with_recovery,
    create_or_update_reference,
    create_signature,
    read_effect_json,
)
from vcs_core.types import (
    ArchivedVia,
    CommitInfo,
    DiffSummary,
    OperationHistory,
    OperationSummary,
    OperationVisibility,
    RebaseResult,
    ScopeInfo,
    Status,
)

if TYPE_CHECKING:
    from vcs_core._projection_store import (
        ArchivedOperationCandidate,
        ArchivedOperationsByIdSnapshot,
        ProjectionCarrierKind,
    )

GROUND_REF = "refs/vcscore/ground"
MATERIALIZED_REF = "refs/vcscore/materialized"
OPERATION_REF_PREFIX = "refs/vcscore/ops"

_REPO_MUTATION_LOCKS_GUARD = threading.Lock()
_REPO_MUTATION_LOCKS: dict[str, threading.RLock] = {}


def _repo_mutation_lock(repo_path: str) -> threading.RLock:
    canonical_path = os.path.realpath(os.path.abspath(repo_path))
    with _REPO_MUTATION_LOCKS_GUARD:
        lock = _REPO_MUTATION_LOCKS.get(canonical_path)
        if lock is None:
            lock = threading.RLock()
            _REPO_MUTATION_LOCKS[canonical_path] = lock
        return lock


@dataclass(frozen=True)
class ArchivedOperationMembershipCacheRecord:
    """In-process archived-operation membership maintained by Store mutations."""

    frontier_digest: str
    archived_operation_ids: frozenset[str]


class Store:
    """Semantic layer: pure Git operations on a bare repository.

    No overlay knowledge. Accepts declarative changesets + metadata.
    Returns library DTOs, never pygit2 types.
    """

    GROUND_REF = GROUND_REF
    MAT_REF = MATERIALIZED_REF
    OP_REF_PREFIX = OPERATION_REF_PREFIX
    ArchivedOperationMembershipCache = ArchivedOperationMembershipCacheRecord

    def __init__(self, repo_path: str, *, repo: pygit2.Repository | None = None) -> None:
        self._repo_path = repo_path
        self._repo = repo or pygit2.init_repository(repo_path, bare=True)
        self._archived_operation_membership_cache: ArchivedOperationMembershipCacheRecord | None = None
        self._archived_operation_projection_cache: ArchivedOperationsByIdSnapshot | None = None
        self._mutation_lock = _repo_mutation_lock(repo_path)

    @classmethod
    def open_existing(cls, repo_path: str) -> Store:
        """Open an existing bare repository without creating filesystem state."""
        if not os.path.exists(repo_path):  # noqa: PTH110
            raise FileNotFoundError(repo_path)
        try:
            repo = pygit2.Repository(repo_path)
        except (pygit2.GitError, KeyError, ValueError) as exc:
            raise InvalidRepositoryStateError(
                f"{repo_path} is not an initialized vcs-core repository. Run `vcs-core init` first."
            ) from exc
        if not repo.is_bare:
            raise InvalidRepositoryStateError(
                f"{repo_path} is not a bare vcs-core repository. Run `vcs-core init` first."
            )
        return cls(repo_path, repo=repo)

    @property
    def repo_path(self) -> str:
        """Filesystem path to the bare repository state directory."""
        return self._repo_path

    @property
    def is_empty(self) -> bool:
        """True if the repository has no commits."""
        return GROUND_REF not in self._repo.references

    def create_root_commit(self) -> str:
        """Create empty dual-tree root commit on ground branch.

        Returns commit OID as hex string.
        """
        with self._mutation_lock:
            ws_tb = self._repo.TreeBuilder()
            ws_tree = ws_tb.write()

            effect_meta = {"type": "Init", "substrate": "vcscore", "scope": "ground", "timestamp": time.time()}
            meta_tree = build_effect_meta_tree(self._repo, effect_meta)
            root_tree = build_dual_tree(self._repo, ws_tree, meta_tree)

            sig = create_signature("init")
            oid = create_commit_with_recovery(self._repo, None, sig, sig, "effect:Init scope:ground\n", root_tree, [])
            create_or_update_reference(self._repo, GROUND_REF, oid)
            create_or_update_reference(self._repo, MATERIALIZED_REF, oid)
            initialize_ground_world_id(self._repo_path)
            self.initialize_scope_registry_projection()
            return str(oid)

    def initialize_scope_registry_projection(self) -> None:
        """Seed the empty scope-registry projection for a freshly initialized repo."""
        with self._mutation_lock:
            snapshot = load_scope_registry_snapshot(self._repo)
            if snapshot is not None:
                return
            if SCOPE_REGISTRY_CURRENT_REF in self._repo.references:
                raise InvalidRepositoryStateError(
                    "Scope registry projection exists but is unreadable; refusing to seed over corrupt control-plane state."
                )
            source_refs = scope_registry_frontier(self._repo)
            published = publish_scope_registry_snapshot(
                self._repo,
                expected_head_oid=None,
                expected_source_digest=scope_registry_digest(source_refs),
                source_refs=source_refs,
                entries=(),
            )
            if not published:
                raise InvalidRepositoryStateError("Failed to initialize the scope-registry projection.")

    # --- Graph operations ---

    def fork(self, parent_ref: str, name: str) -> ScopeInfo:
        """Create a child branch ref from parent's tip as a low-level primitive.

        Rejects names containing '/' (flat ref constraint).
        Returns ScopeInfo with name, ref, instance_id, creation_oid.
        Product lifecycle policy, including live-child admission, is enforced
        by the VcsCore coordinator rather than this primitive Store operation.
        """
        with self._mutation_lock:
            if "/" in name:
                msg = f"Scope name {name!r} contains '/'. Use '-' as separator (flat ref constraint)."
                raise ValueError(msg)
            parent_oid = self._repo.references[parent_ref].peel(pygit2.Commit).id
            ref = f"refs/vcscore/scopes/{name}"
            create_or_update_reference(self._repo, ref, parent_oid)
            instance_id = uuid.uuid4().hex[:12]
            return ScopeInfo(
                name=name,
                ref=ref,
                instance_id=instance_id,
                creation_oid=str(parent_oid),
                world_id=_new_world_id(),
            )

    @staticmethod
    def operation_ref(operation_id: str) -> str:
        """Return the stable ref path for one operation instance."""
        return f"{OPERATION_REF_PREFIX}/{operation_id}"

    @staticmethod
    def _operation_id_from_metadata(metadata: dict[str, Any]) -> str | None:
        """Return the durable operation id from the supported execution epoch."""
        return projected_operation_id_from_metadata(metadata)

    @staticmethod
    def _operation_timestamp(value: object) -> float | None:
        """Normalize metadata timestamps when present."""
        if isinstance(value, int | float):
            return float(value)
        return None

    def _commit_info(self, commit: pygit2.Commit) -> CommitInfo:
        """Convert a pygit2 commit to the public commit DTO."""
        return CommitInfo(
            oid=str(commit.id),
            message=commit.message.strip(),
            timestamp=commit.commit_time,
            metadata=read_effect_json(self._repo, commit),
            parent_oids=[str(p) for p in commit.parent_ids],
        )

    def _scope_name_for_ref(self, ref: str) -> str:
        """Return the logical scope name for a scope ref."""
        if ref == GROUND_REF:
            return "ground"
        prefix = "refs/vcscore/scopes/"
        if not ref.startswith(prefix):
            raise ValueError(f"Ref {ref!r} is not a scope ref.")
        return ref[len(prefix) :]

    def _validated_world_name_for_ref(self, ref: str, *, context: str) -> str:
        """Return a world display name or raise repository-state corruption."""
        try:
            return self._scope_name_for_ref(ref)
        except ValueError as exc:
            raise InvalidRepositoryStateError(
                f"Invalid repository state: {context} has invalid mg.world.ref {ref!r}."
            ) from exc

    @staticmethod
    def _reject_legacy_lifecycle_aliases(metadata: dict[str, Any]) -> None:
        legacy_aliases = (
            "op_id",
            "operation_id",
            "parent_operation_id",
            "world_id",
            "session_id",
            "scope_ref",
            "scope_instance_id",
            "parent_op_ref",
            "base_oid",
        )
        for alias in legacy_aliases:
            if alias in metadata:
                raise ValueError(
                    "Legacy top-level lifecycle metadata is no longer supported on public lifecycle writes; "
                    f"remove {alias!r} and let the store synthesize reserved execution state."
                )

    @staticmethod
    def _reject_reserved_lifecycle_metadata(metadata: dict[str, Any]) -> None:
        if "mg" in metadata:
            raise ValueError(
                "Reserved mg lifecycle metadata is store-owned on public lifecycle writes; "
                "remove 'mg' and pass payload metadata only."
            )

    @staticmethod
    def _build_operation_epoch_metadata(
        *,
        operation: OperationRefInfo,
        phase: str,
        seq: int,
        effect_count: int,
        prev_oid: str | None,
        started_at: float,
        closed_at: float | None = None,
        result: str | None = None,
    ) -> dict[str, Any]:
        operation_metadata: dict[str, Any] = {
            "id": operation.durable_id,
            "phase": phase,
            "seq": seq,
            "prev_oid": prev_oid,
            "kind": operation.kind,
            "label": operation.display_label,
            "effect_count": effect_count,
            "started_at": started_at,
        }
        if operation.parent_operation_id is not None:
            operation_metadata["parent_id"] = operation.parent_operation_id
        if operation.world_disposition is not None:
            operation_metadata["world_disposition"] = operation.world_disposition
        if operation.nested_parent_scope_ref is not None or operation.nested_child_scope_ref is not None:
            if (
                operation.nested_parent_scope_ref is None
                or operation.nested_child_scope_ref is None
                or not operation.nested_ancestry_chain
            ):
                raise ValueError("Nested operation metadata requires a complete parent/child edge.")
            operation_metadata["nested"] = {
                "parent_scope_ref": operation.nested_parent_scope_ref,
                "child_scope_ref": operation.nested_child_scope_ref,
                "ancestry_chain": list(operation.nested_ancestry_chain),
            }
        if closed_at is not None:
            operation_metadata["closed_at"] = closed_at
        if result is not None:
            operation_metadata["result"] = result

        if operation.world_id is None:
            raise ValueError("Operation world_id is required for lifecycle metadata.")
        mg_metadata: dict[str, Any] = {
            "version": 1,
            "world": {
                "id": operation.world_id,
                "ref": operation.scope_ref,
                "instance_id": operation.scope_instance_id,
            },
            "operation": operation_metadata,
        }
        if operation.session_id is not None:
            mg_metadata["session_id"] = operation.session_id
        return mg_metadata

    @staticmethod
    def _operation_epoch_metadata(
        *,
        metadata: dict[str, Any],
        operation: OperationRefInfo,
        phase: str,
        seq: int,
        effect_count: int,
        prev_oid: str | None,
        started_at: float,
        closed_at: float | None = None,
        result: str | None = None,
    ) -> dict[str, Any]:
        expected_mg = Store._build_operation_epoch_metadata(
            operation=operation,
            phase=phase,
            seq=seq,
            effect_count=effect_count,
            prev_oid=prev_oid,
            started_at=started_at,
            closed_at=closed_at,
            result=result,
        )
        return {
            **metadata,
            "mg": expected_mg,
        }

    def _current_operation_epoch_state(self, op: OperationRefInfo) -> tuple[str, int, int, float]:
        if op.ref not in self._repo.references:
            msg = f"Operation ref missing: {op.ref}"
            raise StaleScopeError(msg)
        anchor = find_latest_matching_anchor(
            self._repo,
            op.ref,
            expected_operation_id=op.durable_id,
            terminal_only=False,
        )
        if anchor is None:
            msg = f"Operation anchor missing for ref: {op.ref}"
            raise StaleScopeError(msg)
        metadata = read_effect_json(self._repo, anchor)
        require_pointer_epoch_metadata(metadata, context=f"operation ref {op.ref!r}")
        operation_metadata = metadata["mg"]["operation"]
        started_at = operation_metadata.get("started_at")
        seq = operation_metadata.get("seq")
        effect_count = operation_metadata.get("effect_count")
        if not isinstance(started_at, int | float) or not isinstance(seq, int) or not isinstance(effect_count, int):
            raise InvalidRepositoryStateError(
                f"Invalid repository state: operation ref {op.ref!r} is missing pointer-linked runtime metadata."
            )
        return str(anchor.id), seq, effect_count, float(started_at)

    def _emit_effect_to_ref(
        self,
        ref: str,
        *,
        scope_name: str,
        effect_type: str,
        metadata: dict[str, Any],
        workspace_changes: list[tuple[str, bytes | None] | tuple[str, bytes | None, int]] | None = None,
        substrate: str = "vcscore",
        author_name: str | None = None,
    ) -> str:
        """Create a C1 dual-tree commit on an arbitrary ref."""
        with self._mutation_lock:
            if ref not in self._repo.references:
                msg = f"Ref missing: {ref}"
                raise StaleScopeError(msg)

            parent = self._repo.references[ref].peel(pygit2.Commit)
            oid = self._build_effect_commit(
                parent,
                scope_name=scope_name,
                effect_type=effect_type,
                metadata=metadata,
                workspace_changes=workspace_changes,
                substrate=substrate,
                author_name=author_name,
            )
            create_or_update_reference(self._repo, ref, oid, force=True)
            return str(oid)

    def _build_effect_commit(
        self,
        parent: pygit2.Commit,
        *,
        scope_name: str,
        effect_type: str,
        metadata: dict[str, Any],
        workspace_changes: list[tuple[str, bytes | None] | tuple[str, bytes | None, int]] | None = None,
        substrate: str = "vcscore",
        author_name: str | None = None,
    ) -> pygit2.Oid:
        """Create a C1 dual-tree commit without publishing any refs."""
        if workspace_changes:
            ws_tree_oid = build_tree(self._repo, parent.tree["workspace"].id, workspace_changes)
        else:
            ws_tree_oid = parent.tree["workspace"].id

        effect_meta: dict[str, Any] = {
            **metadata,
            "type": effect_type,
            "substrate": substrate,
            "scope": scope_name,
            "timestamp": time.time(),
        }
        meta_tree_oid = build_effect_meta_tree(self._repo, effect_meta)
        root_tree_oid = build_dual_tree(self._repo, ws_tree_oid, meta_tree_oid)

        effect_json = json.dumps(effect_meta)
        message = f"effect:{effect_type} scope:{scope_name}\n\nMeta-Effect: {effect_json}\n"
        sig = create_signature(author_name or scope_name)
        return create_commit_with_recovery(self._repo, None, sig, sig, message, root_tree_oid, [parent.id])

    def _emit_effect(
        self,
        scope: ScopeInfo,
        effect_type: str,
        metadata: dict[str, Any],
        workspace_changes: list[tuple[str, bytes | None] | tuple[str, bytes | None, int]] | None = None,
        *,
        substrate: str = "vcscore",
    ) -> str:
        """Internal write primitive. Called only by RecordingPipeline.

        Substrates and consumers do not call this directly. Substrates
        produce EffectRecord descriptors; the RecordingPipeline records
        them by calling this method. Consumers interact via VcsCore's
        tree-shaped primitives (fork/merge/discard) and substrate-
        specific methods.

        Creates a C1 dual-tree commit on the scope's branch.
        Returns commit OID as hex string.
        """
        with self._mutation_lock:
            return self._emit_effect_to_ref(
                scope.ref,
                scope_name=scope.name,
                effect_type=effect_type,
                metadata=metadata,
                workspace_changes=workspace_changes,
                substrate=substrate,
                author_name=scope.name,
            )

    def begin_operation(
        self,
        scope_ref: str,
        *,
        handle_id: str,
        kind: str,
        world_id: str,
        scope_instance_id: str | None = None,
        parent_op_ref: str | None = None,
        operation_id: str | None = None,
        operation_label: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        world_disposition: str | None = None,
        nested_parent_scope_ref: str | None = None,
        nested_child_scope_ref: str | None = None,
        nested_ancestry_chain: tuple[str, ...] = (),
    ) -> OperationRefInfo:
        """Create an in-flight operation ref and record OperationStarted."""
        with self._mutation_lock:
            if "/" in handle_id:
                raise ValueError(f"Operation handle {handle_id!r} contains '/'.")
            if not world_id:
                raise ValueError("world_id is required for operation refs.")
            base_ref = parent_op_ref or scope_ref
            if base_ref not in self._repo.references:
                msg = f"Ref missing: {base_ref}"
                raise StaleScopeError(msg)

            scope_name = self._scope_name_for_ref(scope_ref)
            base_oid = str(self._repo.references[base_ref].peel(pygit2.Commit).id)
            resolved_scope_instance_id = scope_instance_id or ("ground" if scope_ref == GROUND_REF else None)
            if resolved_scope_instance_id is None:
                raise ValueError("scope_instance_id is required for non-ground operation refs.")
            begin_metadata = dict(metadata or {})
            self._reject_legacy_lifecycle_aliases(begin_metadata)
            self._reject_reserved_lifecycle_metadata(begin_metadata)
            if "nested" in begin_metadata:
                raise ValueError("Reserved nested operation metadata is store-owned; remove 'nested'.")
            if "world_disposition" in begin_metadata:
                raise ValueError("world_disposition must be passed through Store.begin_operation typed keywords.")
            if world_disposition is not None and world_disposition not in {"adopt", "release"}:
                raise ValueError("world_disposition must be 'adopt' or 'release'.")
            has_nested_edge = nested_parent_scope_ref is not None or nested_child_scope_ref is not None
            if has_nested_edge and (
                nested_parent_scope_ref is None or nested_child_scope_ref is None or not nested_ancestry_chain
            ):
                raise ValueError("Nested operation metadata requires parent scope, child scope, and ancestry chain.")
            if world_disposition is not None and not has_nested_edge:
                raise ValueError("world_disposition requires nested operation metadata.")
            started_at = time.time()
            resolved_operation_id = operation_id or handle_id
            resolved_operation_label = operation_label or handle_id
            parent_operation_id = None
            if parent_operation_id is None and parent_op_ref is not None:
                parent_start = self._read_operation_start_metadata(parent_op_ref)
                parent_operation_id = str(self._operation_id_from_metadata(parent_start) or "")
                if parent_operation_id == "":
                    parent_operation_id = None
            ref = self.operation_ref(resolved_operation_id)

            op = OperationRefInfo(
                handle_id=handle_id,
                kind=kind,
                ref=ref,
                scope_ref=scope_ref,
                scope_instance_id=resolved_scope_instance_id,
                parent_op_ref=parent_op_ref,
                base_oid=base_oid,
                session_id=session_id,
                operation_id=resolved_operation_id,
                parent_operation_id=parent_operation_id,
                operation_label=resolved_operation_label,
                world_id=world_id,
                world_disposition=world_disposition,
                nested_parent_scope_ref=nested_parent_scope_ref,
                nested_child_scope_ref=nested_child_scope_ref,
                nested_ancestry_chain=tuple(nested_ancestry_chain),
            )
            start_metadata: dict[str, Any] = dict(begin_metadata)
            start_metadata = self._operation_epoch_metadata(
                metadata=start_metadata,
                operation=op,
                phase="started",
                seq=0,
                effect_count=0,
                prev_oid=None,
                started_at=started_at,
            )
            create_or_update_reference(self._repo, ref, pygit2.Oid(hex=base_oid))
            self._emit_effect_to_ref(
                op.ref,
                scope_name=scope_name,
                effect_type="OperationStarted",
                metadata=start_metadata,
                substrate="vcscore",
                author_name=scope_name,
            )
            return op

    def append_operation_effect(
        self,
        op: OperationRefInfo,
        effect_type: str,
        metadata: dict[str, Any],
        workspace_changes: list[tuple[str, bytes | None] | tuple[str, bytes | None, int]] | None = None,
        *,
        substrate: str,
    ) -> str:
        """Append an effect to an in-flight operation ref."""
        with self._mutation_lock:
            prev_oid, seq, effect_count, started_at = self._current_operation_epoch_state(op)
            raw_metadata = dict(metadata)
            self._reject_legacy_lifecycle_aliases(raw_metadata)
            self._reject_reserved_lifecycle_metadata(raw_metadata)
            effect_metadata: dict[str, Any] = dict(raw_metadata)
            effect_metadata = self._operation_epoch_metadata(
                metadata=effect_metadata,
                operation=op,
                phase="effect",
                seq=seq + 1,
                effect_count=effect_count + 1,
                prev_oid=prev_oid,
                started_at=started_at,
            )
            return self._emit_effect_to_ref(
                op.ref,
                scope_name=self._scope_name_for_ref(op.scope_ref),
                effect_type=effect_type,
                metadata=effect_metadata,
                workspace_changes=workspace_changes,
                substrate=substrate,
                author_name=self._scope_name_for_ref(op.scope_ref),
            )

    def _validate_operation_target_scope(self, op: OperationRefInfo, scope: ScopeInfo) -> None:
        """Reject stale scope handles when finalizing a root operation."""
        if scope.ref != op.scope_ref or scope.instance_id != op.scope_instance_id:
            msg = (
                f"Operation handle {op.handle_id!r} belongs to scope instance "
                f"{op.scope_instance_id!r}, not {scope.instance_id!r}."
            )
            raise StaleScopeError(msg)

    def _finalize_operation_into_ref(
        self,
        op: OperationRefInfo,
        target_ref: str,
        *,
        metadata: dict[str, Any] | None = None,
        status: str = "ok",
    ) -> str:
        with self._mutation_lock:
            if target_ref not in self._repo.references:
                msg = f"Target ref missing: {target_ref}"
                raise StaleScopeError(msg)
            if op.ref not in self._repo.references:
                msg = f"Operation ref missing: {op.ref}"
                raise StaleScopeError(msg)

            target_oid = str(self._repo.references[target_ref].peel(pygit2.Commit).id)
            if target_oid != op.base_oid:
                msg = (
                    f"Target ref {target_ref} advanced while operation handle {op.handle_id!r} was open. "
                    f"Expected base {op.base_oid[:12]}, found {target_oid[:12]}."
                )
                raise MergePreconditionError(msg)

            prev_oid, seq, effect_count, started_at = self._current_operation_epoch_state(op)
            raw_metadata = dict(metadata or {})
            self._reject_legacy_lifecycle_aliases(raw_metadata)
            self._reject_reserved_lifecycle_metadata(raw_metadata)
            completion_metadata: dict[str, Any] = dict(raw_metadata)
            closed_at = time.time()
            completion_metadata = self._operation_epoch_metadata(
                metadata=completion_metadata,
                operation=op,
                phase="completed",
                seq=seq + 1,
                effect_count=effect_count,
                prev_oid=prev_oid,
                started_at=started_at,
                closed_at=closed_at,
                result=status,
            )
            self._emit_effect_to_ref(
                op.ref,
                scope_name=self._scope_name_for_ref(op.scope_ref),
                effect_type="OperationCompleted",
                metadata=completion_metadata,
                substrate="vcscore",
                author_name=self._scope_name_for_ref(op.scope_ref),
            )
            tip = self._repo.references[op.ref].peel(pygit2.Commit).id
            create_or_update_reference(self._repo, target_ref, tip, force=True)
            self._repo.references.delete(op.ref)
            return str(tip)

    def finalize_operation(
        self,
        op: OperationRefInfo,
        *,
        scope: ScopeInfo | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "ok",
    ) -> str:
        """Close an operation and fast-forward it into its parent or scope ref."""
        with self._mutation_lock:
            if op.parent_op_ref is None and scope is not None:
                self._validate_operation_target_scope(op, scope)
            if op.parent_op_ref is None and scope is None and op.scope_ref != GROUND_REF:
                raise ValueError("Root operation finalization requires the live ScopeInfo target.")
            target_ref = op.parent_op_ref or (scope.ref if scope is not None else op.scope_ref)
            return self._finalize_operation_into_ref(op, target_ref, metadata=metadata, status=status)

    def complete_operation_to_archive(
        self,
        op: OperationRefInfo,
        *,
        metadata: dict[str, Any] | None = None,
        status: str = "ok",
    ) -> str:
        """Complete an operation ref and archive it without advancing a world ref."""
        with self._mutation_lock:
            if op.ref not in self._repo.references:
                msg = f"Operation ref missing: {op.ref}"
                raise StaleScopeError(msg)

            membership_cache = self._fresh_archived_operation_membership_cache()
            projection = self._fresh_archived_operation_projection()
            prev_oid, seq, effect_count, started_at = self._current_operation_epoch_state(op)
            raw_metadata = dict(metadata or {})
            self._reject_legacy_lifecycle_aliases(raw_metadata)
            self._reject_reserved_lifecycle_metadata(raw_metadata)
            completion_metadata = self._operation_epoch_metadata(
                metadata=raw_metadata,
                operation=op,
                phase="completed",
                seq=seq + 1,
                effect_count=effect_count,
                prev_oid=prev_oid,
                started_at=started_at,
                closed_at=time.time(),
                result=status,
            )
            parent = self._repo.references[op.ref].peel(pygit2.Commit)
            terminal_oid = self._build_effect_commit(
                parent,
                scope_name=self._scope_name_for_ref(op.scope_ref),
                effect_type="OperationCompleted",
                metadata=completion_metadata,
                substrate="vcscore",
                author_name=self._scope_name_for_ref(op.scope_ref),
            )
            archive_ref = f"refs/vcscore/archive/ops/{op.durable_id}"
            if archive_ref in self._repo.references:
                msg = f"Archived operation ref already exists for durable operation_id {op.durable_id!r}."
                raise InvalidRepositoryStateError(msg)
            create_or_update_reference(self._repo, archive_ref, terminal_oid)
            self._repo.references.delete(op.ref)
            self._record_archived_membership_additions(
                membership_cache,
                added_operation_ids=frozenset((op.durable_id,)),
            )
            self._record_archived_operation_projection_additions(
                projection,
                carrier_ref=archive_ref,
                carrier_kind="archived_operation_ref",
                operation_ids=frozenset((op.durable_id,)),
            )
            return archive_ref

    def abort_operation(
        self,
        op: OperationRefInfo,
        *,
        metadata: dict[str, Any] | None = None,
        status: str = "error",
    ) -> str:
        """Abort an in-flight operation and archive its ref.

        Aborted operations remain durable for recovery/debugging but do not
        advance parent or world-visible history.
        """
        with self._mutation_lock:
            if op.ref not in self._repo.references:
                msg = f"Operation ref missing: {op.ref}"
                raise StaleScopeError(msg)

            prev_oid, seq, effect_count, started_at = self._current_operation_epoch_state(op)
            raw_metadata = dict(metadata or {})
            self._reject_legacy_lifecycle_aliases(raw_metadata)
            self._reject_reserved_lifecycle_metadata(raw_metadata)
            abort_metadata: dict[str, Any] = dict(raw_metadata)
            closed_at = time.time()
            abort_metadata = self._operation_epoch_metadata(
                metadata=abort_metadata,
                operation=op,
                phase="aborted",
                seq=seq + 1,
                effect_count=effect_count,
                prev_oid=prev_oid,
                started_at=started_at,
                closed_at=closed_at,
                result=status,
            )

            self._emit_effect_to_ref(
                op.ref,
                scope_name=self._scope_name_for_ref(op.scope_ref),
                effect_type="OperationAborted",
                metadata=abort_metadata,
                substrate="vcscore",
                author_name=self._scope_name_for_ref(op.scope_ref),
            )
            return self.archive_operation(op)

    def archive_operation(self, op: OperationRefInfo) -> str:
        """Archive an in-flight operation ref. Returns archive ref path."""
        with self._mutation_lock:
            if op.ref not in self._repo.references:
                msg = f"Operation ref missing: {op.ref}"
                raise StaleScopeError(msg)

            membership_cache = self._fresh_archived_operation_membership_cache()
            projection = self._fresh_archived_operation_projection()
            tip = self._repo.references[op.ref].peel(pygit2.Commit).id
            archive_ref = f"refs/vcscore/archive/ops/{op.durable_id}"
            if archive_ref in self._repo.references:
                msg = f"Archived operation ref already exists for durable operation_id {op.durable_id!r}."
                raise InvalidRepositoryStateError(msg)
            create_or_update_reference(self._repo, archive_ref, tip)
            self._repo.references.delete(op.ref)
            self._record_archived_membership_additions(
                membership_cache,
                added_operation_ids=frozenset((op.durable_id,)),
            )
            self._record_archived_operation_projection_additions(
                projection,
                carrier_ref=archive_ref,
                carrier_kind="archived_operation_ref",
                operation_ids=frozenset((op.durable_id,)),
            )
            return archive_ref

    def merge(self, scope: ScopeInfo, parent_ref: str) -> str:
        """Fast-forward parent ref to scope's tip. Delete scope ref.

        Returns merged commit OID as hex string.
        """
        with self._mutation_lock:
            self.assert_mergeable(scope, parent_ref)
            tip = self._repo.references[scope.ref].peel(pygit2.Commit).id
            create_or_update_reference(self._repo, parent_ref, tip, force=True)
            self._repo.references.delete(scope.ref)
            return str(tip)

    def assert_mergeable(self, scope: ScopeInfo, parent_ref: str) -> None:
        """Validate that scope can be fast-forward merged into parent_ref."""
        if scope.ref not in self._repo.references:
            msg = f"Scope ref missing: {scope.ref}"
            raise StaleScopeError(msg)

        parent = self._repo.references[parent_ref].peel(pygit2.Commit)
        parent_oid = str(parent.id)

        if parent_oid == scope.creation_oid:
            return

        if self._is_ancestor(scope.creation_oid, parent_oid):
            # Parent advanced past our fork point, but fork point is
            # still an ancestor. Current product policy requires creating
            # a fresh child from the current parent rather than rebasing.
            msg = (
                f"Parent ref {parent_ref} advanced past fork point "
                f"({scope.creation_oid[:12]}→{parent_oid[:12]}). "
                f"Scope {scope.name!r} was created from an older parent tip and cannot be merged "
                "under the sequential live-child policy. Discard it and create a fresh child from "
                "the current parent."
            )
            raise MergePreconditionError(msg)

        # Parent diverged in an unexpected way
        msg = (
            f"Parent ref {parent_ref} diverged unexpectedly from fork point. "
            f"Expected {scope.creation_oid[:12]}, found {parent_oid[:12]}. "
            f"This may indicate external ref mutation."
        )
        raise MergePreconditionError(msg)

    def _is_ancestor(self, ancestor_oid: str, descendant_oid: str) -> bool:
        """True if ancestor_oid is reachable from descendant_oid."""
        return any(str(commit.id) == ancestor_oid for commit in topological_commits(self._repo, descendant_oid))

    def discard(self, scope: ScopeInfo) -> str:
        """Archive scope ref. Returns archive ref path."""
        with self._mutation_lock:
            if scope.ref not in self._repo.references:
                msg = f"Scope ref missing: {scope.ref}"
                raise StaleScopeError(msg)

            membership_cache = self._fresh_archived_operation_membership_cache()
            projection = self._fresh_archived_operation_projection()
            should_enumerate_scope = membership_cache is not None or projection is not None
            added_operation_ids = (
                self._exact_operation_ids_on_committed_carrier(scope.ref) if should_enumerate_scope else frozenset()
            )
            tip = self._repo.references[scope.ref].peel(pygit2.Commit).id
            archive_ref = f"refs/vcscore/archive/{scope.name}-{scope.instance_id}"
            create_or_update_reference(self._repo, archive_ref, tip)
            self._repo.references.delete(scope.ref)
            self._record_archived_membership_additions(
                membership_cache,
                added_operation_ids=added_operation_ids,
            )
            self._record_archived_operation_projection_additions(
                projection,
                carrier_ref=archive_ref,
                carrier_kind="discarded_world_ref",
                operation_ids=added_operation_ids,
            )
            return archive_ref

    def list_operation_refs(self) -> list[str]:
        return _store_operation_queries.list_operation_refs(self)

    def _read_operation_start_commit(self, ref: str) -> pygit2.Commit:
        return _store_operation_queries.read_operation_start_commit(self, ref)

    def _read_operation_start_metadata(self, ref: str) -> dict[str, Any]:
        return _store_operation_queries.read_operation_start_metadata(self, ref)

    def list_open_operations(
        self,
        *,
        scope_ref: str | None = None,
        session_id: str | None = None,
    ) -> list[OperationRefInfo]:
        return _store_operation_queries.list_open_operations(self, scope_ref=scope_ref, session_id=session_id)

    def list_operation_archive_refs(
        self,
        *,
        world_id: str | None = None,
        operation_id: str | None = None,
    ) -> list[str]:
        return _store_operation_queries.list_operation_archive_refs(
            self,
            world_id=world_id,
            operation_id=operation_id,
        )

    def _operation_summary_from_projection(
        self,
        *,
        projection: Any,
        visibility: OperationVisibility,
        carrier_ref: str,
        archived_via: ArchivedVia | None = None,
    ) -> OperationSummary:
        return _store_operation_queries.operation_summary_from_projection(
            self,
            projection=projection,
            visibility=visibility,
            carrier_ref=carrier_ref,
            archived_via=archived_via,
        )

    @staticmethod
    def _operation_summary_sort_key(summary: OperationSummary) -> float:
        return _store_operation_queries.operation_summary_sort_key(summary)

    @staticmethod
    def _raise_duplicate_visible_operation_id(ref: str, operation_id: str) -> None:
        _store_operation_queries.raise_duplicate_visible_operation_id(ref, operation_id)

    def read_operation_history(self, ref: str) -> OperationHistory:
        return _store_operation_queries.read_operation_history(self, ref)

    def _completed_operation_commits_on_ref(self, ref: str) -> list[pygit2.Commit]:
        return _store_operation_queries.completed_operation_commits_on_ref(self, ref)

    def _read_committed_operation_history(
        self,
        ref: str,
        *,
        operation_id: str,
        visibility: OperationVisibility,
        archived_via: ArchivedVia | None = None,
    ) -> OperationHistory:
        return _store_operation_queries.read_committed_operation_history(
            self,
            ref,
            operation_id=operation_id,
            visibility=visibility,
            archived_via=archived_via,
        )

    def read_visible_operation_history(
        self,
        ref: str,
        *,
        operation_id: str,
    ) -> OperationHistory:
        return _store_operation_queries.read_visible_operation_history(self, ref, operation_id=operation_id)

    def read_discarded_world_operation_history(
        self,
        ref: str,
        *,
        operation_id: str,
    ) -> OperationHistory:
        return _store_operation_queries.read_discarded_world_operation_history(
            self,
            ref,
            operation_id=operation_id,
        )

    def _summaries_from_committed_carrier(
        self,
        ref: str,
        *,
        visibility: OperationVisibility,
        max_count: int,
        archived_via: ArchivedVia | None = None,
    ) -> list[OperationSummary]:
        return _store_operation_queries.summaries_from_committed_carrier(
            self,
            ref,
            visibility=visibility,
            max_count=max_count,
            archived_via=archived_via,
        )

    def open_operations(
        self,
        *,
        scope_ref: str | None = None,
        session_id: str | None = None,
    ) -> list[OperationSummary]:
        return _store_operation_queries.open_operations(self, scope_ref=scope_ref, session_id=session_id)

    def committed_carrier_operations(
        self,
        ref: str,
        *,
        max_count: int = 50,
    ) -> list[OperationSummary]:
        return _store_operation_queries.committed_carrier_operations(self, ref, max_count=max_count)

    def archived_recovery_operations(
        self,
        *,
        max_count: int = 50,
        world_id: str | None = None,
        operation_id: str | None = None,
    ) -> list[OperationSummary]:
        return _store_operation_queries.archived_recovery_operations(
            self,
            max_count=max_count,
            world_id=world_id,
            operation_id=operation_id,
        )

    def archived_operations(
        self,
        *,
        max_count: int = 50,
        world_id: str | None = None,
        operation_id: str | None = None,
    ) -> list[OperationSummary]:
        return _store_operation_queries.archived_operations(
            self,
            max_count=max_count,
            world_id=world_id,
            operation_id=operation_id,
        )

    def _canonical_archived_operations(
        self,
        *,
        max_count: int,
        world_id: str | None,
        operation_id: str | None,
    ) -> list[OperationSummary]:
        return _store_operation_queries.canonical_archived_operations(
            self,
            max_count=max_count,
            world_id=world_id,
            operation_id=operation_id,
        )

    def visible_operations(self, ref: str | None = None, *, max_count: int = 50) -> list[OperationSummary]:
        return _store_operation_queries.visible_operations(self, ref=ref, max_count=max_count)

    def operation_id_exists(self, operation_id: str) -> bool:
        return _store_operation_queries.operation_id_exists(self, operation_id)

    def _archived_operation_id_exists_in_current_process(self, operation_id: str) -> bool:
        return _store_operation_queries.archived_operation_id_exists_in_current_process(self, operation_id)

    def _fresh_archived_operation_membership_cache(self) -> ArchivedOperationMembershipCacheRecord | None:
        return _store_operation_queries.fresh_archived_operation_membership_cache(self)

    def _build_archived_operation_membership_cache(self) -> ArchivedOperationMembershipCacheRecord:
        return _store_operation_queries.build_archived_operation_membership_cache(self)

    def _record_archived_membership_additions(
        self,
        previous_cache: ArchivedOperationMembershipCacheRecord | None,
        *,
        added_operation_ids: frozenset[str],
    ) -> None:
        _store_operation_queries.record_archived_membership_additions(
            self,
            previous_cache,
            added_operation_ids=added_operation_ids,
        )

    def _exact_operation_ids_on_committed_carrier(self, ref: str) -> frozenset[str]:
        return _store_operation_queries.exact_operation_ids_on_committed_carrier(self, ref)

    def _fresh_archived_operation_projection(self) -> ArchivedOperationsByIdSnapshot | None:
        return _store_operation_queries.fresh_archived_operation_projection(self)

    def _validated_projected_archived_operation_summary(
        self,
        *,
        operation_id: str,
        world_id: str | None,
    ) -> OperationSummary | None:
        return _store_operation_queries.validated_projected_archived_operation_summary(
            self,
            operation_id=operation_id,
            world_id=world_id,
        )

    def _build_archived_operation_projection_entries(self) -> tuple[ArchivedOperationCandidate, ...]:
        return _store_operation_queries.build_archived_operation_projection_entries(self)

    def _publish_archived_operation_projection(self) -> bool:
        with self._mutation_lock:
            return _store_operation_queries.publish_archived_operation_projection(self)

    def _record_archived_operation_projection_additions(
        self,
        previous_projection: ArchivedOperationsByIdSnapshot | None,
        *,
        carrier_ref: str,
        carrier_kind: ProjectionCarrierKind,
        operation_ids: frozenset[str],
    ) -> bool:
        with self._mutation_lock:
            return _store_operation_queries.record_archived_operation_projection_additions(
                self,
                previous_projection,
                carrier_ref=carrier_ref,
                carrier_kind=carrier_kind,
                operation_ids=operation_ids,
            )

    def _refresh_archived_operation_projection(self) -> bool:
        with self._mutation_lock:
            return _store_operation_queries.refresh_archived_operation_projection(self)

    def load_scope_registry_projection(self) -> ScopeRegistrySnapshot | None:
        """Return the current scope-registry projection snapshot when readable."""
        return load_scope_registry_snapshot(self._repo)

    def require_scope_registry_projection(self) -> ScopeRegistrySnapshot:
        """Return the required scope-registry projection for the current control-plane epoch."""
        snapshot = self.load_scope_registry_projection()
        if snapshot is not None:
            return snapshot
        if SCOPE_REGISTRY_CURRENT_REF in self._repo.references:
            raise InvalidRepositoryStateError(
                "Scope registry projection is unreadable or version-mismatched for the current control-plane epoch."
            )
        raise InvalidRepositoryStateError("Scope registry projection is missing for the current control-plane epoch.")

    @staticmethod
    def scope_info_from_registry_entry(entry: ScopeRegistryEntry) -> ScopeInfo:
        """Convert one scope-registry entry into the public scope handle DTO."""
        return ScopeInfo(
            name=entry.name,
            ref=entry.ref,
            instance_id=entry.instance_id,
            creation_oid=entry.creation_oid,
            world_id=entry.world_id,
        )

    def scope_registry_entries(
        self,
        *,
        status: ScopeRegistryStatus | None = None,
    ) -> tuple[ScopeRegistryEntry, ...]:
        """Return scope-registry entries, optionally filtered by lifecycle status."""
        snapshot = self.load_scope_registry_projection()
        if snapshot is None:
            return ()
        if status is None:
            return snapshot.entries
        return tuple(entry for entry in snapshot.entries if entry.status == status)

    def scope_registry_entry(
        self,
        name: str,
        *,
        status: ScopeRegistryStatus | None = None,
    ) -> ScopeRegistryEntry | None:
        """Look up one scope-registry entry by logical name."""
        snapshot = self.load_scope_registry_projection()
        if snapshot is None:
            return None
        entry = snapshot.entries_by_name.get(name)
        if entry is None:
            return None
        if status is not None and entry.status != status:
            return None
        return entry

    def scope_registry_projection_mismatches(self) -> tuple[ScopeRegistryMismatch, ...]:
        """Classify allowed scope-registry mismatch states against live scope refs."""
        return scope_registry_mismatches(self._repo)

    def publish_scope_registry_projection(
        self,
        *,
        entries: tuple[ScopeRegistryEntry, ...],
        expected_head_oid: str | None,
        expected_source_digest: str | None = None,
    ) -> bool:
        """Publish a scope-registry projection over the current live-scope frontier."""
        with self._mutation_lock:
            source_refs = scope_registry_frontier(self._repo)
            source_digest = scope_registry_digest(source_refs)
            return publish_scope_registry_snapshot(
                self._repo,
                expected_head_oid=expected_head_oid,
                expected_source_digest=source_digest if expected_source_digest is None else expected_source_digest,
                source_refs=source_refs,
                entries=entries,
            )

    # --- Deferred sibling-group recovery refs ---

    @staticmethod
    def sibling_group_ref(group_id: str) -> str:
        """Return the internal control ref for one sibling group."""
        return sibling_group_ref(group_id)

    def load_sibling_group(self, group_id: str) -> SiblingGroupSnapshot | None:
        """Load one deferred sibling-group record, if its control ref exists."""
        with self._mutation_lock:
            return load_sibling_group_snapshot(self._repo, group_id)

    def _publish_sibling_group_for_recovery_test(
        self,
        record: SiblingGroupRecord,
        *,
        expected_head_oid: str | None,
    ) -> bool:
        """Publish one deferred sibling-group record for recovery tests only."""
        with self._mutation_lock:
            return publish_sibling_group_snapshot(self._repo, record, expected_head_oid=expected_head_oid)

    def list_sibling_groups(self) -> SiblingGroupListing:
        """List readable and unreadable deferred sibling-group control refs."""
        with self._mutation_lock:
            return list_sibling_groups(self._repo)

    # --- Materialization bookkeeping ---

    def advance_materialized(self) -> None:
        """Advance materialized ref to ground tip."""
        with self._mutation_lock:
            ground_oid = self._repo.references[GROUND_REF].peel(pygit2.Commit).id
            create_or_update_reference(self._repo, MATERIALIZED_REF, ground_oid, force=True)

    def reset_ground_to_materialized(self) -> int:
        """Rewind ground to materialized. Archive old ground tip.

        Returns number of commits discarded.
        """
        with self._mutation_lock:
            materialized = self._repo.references[MATERIALIZED_REF].peel(pygit2.Commit)
            head = self._repo.references[GROUND_REF].peel(pygit2.Commit)
            if materialized.id == head.id:
                return 0
            ahead = count_between(self._repo, materialized, head)
            archive_ref = f"refs/vcscore/archive/ground-reset-{int(time.time())}"
            create_or_update_reference(self._repo, archive_ref, head.id)
            create_or_update_reference(self._repo, GROUND_REF, materialized.id, force=True)
            return ahead

    # --- Queries ---

    def walk_pending(self, max_count: int = 500) -> list[CommitInfo]:
        """Walk commits between materialized and ground (oldest first).

        Returns commits in causal order (oldest to newest), which is the
        natural order for intent collection and materialization planning.
        """
        return _store_workspace_queries.walk_pending(self, max_count=max_count)

    def status(self) -> Status:
        """Materialization status: local changes and commits ahead."""
        return _store_workspace_queries.status(self)

    def log(self, ref: str | None = None, max_count: int = 50) -> list[CommitInfo]:
        """Commit history from the given ref (default: ground)."""
        return _store_workspace_queries.log(self, ref=ref, max_count=max_count)

    def diff(self) -> DiffSummary:
        """File changes between materialized and ground."""
        return _store_workspace_queries.diff(self)

    def filter_effects(
        self,
        effect_type: str | None = None,
        substrate: str | None = None,
        ref: str | None = None,
        max_count: int = 100,
        scope: str | None = None,
    ) -> list[CommitInfo]:
        """Filter commits by effect type, substrate, and/or scope.

        Walks the full history until max_count matches are found or
        history is exhausted. No heuristic multiplier.
        """
        return _store_workspace_queries.filter_effects(
            self,
            effect_type=effect_type,
            substrate=substrate,
            ref=ref,
            max_count=max_count,
            scope=scope,
        )

    def _resolve_workspace_entry(self, ref: str, path: str) -> pygit2.Object | None:
        """Resolve a workspace path at ref to its Git object, if present."""
        return _store_workspace_queries.resolve_workspace_entry(self, ref, path)

    def file_exists_in_workspace(self, ref: str, path: str) -> bool:
        """Check if a file exists in the workspace tree at the given ref."""
        return _store_workspace_queries.file_exists_in_workspace(self, ref, path)

    def read_workspace_file(self, ref: str, path: str) -> bytes | None:
        """Read file bytes from the workspace tree at the given ref."""
        return _store_workspace_queries.read_workspace_file(self, ref, path)

    def workspace_file_mode(self, ref: str, path: str) -> int | None:
        """Return the Git filemode for a workspace file, or None if not found."""
        return _store_workspace_queries.workspace_file_mode(self, ref, path)

    def resolve_to_commit(self, commitish: str) -> pygit2.Commit | None:
        """Resolve a ref name or hex OID to a Commit, or None.

        Resolution order:
        1. Named ref (e.g. refs/vcscore/ground)
        2. Full hex OID (40 chars)
        3. Short OID prefix via revparse (pygit2 handles prefix matching)
        """
        return _store_workspace_queries.resolve_to_commit(self, commitish)

    def _get_workspace_tree_oid(self, commitish: str) -> pygit2.Oid | None:
        """Resolve a commitish to the workspace tree OID, or None."""
        return _store_workspace_queries.get_workspace_tree_oid(self, commitish)

    def list_workspace_files(self, ref: str) -> list[tuple[str, str, int]]:
        """List all files in the workspace tree at the given ref or OID.

        Returns list of (path, blob_oid_hex, git_filemode) triples.
        Raises RefResolutionError if the ref cannot be resolved.
        """
        return _store_workspace_queries.list_workspace_files(self, ref)

    _CHECKOUT_MARKER = ".vcscore-checkout"

    def checkout_workspace_tree(self, ref: str, dest: str) -> int:
        """Extract all workspace files at ref or OID to dest directory.

        If dest was created by a prior checkout (has a marker file),
        it is cleaned and re-extracted. Otherwise, dest must not exist.

        Raises RefResolutionError if the ref cannot be resolved.
        Raises ValueError if dest is a protected path or an unmarked
        existing directory.

        Returns number of workspace files written (excludes the marker).
        """
        return _store_workspace_queries.checkout_workspace_tree(self, ref, dest)

    def list_scope_refs(self) -> list[str]:
        """Enumerate all live scope refs (refs/vcscore/scopes/*)."""
        return sorted(r for r in self._repo.references if r.startswith("refs/vcscore/scopes/"))

    def list_archive_refs(self) -> list[str]:
        """Enumerate all archive refs (refs/vcscore/archive/*)."""
        return sorted(r for r in self._repo.references if r.startswith("refs/vcscore/archive/"))

    @staticmethod
    def _is_discarded_world_archive_ref(ref: str) -> bool:
        return (
            ref.startswith("refs/vcscore/archive/")
            and not ref.startswith("refs/vcscore/archive/ops/")
            and not ref.startswith("refs/vcscore/archive/ground-reset-")
        )

    def list_discarded_world_archive_refs(self) -> list[str]:
        """Enumerate archived world refs that can carry discarded execution history."""
        return sorted(ref for ref in self._repo.references if self._is_discarded_world_archive_ref(ref))

    def ref_exists(self, ref: str) -> bool:
        """Check if a Git ref exists in the repository."""
        with self._mutation_lock:
            return ref in self._repo.references

    def _delete_ref_if_exists(self, ref: str) -> bool:
        """Delete a ref under the repo mutation gate. Returns whether it existed."""
        with self._mutation_lock:
            if ref not in self._repo.references:
                return False
            self._repo.references.delete(ref)
            return True

    def _delete_ref_if_ref_exists(self, *, ref: str, required_ref: str) -> bool:
        """Delete ref only if required_ref exists, under one repo mutation gate."""
        with self._mutation_lock:
            if required_ref not in self._repo.references:
                return False
            if ref not in self._repo.references:
                return False
            self._repo.references.delete(ref)
            return True

    def rebase(self, source: ScopeInfo, onto_ref: str) -> RebaseResult:
        """Replay source's commits onto onto_ref's tip (linear history).

        Pure Git operation -- no substrate involvement. Requires three-way
        merge via merge_trees for correctness (concurrent branches may
        have diverged). Deferred to R2 alongside lateral merge.

        The merge_trees primitive is priced GREEN -- "wrap libgit2," no
        fork-point search (the fork point == creation_oid == the libgit2
        merge-base; conflicts represented via the libgit2 conflict index). See
        spikes/260614-three-way-merge-store/ (probe 6). Note: pass
        flags=MergeFlag(0) -- pygit2 defaults rename-detection ON.
        """
        raise NotImplementedError(
            "Store.rebase() requires three-way merge (merge_trees) for "
            "correctness. Deferred to R2 alongside lateral merge support."
        )

    def prune_archives(self, keep_recent: int = 100) -> int:
        """Delete old archive refs beyond keep_recent. Returns count pruned."""
        with self._mutation_lock:
            archive_refs = sorted(
                (r for r in self._repo.references if r.startswith("refs/vcscore/archive/")),
                key=lambda ref: (self._archive_ref_commit_time(ref), ref),
            )
            to_prune = archive_refs[: max(0, len(archive_refs) - keep_recent)]
            for ref in to_prune:
                self._repo.references.delete(ref)
            self._archived_operation_membership_cache = None
            self._archived_operation_projection_cache = None
            self._refresh_archived_operation_projection()
            return len(to_prune)

    def _archive_ref_commit_time(self, ref: str) -> int:
        commit = self._repo.references[ref].peel(pygit2.Commit)
        return commit.commit_time
