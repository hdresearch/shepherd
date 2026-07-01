"""Coordinator repository for v2 world commits."""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pygit2

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._pygit2_helpers import require_commit, require_tree
from vcs_core._transition_kernel_records import (
    CandidateCommitRecord,
    CandidateOutcomeRecord,
    EvidenceOnlyEnvelopeRecord,
    EvidenceRecord,
    EvidenceRef,
    HeadSelectionEvidence,
    HeadSelectionRecord,
    is_candidate_backed_selection_kind,
    validate_head_selection,
)
from vcs_core._world_refs import (
    candidate_archive_ref,
    candidate_ref,
    evidence_only_envelope_ref,
    evidence_record_ref,
    world_pin_ref,
)
from vcs_core._world_retention import CHILD_WORLD_RETENTION, SELECTED_HEAD_PIN, validate_retention_policy_kind
from vcs_core._world_selection_policy import (
    ExistingHeadSelectionKind,
    allowed_existing_head_semantic_ops,
    validate_unchanged_head_identity,
)
from vcs_core._world_types import (
    OPERATION_FINAL_SCHEMA,
    WORLD_REF_SUBSTRATE_KIND,
    WORLD_SCHEMA,
    WORLD_TRANSITION_SCHEMA,
    OperationFinalRecord,
    SubstrateHead,
    WorldCommit,
    WorldRefPayload,
    WorldSnapshot,
    canonical_digest,
    compact_json_bytes,
    load_canonical_json,
)
from vcs_core.git_store import create_commit_with_recovery, create_or_update_reference, insert_tree_entry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from vcs_core._substrate_store import PreparedCandidateProvenance, SubstrateStore

WORLD_IDENTITY_REF = "refs/vcscore/world-store/identity"
OPERATION_FINAL_PATH = "meta/operation-final.json"
EVIDENCE_RECORD_PATH = "meta/evidence-record.json"
EVIDENCE_ONLY_ENVELOPE_PATH = "meta/evidence-only-envelope.json"
FILEMODE_COMMIT = getattr(pygit2, "GIT_FILEMODE_COMMIT", 0o160000)


class WorldValidationProfile(str, Enum):
    """Validation depth for world commits."""

    STRUCTURAL = "structural"
    DEEP = "deep"


@dataclass(frozen=True)
class _SelectionValidationContext:
    world: WorldCommit
    stores_by_id: Mapping[str, SubstrateStore]
    selected: Mapping[str, str]
    operation_id: str
    input_world: WorldCommit | None
    selections_by_binding: Mapping[str, HeadSelectionRecord]
    evidence_by_binding: Mapping[str, HeadSelectionEvidence]
    candidate_commits: Mapping[tuple[str, str, str, str], CandidateCommitRecord]
    selected_outcomes_by_binding: Mapping[str, Mapping[str, Any]]


class WorldStore:
    """Git-backed coordinator store for v2 world snapshots and transitions."""

    def __init__(self, repo_path: str, *, world_store_id: str, repo: pygit2.Repository) -> None:
        if not world_store_id:
            raise ValueError("world_store_id is required")
        self._repo_path = repo_path
        self._world_store_id = world_store_id
        self._repo = repo
        self._mutation_lock = threading.RLock()

    @classmethod
    def open_or_init(cls, repo_path: str | Path, *, world_store_id: str) -> WorldStore:
        """Open or initialize a bare coordinator repo and verify its world store id."""
        path = Path(repo_path)
        repo = _open_or_init_bare_repo(path)
        store = cls(str(path), world_store_id=world_store_id, repo=repo)
        try:
            existing_id = store._read_identity()
        except KeyError:
            store._write_identity()
        else:
            if existing_id != world_store_id:
                raise InvalidRepositoryStateError(
                    f"World store identity mismatch for {path}: expected {world_store_id!r}, found {existing_id!r}."
                )
        return store

    @classmethod
    def open_existing(cls, repo_path: str | Path, *, world_store_id: str) -> WorldStore:
        """Open an existing bare coordinator repo and verify its world store id."""
        path = Path(repo_path)
        repo = _open_existing_bare_repo(path)
        store = cls(str(path), world_store_id=world_store_id, repo=repo)
        try:
            existing_id = store._read_identity()
        except KeyError as exc:
            raise InvalidRepositoryStateError(f"World store at {path} is missing its identity ref") from exc
        if existing_id != world_store_id:
            raise InvalidRepositoryStateError(
                f"World store identity mismatch for {path}: expected {world_store_id!r}, found {existing_id!r}."
            )
        return store

    @property
    def repo_path(self) -> str:
        return self._repo_path

    @property
    def repo(self) -> pygit2.Repository:
        return self._repo

    @property
    def world_store_id(self) -> str:
        return self._world_store_id

    def store_evidence_record(self, record: EvidenceRecord) -> EvidenceRef:
        """Persist one immutable evidence record and return its durable coordinator ref."""
        record_bytes = record.canonical_bytes()
        EvidenceRecord.from_canonical_bytes(record_bytes)
        ref = evidence_record_ref(record.operation_id, record.record_digest())
        with self._mutation_lock:
            if ref in self._repo.references:
                existing = self.resolve_evidence_ref(ref, expected_operation_id=record.operation_id)
                if existing.record_digest() != record.record_digest():
                    raise InvalidRepositoryStateError(f"Evidence ref already exists for a different record: {ref}")
            else:
                meta_builder = self._repo.TreeBuilder()
                _insert_blob(self._repo, meta_builder, "evidence-record.json", record_bytes)
                root_builder = self._repo.TreeBuilder()
                insert_tree_entry(
                    self._repo,
                    root_builder,
                    "meta",
                    meta_builder.write(),
                    pygit2.GIT_FILEMODE_TREE,
                )
                signature = pygit2.Signature("vcs-core world store", "vcs-core@example.invalid")
                oid = create_commit_with_recovery(
                    self._repo,
                    None,
                    signature,
                    signature,
                    f"evidence {record.operation_id}",
                    root_builder.write(),
                    [],
                )
                create_or_update_reference(self._repo, ref, oid)
        return EvidenceRef(
            ref=ref,
            evidence_digest=record.evidence_digest(),
            record_digest=record.record_digest(),
            payload_digest=record.payload_digest,
        )

    def resolve_evidence_ref(
        self,
        evidence: EvidenceRef | str,
        *,
        expected_operation_id: str | None = None,
    ) -> EvidenceRecord:
        """Resolve and validate one coordinator-owned evidence ref."""
        ref = evidence.ref if isinstance(evidence, EvidenceRef) else evidence
        try:
            target = self._repo.references[ref].target
        except KeyError as exc:
            raise InvalidRepositoryStateError(f"evidence ref is missing: {ref}") from exc
        commit = require_commit(self._repo, _coerce_oid(target), context="evidence record")
        try:
            record = EvidenceRecord.from_canonical_bytes(
                _read_blob_bytes(self._repo, commit.tree, EVIDENCE_RECORD_PATH)
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidRepositoryStateError(f"invalid evidence record at {ref}: {exc}") from exc
        if expected_operation_id is not None and record.operation_id != expected_operation_id:
            raise InvalidRepositoryStateError("evidence record operation_id disagrees with expected operation")
        expected_ref = evidence_record_ref(record.operation_id, record.record_digest())
        if ref != expected_ref:
            raise InvalidRepositoryStateError("evidence ref path disagrees with evidence record digest")
        if isinstance(evidence, EvidenceRef):
            if evidence.evidence_digest != record.evidence_digest():
                raise InvalidRepositoryStateError("evidence ref evidence_digest disagrees with record")
            if evidence.record_digest != record.record_digest():
                raise InvalidRepositoryStateError("evidence ref record_digest disagrees with record")
            if evidence.payload_digest != record.payload_digest:
                raise InvalidRepositoryStateError("evidence ref payload_digest disagrees with record")
        return record

    def store_evidence_only_envelope(self, envelope: EvidenceOnlyEnvelopeRecord) -> str:
        """Persist one immutable evidence-only envelope and return its durable coordinator ref."""
        envelope_bytes = envelope.canonical_bytes()
        envelope = EvidenceOnlyEnvelopeRecord.from_canonical_bytes(envelope_bytes)
        ref = evidence_only_envelope_ref(envelope.producer_operation_id, envelope.envelope_digest())
        with self._mutation_lock:
            self._validate_evidence_only_envelope(envelope)
            if ref in self._repo.references:
                existing = self.resolve_evidence_only_envelope(ref)
                if existing.envelope_digest() != envelope.envelope_digest():
                    raise InvalidRepositoryStateError(
                        f"Evidence-only envelope ref already exists for a different record: {ref}"
                    )
            else:
                meta_builder = self._repo.TreeBuilder()
                _insert_blob(self._repo, meta_builder, "evidence-only-envelope.json", envelope_bytes)
                root_builder = self._repo.TreeBuilder()
                insert_tree_entry(
                    self._repo,
                    root_builder,
                    "meta",
                    meta_builder.write(),
                    pygit2.GIT_FILEMODE_TREE,
                )
                signature = pygit2.Signature("vcs-core world store", "vcs-core@example.invalid")
                oid = create_commit_with_recovery(
                    self._repo,
                    None,
                    signature,
                    signature,
                    f"evidence-only {envelope.producer_operation_id}",
                    root_builder.write(),
                    [],
                )
                create_or_update_reference(self._repo, ref, oid)
        return ref

    def resolve_evidence_only_envelope(
        self,
        envelope_ref: str,
        *,
        expected_operation_id: str | None = None,
    ) -> EvidenceOnlyEnvelopeRecord:
        """Resolve and validate one coordinator-owned evidence-only envelope."""
        try:
            target = self._repo.references[envelope_ref].target
        except KeyError as exc:
            raise InvalidRepositoryStateError(f"evidence-only envelope ref is missing: {envelope_ref}") from exc
        commit = require_commit(self._repo, _coerce_oid(target), context="evidence-only envelope")
        try:
            envelope = EvidenceOnlyEnvelopeRecord.from_canonical_bytes(
                _read_blob_bytes(self._repo, commit.tree, EVIDENCE_ONLY_ENVELOPE_PATH)
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidRepositoryStateError(f"invalid evidence-only envelope at {envelope_ref}: {exc}") from exc
        if expected_operation_id is not None and envelope.producer_operation_id != expected_operation_id:
            raise InvalidRepositoryStateError("evidence-only envelope operation_id disagrees with expected operation")
        expected_ref = evidence_only_envelope_ref(envelope.producer_operation_id, envelope.envelope_digest())
        if envelope_ref != expected_ref:
            raise InvalidRepositoryStateError("evidence-only envelope ref path disagrees with envelope digest")
        self._validate_evidence_only_envelope(envelope)
        return envelope

    def _validate_evidence_only_envelope(self, envelope: EvidenceOnlyEnvelopeRecord) -> None:
        seen_refs: set[str] = set()
        seen_record_digests: set[str] = set()
        for index, evidence_ref in enumerate(envelope.evidence_refs):
            if evidence_ref.ref in seen_refs:
                raise InvalidRepositoryStateError("evidence-only envelope contains duplicate evidence ref")
            seen_refs.add(evidence_ref.ref)
            record = self.resolve_evidence_ref(
                evidence_ref,
                expected_operation_id=envelope.producer_operation_id,
            )
            if record.record_digest() in seen_record_digests:
                raise InvalidRepositoryStateError("evidence-only envelope contains duplicate evidence record")
            seen_record_digests.add(record.record_digest())
            if record.evidence_kind != envelope.evidence_kinds[index]:
                raise InvalidRepositoryStateError("evidence-only envelope evidence kind disagrees with record")
            if record.binding != envelope.binding:
                raise InvalidRepositoryStateError("evidence-only envelope evidence binding disagrees with envelope")
            if record.store_id != envelope.store_id:
                raise InvalidRepositoryStateError("evidence-only envelope evidence store_id disagrees with envelope")
            if record.substrate_kind != envelope.substrate_kind:
                raise InvalidRepositoryStateError(
                    "evidence-only envelope evidence substrate kind disagrees with envelope"
                )
            if record.ingress_kind != envelope.ingress_kind:
                raise InvalidRepositoryStateError("evidence-only envelope evidence ingress disagrees with envelope")

    def create_world_commit(
        self,
        *,
        snapshot: WorldSnapshot,
        transition: Mapping[str, Any],
        operation_final: Mapping[str, Any] | OperationFinalRecord,
        parents: tuple[str | pygit2.Oid, ...] = (),
        locator_hints: Mapping[str, str] | None = None,
        include_gitlinks: bool = False,
        gitlink_heads: Mapping[str, str] | None = None,
    ) -> str:
        """Create an immutable world commit without publishing any authority ref."""
        if gitlink_heads is not None and not include_gitlinks:
            raise ValueError("gitlink_heads require include_gitlinks=True")
        final_record = (
            operation_final
            if isinstance(operation_final, OperationFinalRecord)
            else OperationFinalRecord(dict(operation_final))
        )
        final_bytes = final_record.canonical_bytes()
        final_digest = f"sha256:{hashlib.sha256(final_bytes).hexdigest()}"
        transition_payload = {
            **dict(transition),
            "operation_final": {
                "path": OPERATION_FINAL_PATH,
                "digest": final_digest,
            },
        }
        manifest = {
            "schema": WORLD_SCHEMA,
            "snapshot": snapshot.to_json(),
            "locator_hints": dict(sorted((locator_hints or {}).items())),
        }

        with self._mutation_lock:
            meta_builder = self._repo.TreeBuilder()
            _insert_blob(self._repo, meta_builder, "world.json", compact_json_bytes(manifest))
            _insert_blob(self._repo, meta_builder, "transition.json", compact_json_bytes(transition_payload))
            _insert_blob(self._repo, meta_builder, "operation-final.json", final_bytes)
            meta_tree = meta_builder.write()

            root_builder = self._repo.TreeBuilder()
            insert_tree_entry(self._repo, root_builder, "meta", meta_tree, pygit2.GIT_FILEMODE_TREE)
            if include_gitlinks:
                gitlink_map = _gitlink_map(snapshot, gitlink_heads)
                if gitlink_map:
                    substrates_builder = self._repo.TreeBuilder()
                    for binding, head in sorted(gitlink_map.items()):
                        insert_tree_entry(
                            self._repo,
                            substrates_builder,
                            binding,
                            pygit2.Oid(hex=head),
                            FILEMODE_COMMIT,
                        )
                    insert_tree_entry(
                        self._repo,
                        root_builder,
                        "substrates",
                        substrates_builder.write(),
                        pygit2.GIT_FILEMODE_TREE,
                    )

            root_tree = root_builder.write()
            signature = pygit2.Signature("vcs-core world store", "vcs-core@example.invalid")
            message = str(transition_payload.get("operation_id", "world commit"))
            oid = create_commit_with_recovery(
                self._repo,
                None,
                signature,
                signature,
                message,
                root_tree,
                [_coerce_oid(parent) for parent in parents],
            )
            return str(oid)

    def read_world_commit(self, oid: str) -> WorldCommit:
        """Read and validate one v2 world commit from the coordinator repo."""
        commit = require_commit(self._repo, pygit2.Oid(hex=oid), context="world commit")
        manifest = _read_json_blob(self._repo, commit.tree, "meta/world.json")
        if manifest.get("schema") != WORLD_SCHEMA:
            raise ValueError(f"unsupported world schema: {manifest.get('schema')!r}")
        raw_snapshot = manifest.get("snapshot")
        if not isinstance(raw_snapshot, dict):
            raise TypeError("world manifest snapshot must be a JSON object")
        snapshot = WorldSnapshot.from_json(raw_snapshot)
        raw_locator_hints = manifest.get("locator_hints", {})
        if not isinstance(raw_locator_hints, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in raw_locator_hints.items()
        ):
            raise TypeError("world manifest locator_hints must be a string map")
        locator_hints = dict(raw_locator_hints)
        transition = _read_json_blob(self._repo, commit.tree, "meta/transition.json")
        parent_oids = tuple(str(parent) for parent in commit.parent_ids)
        _validate_transition(transition, parent_oids)
        operation_final_bytes = _read_blob_bytes(self._repo, commit.tree, _operation_final_path(transition))
        expected_digest = _operation_final_digest(transition)
        actual_digest = f"sha256:{hashlib.sha256(operation_final_bytes).hexdigest()}"
        if actual_digest != expected_digest:
            raise InvalidRepositoryStateError("operation-final digest disagrees with embedded canonical bytes")
        operation_final = load_canonical_json(operation_final_bytes)
        try:
            canonical_final = OperationFinalRecord(operation_final)
        except (TypeError, ValueError) as exc:
            raise InvalidRepositoryStateError(f"invalid operation-final record: {exc}") from exc
        if canonical_final.canonical_bytes() != operation_final_bytes:
            raise InvalidRepositoryStateError("operation-final record is not semantically canonical")
        self._validate_optional_gitlinks(commit, snapshot)
        return WorldCommit(
            oid=str(commit.id),
            snapshot=snapshot,
            transition=transition,
            operation_final=canonical_final.payload,
            manifest=manifest,
            locator_hints=locator_hints,
            parent_oids=parent_oids,
        )

    def validate_world_commit(
        self,
        oid: str,
        bound_stores: Mapping[str, SubstrateStore],
        *,
        allow_same_resource_alias: bool = False,
        require_selected_candidate_refs: bool = True,
        validate_input_worlds: bool = True,
        profile: WorldValidationProfile | str = WorldValidationProfile.DEEP,
    ) -> None:
        """Validate selected heads, store identities, operation evidence, and optional gitlinks."""
        validation_profile = _world_validation_profile(profile)
        self._validate_world_commit(
            oid,
            bound_stores,
            allow_same_resource_alias=allow_same_resource_alias,
            require_selected_candidate_refs=require_selected_candidate_refs,
            validate_input_worlds=validate_input_worlds,
            profile=validation_profile,
            seen_worlds=frozenset(),
            depth=0,
        )

    def _validate_world_commit(
        self,
        oid: str,
        bound_stores: Mapping[str, SubstrateStore],
        *,
        allow_same_resource_alias: bool,
        require_selected_candidate_refs: bool,
        validate_input_worlds: bool,
        profile: WorldValidationProfile,
        seen_worlds: frozenset[str],
        depth: int,
    ) -> None:
        if depth > 64:
            raise InvalidRepositoryStateError("recursive world validation exceeded maximum depth")
        if oid in seen_worlds:
            return
        seen_worlds = seen_worlds | {oid}
        world = self.read_world_commit(oid)
        stores_by_id = _stores_by_id(bound_stores)
        seen_store_resources: dict[str, str] = {}
        seen_resource_stores: dict[str, str] = {}

        for head in world.snapshot.heads:
            try:
                store = stores_by_id[head.store_id]
            except KeyError as exc:
                raise InvalidRepositoryStateError(f"missing substrate store for binding {head.binding!r}") from exc
            _validate_head_identity(head, store)
            if not store.contains(head.head):
                raise InvalidRepositoryStateError(
                    f"substrate store {head.store_id!r} does not contain selected head {head.head!r}"
                )
            store.read_revision_metadata(head.head)
            if head.kind == WORLD_REF_SUBSTRATE_KIND:
                self._validate_world_ref_head(head, store)

            prior_resource = seen_store_resources.get(head.store_id)
            if prior_resource is not None and prior_resource != head.resource_id:
                raise InvalidRepositoryStateError("distinct resource_id bindings must not share one substrate store")
            seen_store_resources.setdefault(head.store_id, head.resource_id)

            prior_store = seen_resource_stores.get(head.resource_id)
            if prior_store is not None and prior_store != head.store_id:
                raise InvalidRepositoryStateError("one resource_id must not resolve to multiple substrate stores")
            if prior_store == head.store_id and not allow_same_resource_alias:
                raise InvalidRepositoryStateError("same-resource aliases require explicit coordinator policy")
            seen_resource_stores.setdefault(head.resource_id, head.store_id)
        _validate_operation_final(
            world,
            stores_by_id,
            require_selected_candidate_refs=require_selected_candidate_refs,
            evidence_resolver=self.resolve_evidence_ref,
            producer_world_resolver=self.read_world_commit,
            producer_world_validator=lambda producer_oid: self._validate_world_commit(
                producer_oid,
                bound_stores,
                allow_same_resource_alias=allow_same_resource_alias,
                require_selected_candidate_refs=require_selected_candidate_refs,
                validate_input_worlds=validate_input_worlds,
                profile=profile,
                seen_worlds=seen_worlds,
                depth=depth + 1,
            ),
            validate_input_worlds=validate_input_worlds,
            profile=profile,
        )

    def pin_selected_heads(self, oid: str, bound_stores: Mapping[str, SubstrateStore]) -> tuple[str, ...]:
        """Write world-scoped substrate pin refs for every selected head."""
        world = self.read_world_commit(oid)
        stores_by_id = _stores_by_id(bound_stores)
        pin_refs: list[str] = []
        for head in world.snapshot.heads:
            store = stores_by_id[head.store_id]
            pin_refs.append(
                store.pin_world_head(
                    world_store_id=self.world_store_id,
                    world_oid=oid,
                    binding=head.binding,
                    head=head.head,
                )
            )
        return tuple(pin_refs)

    def _publish_ref_unchecked(self, ref: str, new_oid: str, expected_oid: str | None) -> bool:
        """Unchecked compare-and-swap primitive for world authority refs.

        Callers must validate closure retention, write retained refs, and record
        retention receipts before using this low-level ref update.
        """
        if not pygit2.reference_is_valid_name(ref):
            raise InvalidRepositoryStateError(f"invalid world ref name: {ref!r}")
        _validated_commit_oid(self._repo, new_oid, context="world ref target")
        expected_target = (
            _validated_oid(expected_oid, context="expected world ref target") if expected_oid is not None else None
        )
        cmd = ["git", "update-ref", ref, new_oid, expected_oid or ""]
        try:
            result = subprocess.run(cmd, cwd=self._repo.path, capture_output=True, check=False, text=True)
        except OSError as exc:
            raise InvalidRepositoryStateError(f"failed to update world ref {ref!r}: {exc}") from exc
        if result.returncode == 0:
            return True

        current_target = _current_ref_target(self._repo, ref)
        if expected_target is None:
            if current_target is not None:
                return False
        elif current_target != str(expected_target):
            return False

        detail = (result.stderr or result.stdout or "git update-ref failed").strip()
        raise InvalidRepositoryStateError(f"failed to update world ref {ref!r}: {detail}")

    def classify_world_pins(
        self,
        oid: str,
        bound_stores: Mapping[str, SubstrateStore],
        *,
        authority_refs: tuple[str, ...],
    ) -> dict[str, tuple[str, ...]]:
        """Classify world-scoped selected-head pins for recovery and GC."""
        world = self.read_world_commit(oid)
        stores_by_id = _stores_by_id(bound_stores)
        published = _world_is_reachable_from_refs(self._repo, oid, authority_refs)
        result: dict[str, list[str]] = {
            "published": [],
            "orphaned": [],
            "missing_for_published_world": [],
            "corrupt": [],
        }

        for head in world.snapshot.heads:
            store = stores_by_id[head.store_id]
            ref = world_pin_ref(self.world_store_id, oid, head.binding)
            try:
                target = store.repo.references[ref].target
            except KeyError:
                if published:
                    result["missing_for_published_world"].append(ref)
                continue
            if str(target) != head.head:
                result["corrupt"].append(ref)
            elif published:
                result["published"].append(ref)
            else:
                result["orphaned"].append(ref)
        return {key: tuple(values) for key, values in result.items()}

    def delete_orphan_world_pins(
        self,
        oid: str,
        bound_stores: Mapping[str, SubstrateStore],
        *,
        authority_refs: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Delete recoverable pins for an unpublished world commit."""
        classification = self.classify_world_pins(oid, bound_stores, authority_refs=authority_refs)
        stores_by_id = _stores_by_id(bound_stores)
        world = self.read_world_commit(oid)
        refs_by_binding = {world_pin_ref(self.world_store_id, oid, head.binding): head for head in world.snapshot.heads}
        deleted: list[str] = []
        for ref in classification["orphaned"]:
            head = refs_by_binding[ref]
            stores_by_id[head.store_id].repo.references[ref].delete()
            deleted.append(ref)
        return tuple(deleted)

    def _validate_world_ref_head(self, head: SubstrateHead, store: SubstrateStore) -> None:
        commit = require_commit(store.repo, pygit2.Oid(hex=head.head), context="world-ref substrate revision")
        try:
            payload = WorldRefPayload.from_json(_read_json_blob(store.repo, commit.tree, "revision.json"))
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidRepositoryStateError("invalid world-ref substrate payload") from exc
        if payload.world_store_id != self.world_store_id:
            raise InvalidRepositoryStateError("world-ref payload world_store_id disagrees with coordinator")
        referenced = self.read_world_commit(payload.world_oid)
        if referenced.snapshot.digest() != payload.snapshot_digest:
            raise InvalidRepositoryStateError("world-ref payload snapshot_digest disagrees with referenced world")

    def _read_identity(self) -> str:
        if WORLD_IDENTITY_REF not in self._repo.references:
            raise KeyError(WORLD_IDENTITY_REF)
        commit = self._repo.references[WORLD_IDENTITY_REF].peel(pygit2.Commit)
        value = _read_json_blob(self._repo, commit.tree, "identity.json")
        if value.get("schema") != "vcscore/world-store-identity/v1":
            raise ValueError(f"unsupported world store identity schema: {value.get('schema')!r}")
        world_store_id = value.get("world_store_id")
        if not isinstance(world_store_id, str) or not world_store_id:
            raise ValueError("world_store_id is required")
        return world_store_id

    def _write_identity(self) -> None:
        with self._mutation_lock:
            if WORLD_IDENTITY_REF in self._repo.references:
                raise InvalidRepositoryStateError(f"World store identity ref already exists: {WORLD_IDENTITY_REF}")
            tree_builder = self._repo.TreeBuilder()
            _insert_blob(
                self._repo,
                tree_builder,
                "identity.json",
                compact_json_bytes(
                    {
                        "schema": "vcscore/world-store-identity/v1",
                        "world_store_id": self.world_store_id,
                    }
                ),
            )
            tree = tree_builder.write()
            signature = pygit2.Signature("vcs-core world store", "vcs-core@example.invalid")
            oid = create_commit_with_recovery(
                self._repo,
                None,
                signature,
                signature,
                "world store identity",
                tree,
                [],
            )
            create_or_update_reference(self._repo, WORLD_IDENTITY_REF, oid)

    def _validate_optional_gitlinks(self, commit: pygit2.Commit, snapshot: WorldSnapshot) -> None:
        try:
            substrates_entry = commit.tree["substrates"]
        except KeyError:
            return
        substrates_tree = require_tree(self._repo, substrates_entry.id, context="world substrates gitlink tree")
        selected_heads = snapshot.by_binding()
        for entry in substrates_tree:
            binding = str(entry.name)
            if binding not in selected_heads:
                raise InvalidRepositoryStateError(f"unexpected gitlink for {binding}")
            if entry.filemode != FILEMODE_COMMIT:
                raise InvalidRepositoryStateError(f"gitlink for {binding} has wrong file mode")
            if str(entry.id) != selected_heads[binding].head:
                raise InvalidRepositoryStateError(f"gitlink for {binding} disagrees with manifest")


def _open_or_init_bare_repo(path: Path) -> pygit2.Repository:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        repo = pygit2.init_repository(str(path), bare=True)
    else:
        try:
            repo = pygit2.Repository(str(path))
        except (KeyError, ValueError, pygit2.GitError) as exc:
            if not path.is_dir() or any(path.iterdir()):
                raise InvalidRepositoryStateError(f"{path} exists but is not a Git repository") from exc
            repo = pygit2.init_repository(str(path), bare=True)
    if not repo.is_bare:
        raise InvalidRepositoryStateError(f"{path} is not a bare world store repository")
    return repo


def _open_existing_bare_repo(path: Path) -> pygit2.Repository:
    if not path.exists():
        raise InvalidRepositoryStateError(f"configured world store is missing: {path}")
    try:
        repo = pygit2.Repository(str(path))
    except (KeyError, ValueError, pygit2.GitError) as exc:
        raise InvalidRepositoryStateError(f"{path} exists but is not a Git repository") from exc
    if not repo.is_bare:
        raise InvalidRepositoryStateError(f"{path} is not a bare world store repository")
    return repo


def _insert_blob(repo: pygit2.Repository, builder: pygit2.TreeBuilder, name: str, data: bytes) -> None:
    insert_tree_entry(repo, builder, name, repo.create_blob(data), pygit2.GIT_FILEMODE_BLOB)


def _read_blob_bytes(repo: pygit2.Repository, tree: pygit2.Tree, path: str) -> bytes:
    obj: pygit2.Object = tree
    for component in path.split("/"):
        if not isinstance(obj, pygit2.Tree):
            raise TypeError(f"{path!r} did not resolve to a blob")
        obj = repo[obj[component].id]
    if not isinstance(obj, pygit2.Blob):
        raise TypeError(f"{path!r} did not resolve to a blob")
    return bytes(obj.data)


def _read_json_blob(repo: pygit2.Repository, tree: pygit2.Tree, path: str) -> dict[str, Any]:
    blob = _read_blob_bytes(repo, tree, path)
    value = json.loads(blob.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path!r} must contain a JSON object")
    return value


def _coerce_oid(value: str | pygit2.Oid) -> pygit2.Oid:
    if isinstance(value, pygit2.Oid):
        return value
    return pygit2.Oid(hex=value)


def _gitlink_map(snapshot: WorldSnapshot, gitlink_heads: Mapping[str, str] | None) -> dict[str, str]:
    selected_heads = snapshot.by_binding()
    if gitlink_heads is None:
        return {
            binding: head.head for binding, head in selected_heads.items() if _can_represent_gitlink(head, head.head)
        }

    result: dict[str, str] = {}
    for binding, oid in gitlink_heads.items():
        try:
            head = selected_heads[binding]
        except KeyError as exc:
            raise ValueError(f"gitlink binding {binding!r} is not present in the world snapshot") from exc
        if not _can_represent_gitlink(head, oid):
            raise ValueError(f"gitlink for {binding!r} cannot represent substrate object format {head.object_format!r}")
        result[binding] = oid
    return result


def _can_represent_gitlink(head: SubstrateHead, oid: str) -> bool:
    return head.object_format == "sha1" and len(oid) == 40 and all(char in "0123456789abcdefABCDEF" for char in oid)


def _operation_final_path(transition: Mapping[str, Any]) -> str:
    operation_final = transition.get("operation_final")
    if not isinstance(operation_final, dict):
        raise InvalidRepositoryStateError("transition is missing operation_final")
    path = operation_final.get("path")
    if not isinstance(path, str) or not path:
        raise InvalidRepositoryStateError("transition operation_final.path is required")
    if path != OPERATION_FINAL_PATH:
        raise InvalidRepositoryStateError(f"transition operation_final.path must be {OPERATION_FINAL_PATH!r}")
    return path


def _operation_final_digest(transition: Mapping[str, Any]) -> str:
    operation_final = transition.get("operation_final")
    if not isinstance(operation_final, dict):
        raise InvalidRepositoryStateError("transition is missing operation_final")
    digest = operation_final.get("digest")
    if not isinstance(digest, str) or not digest:
        raise InvalidRepositoryStateError("transition operation_final.digest is required")
    return digest


def _validate_transition(transition: Mapping[str, Any], parent_oids: tuple[str, ...]) -> None:
    if transition.get("schema") != WORLD_TRANSITION_SCHEMA:
        raise InvalidRepositoryStateError(f"unsupported world transition schema: {transition.get('schema')!r}")
    operation_id = transition.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise InvalidRepositoryStateError("world transition operation_id is required")
    parent_worlds = transition.get("parent_worlds")
    if not isinstance(parent_worlds, list) or not all(isinstance(parent, str) for parent in parent_worlds):
        raise InvalidRepositoryStateError("world transition parent_worlds must be a string list")
    if tuple(parent_worlds) != parent_oids:
        raise InvalidRepositoryStateError("world transition parent_worlds disagree with Git commit parents")
    input_world = transition.get("input_world")
    if parent_oids and (not isinstance(input_world, str) or not input_world):
        raise InvalidRepositoryStateError("world transition input_world is required for non-root worlds")
    if input_world is not None:
        if not isinstance(input_world, str) or not input_world:
            raise InvalidRepositoryStateError("world transition input_world must be a non-empty string")
        if input_world not in parent_oids:
            raise InvalidRepositoryStateError("world transition input_world must be one of parent_worlds")


def _validate_operation_final(
    world: WorldCommit,
    stores_by_id: Mapping[str, SubstrateStore],
    *,
    require_selected_candidate_refs: bool,
    evidence_resolver: Any,
    producer_world_resolver: Any,
    producer_world_validator: Any,
    validate_input_worlds: bool,
    profile: WorldValidationProfile,
) -> None:
    final = world.operation_final
    if final.get("schema") != OPERATION_FINAL_SCHEMA:
        raise InvalidRepositoryStateError(f"unsupported operation-final schema: {final.get('schema')!r}")

    selected = final.get("selected")
    expected_heads = world.snapshot.by_binding()
    expected_selected = {binding: head.head for binding, head in expected_heads.items()}
    if not isinstance(selected, dict) or not all(
        isinstance(binding, str) and isinstance(head, str) for binding, head in selected.items()
    ):
        raise InvalidRepositoryStateError("operation-final selected must be a string map")
    if selected != expected_selected:
        raise InvalidRepositoryStateError("operation-final selected heads disagree with world snapshot")

    transition_operation_id = world.transition.get("operation_id")
    final_operation_id = final.get("operation_id")
    if not isinstance(transition_operation_id, str) or not transition_operation_id:
        raise InvalidRepositoryStateError("world transition operation_id is required")
    if not isinstance(final_operation_id, str) or not final_operation_id:
        raise InvalidRepositoryStateError("operation-final operation_id is required")
    if transition_operation_id != final_operation_id:
        raise InvalidRepositoryStateError("operation-final operation_id disagrees with transition")
    input_world = _validated_input_world(
        world,
        producer_world_resolver=producer_world_resolver,
        producer_world_validator=producer_world_validator,
        validate_input_worlds=validate_input_worlds,
        profile=profile,
    )
    selections_by_binding, evidence_by_binding = _validate_head_selection_records(
        final,
        expected_heads,
        final_operation_id,
        stores_by_id=stores_by_id,
        evidence_resolver=evidence_resolver,
        producer_world_resolver=producer_world_resolver,
        profile=profile,
    )
    candidate_commits = _validate_candidate_commit_records(final)

    outcomes = final.get("candidate_outcomes", [])
    if not isinstance(outcomes, list):
        raise InvalidRepositoryStateError("operation-final candidate_outcomes must be a list")
    outcome_keys: set[tuple[str, str, str, str]] = set()
    selected_outcomes_by_binding: dict[str, Mapping[str, Any]] = {}
    for outcome in outcomes:
        outcome_key = _candidate_outcome_key(outcome, final_operation_id)
        if outcome_key in outcome_keys:
            raise InvalidRepositoryStateError("operation-final candidate_outcomes contain duplicate candidate")
        outcome_keys.add(outcome_key)
        if isinstance(outcome, dict) and outcome.get("outcome") == "selected":
            binding = outcome.get("binding")
            if isinstance(binding, str):
                if binding in selected_outcomes_by_binding:
                    raise InvalidRepositoryStateError(
                        "operation-final candidate_outcomes contain duplicate selected binding"
                    )
                selected_outcomes_by_binding[binding] = outcome
        _validate_candidate_outcome(
            outcome,
            world,
            stores_by_id,
            expected_selected,
            selections_by_binding,
            evidence_by_binding,
            candidate_commits,
            final_operation_id,
            require_selected_candidate_refs=require_selected_candidate_refs,
            evidence_resolver=evidence_resolver,
            producer_world_resolver=producer_world_resolver,
            producer_world_validator=producer_world_validator,
            profile=profile,
        )
    if set(candidate_commits) != outcome_keys:
        raise InvalidRepositoryStateError("operation-final candidate_commits must match candidate_outcomes")
    if profile is WorldValidationProfile.DEEP:
        _validate_selected_head_semantics(
            _SelectionValidationContext(
                world=world,
                stores_by_id=stores_by_id,
                selected=expected_selected,
                operation_id=final_operation_id,
                input_world=input_world,
                selections_by_binding=selections_by_binding,
                evidence_by_binding=evidence_by_binding,
                candidate_commits=candidate_commits,
                selected_outcomes_by_binding=selected_outcomes_by_binding,
            )
        )


def _validated_input_world(
    world: WorldCommit,
    *,
    producer_world_resolver: Any,
    producer_world_validator: Any,
    validate_input_worlds: bool,
    profile: WorldValidationProfile,
) -> WorldCommit | None:
    input_world_oid = world.transition.get("input_world")
    if input_world_oid is None:
        return None
    if not isinstance(input_world_oid, str) or not input_world_oid:
        raise InvalidRepositoryStateError("world transition input_world must be a non-empty string")
    if profile is WorldValidationProfile.STRUCTURAL:
        return None
    if validate_input_worlds:
        producer_world_validator(input_world_oid)
    try:
        input_world = producer_world_resolver(input_world_oid)
    except (KeyError, TypeError, ValueError, InvalidRepositoryStateError) as exc:
        raise InvalidRepositoryStateError("world transition input_world is not a readable world") from exc
    if not isinstance(input_world, WorldCommit):
        raise InvalidRepositoryStateError("world transition input_world resolver returned invalid world")
    return input_world


def _validate_selected_head_semantics(context: _SelectionValidationContext) -> None:
    for binding, selection in context.selections_by_binding.items():
        if selection.selection_kind == "unchanged":
            if context.input_world is None:
                raise InvalidRepositoryStateError("root unchanged selection requires bootstrap")
            try:
                input_head = context.input_world.snapshot.head_for(binding)
                selected_head = context.world.snapshot.head_for(binding)
            except KeyError as exc:
                raise InvalidRepositoryStateError("unchanged selection binding is missing from input world") from exc
            validate_unchanged_head_identity(input_head=input_head, selected_head=selected_head)
            continue

        if selection.selection_kind == "new-candidate":
            outcome = _selected_candidate_outcome(context, binding)
            producer_operation_id = _candidate_producer_operation_id(outcome, context.operation_id)
            if producer_operation_id != context.operation_id:
                raise InvalidRepositoryStateError("new-candidate selection must be produced by current operation")
            continue

        if selection.selection_kind == "child-produced":
            outcome = _selected_candidate_outcome(context, binding)
            producer_world_oid = outcome.get("producer_world_oid")
            if not isinstance(producer_world_oid, str) or not producer_world_oid:
                raise InvalidRepositoryStateError("child-produced selection requires producer_world_oid")


def _selected_candidate_outcome(context: _SelectionValidationContext, binding: str) -> Mapping[str, Any]:
    outcome = context.selected_outcomes_by_binding.get(binding)
    if outcome is None:
        raise InvalidRepositoryStateError("candidate-backed selection requires selected candidate outcome")
    if outcome.get("candidate") != context.selected[binding]:
        raise InvalidRepositoryStateError("selected candidate outcome disagrees with world snapshot")
    return outcome


def _validate_head_selection_records(
    final: Mapping[str, Any],
    heads_by_binding: Mapping[str, SubstrateHead],
    operation_id: str,
    *,
    stores_by_id: Mapping[str, SubstrateStore],
    evidence_resolver: Any,
    producer_world_resolver: Any,
    profile: WorldValidationProfile,
) -> tuple[dict[str, HeadSelectionRecord], dict[str, HeadSelectionEvidence]]:
    raw_selections = final.get("head_selections")
    raw_evidence = final.get("selection_evidence")
    if not isinstance(raw_selections, list):
        raise InvalidRepositoryStateError("operation-final head_selections must be a list")
    if not isinstance(raw_evidence, list):
        raise InvalidRepositoryStateError("operation-final selection_evidence must be a list")

    selections_by_binding: dict[str, HeadSelectionRecord] = {}
    for item in raw_selections:
        if not isinstance(item, dict):
            raise InvalidRepositoryStateError("operation-final head selection entries must be objects")
        try:
            selection = HeadSelectionRecord.from_json(item)
        except (TypeError, ValueError) as exc:
            raise InvalidRepositoryStateError(str(exc)) from exc
        if selection.binding in selections_by_binding:
            raise InvalidRepositoryStateError(f"duplicate head selection for binding {selection.binding!r}")
        selections_by_binding[selection.binding] = selection

    evidence_by_binding: dict[str, HeadSelectionEvidence] = {}
    for item in raw_evidence:
        if not isinstance(item, dict):
            raise InvalidRepositoryStateError("operation-final selection evidence entries must be objects")
        try:
            evidence = HeadSelectionEvidence.from_json(item)
        except (TypeError, ValueError) as exc:
            raise InvalidRepositoryStateError(str(exc)) from exc
        if evidence.binding in evidence_by_binding:
            raise InvalidRepositoryStateError(f"duplicate selection evidence for binding {evidence.binding!r}")
        evidence_by_binding[evidence.binding] = evidence

    if set(selections_by_binding) != set(heads_by_binding):
        raise InvalidRepositoryStateError("operation-final head selections must explain every selected binding")
    if set(evidence_by_binding) != set(heads_by_binding):
        raise InvalidRepositoryStateError("operation-final selection evidence must explain every selected binding")
    for binding, head in heads_by_binding.items():
        selection = selections_by_binding[binding]
        evidence = evidence_by_binding[binding]
        if selection.store_id != head.store_id or evidence.store_id != head.store_id:
            raise InvalidRepositoryStateError("head selection store_id disagrees with world snapshot")
        if selection.resource_id != head.resource_id or evidence.resource_id != head.resource_id:
            raise InvalidRepositoryStateError("head selection resource_id disagrees with world snapshot")
        if selection.selected_head != head.head:
            raise InvalidRepositoryStateError("head selection selected_head disagrees with world snapshot")
        if evidence.operation_id != operation_id:
            raise InvalidRepositoryStateError("selection evidence operation_id disagrees with operation-final")
        if profile is WorldValidationProfile.DEEP:
            evidence_records = _validate_selection_evidence_refs(evidence, evidence_resolver=evidence_resolver)
        else:
            evidence_records = ()
        _validate_selection_relationship_requirements(selection, heads_by_binding, stores_by_id)
        if profile is WorldValidationProfile.DEEP:
            _validate_selection_retention_policy_requirements(
                selection,
                head,
                stores_by_id,
                producer_world_resolver=producer_world_resolver,
            )
        try:
            validate_head_selection(selection, evidence)
        except ValueError as exc:
            raise InvalidRepositoryStateError(str(exc)) from exc
        if profile is WorldValidationProfile.DEEP:
            _validate_non_candidate_selection_provenance(
                selection,
                evidence_records,
                stores_by_id,
                evidence_resolver=evidence_resolver,
            )
    return selections_by_binding, evidence_by_binding


def _validate_selection_evidence_refs(
    evidence: HeadSelectionEvidence,
    *,
    evidence_resolver: Any,
) -> tuple[EvidenceRecord, ...]:
    records: list[EvidenceRecord] = []
    for evidence_ref in evidence.evidence_refs:
        record = evidence_resolver(evidence_ref, expected_operation_id=evidence.operation_id)
        if record.binding is not None and record.binding != evidence.binding:
            raise InvalidRepositoryStateError("selection evidence ref binding disagrees with selection")
        if record.store_id is not None and record.store_id != evidence.store_id:
            raise InvalidRepositoryStateError("selection evidence ref store_id disagrees with selection")
        records.append(record)
    return tuple(records)


def _validate_non_candidate_selection_provenance(
    selection: HeadSelectionRecord,
    evidence_records: tuple[EvidenceRecord, ...],
    stores_by_id: Mapping[str, SubstrateStore],
    *,
    evidence_resolver: Any,
) -> None:
    if is_candidate_backed_selection_kind(selection.selection_kind):
        return
    if selection.selection_kind not in {"bootstrap", "checkpoint", "import", "revert"}:
        required_kinds = None
    else:
        required_kinds = allowed_existing_head_semantic_ops(cast("ExistingHeadSelectionKind", selection.selection_kind))
    if required_kinds is not None:
        matching_records = tuple(record for record in evidence_records if record.evidence_kind in required_kinds)
        if not matching_records:
            allowed = " or ".join(sorted(required_kinds))
            raise InvalidRepositoryStateError(f"{selection.selection_kind} selection requires {allowed} evidence")
        store = stores_by_id[selection.store_id]
        if not any(
            _is_coordinator_selection_evidence(record, selection=selection, store=store) for record in matching_records
        ):
            raise InvalidRepositoryStateError(
                f"{selection.selection_kind} selection evidence must exactly observe selected head as coordinator-owned evidence"
            )
        try:
            provenance = store.validate_prepared_revision(
                selection.selected_head,
                evidence_resolver=evidence_resolver,
            )
        except (InvalidRepositoryStateError, KeyError, TypeError, ValueError) as exc:
            raise InvalidRepositoryStateError(
                f"{selection.selection_kind} selection requires prepared revision provenance"
            ) from exc
        if provenance.transition.semantic_op not in required_kinds:
            allowed = " or ".join(sorted(required_kinds))
            raise InvalidRepositoryStateError(
                f"{selection.selection_kind} selection requires original {allowed} revision provenance"
            )
    if selection.selection_kind != "revert":
        return
    if selection.selected_from is None:
        raise InvalidRepositoryStateError("revert selection requires selected_from")
    store = stores_by_id[selection.store_id]
    if not _head_descends_from(
        store.repo, selected_head=selection.selected_from, required_head=selection.selected_head
    ):
        raise InvalidRepositoryStateError("revert selected_from must descend from selected_head")


def _is_coordinator_selection_evidence(
    record: EvidenceRecord,
    *,
    selection: HeadSelectionRecord,
    store: SubstrateStore,
) -> bool:
    if (
        record.ingress_kind != "coordinator"
        or record.binding != selection.binding
        or record.store_id != selection.store_id
        or record.substrate_kind != store.identity.kind
        or record.observed_head != selection.selected_head
    ):
        return False
    stable_observation: dict[str, object] = {
        "binding": selection.binding,
        "store_id": selection.store_id,
        "resource_id": selection.resource_id,
        "substrate_kind": store.identity.kind,
        "head": selection.selected_head,
        "kind": record.evidence_kind,
    }
    if selection.selected_from is not None:
        stable_observation["selected_from"] = selection.selected_from
    return record.stable_observation == stable_observation and record.payload_digest == canonical_digest(
        stable_observation
    )


def _validate_selection_relationship_requirements(
    selection: HeadSelectionRecord,
    heads_by_binding: Mapping[str, SubstrateHead],
    stores_by_id: Mapping[str, SubstrateStore],
) -> None:
    for requirement in selection.relationship_requirements:
        if requirement.binding != selection.binding:
            raise InvalidRepositoryStateError("selection relationship requirement binding disagrees with selection")
        try:
            target = heads_by_binding[requirement.target_binding]
        except KeyError as exc:
            raise InvalidRepositoryStateError(
                "selection relationship requirement target binding is not selected"
            ) from exc
        if requirement.relation == "exact":
            if target.head != requirement.target_head:
                raise InvalidRepositoryStateError(
                    "selection relationship requirement target head disagrees with world snapshot"
                )
            continue
        if requirement.relation == "descends-from":
            store = stores_by_id[target.store_id]
            if not _head_descends_from(store.repo, selected_head=target.head, required_head=requirement.target_head):
                raise InvalidRepositoryStateError(
                    "selection relationship requirement target head does not descend from required head"
                )
            continue
        raise InvalidRepositoryStateError(f"unsupported relationship relation: {requirement.relation!r}")


def _validate_selection_retention_policy_requirements(
    selection: HeadSelectionRecord,
    head: SubstrateHead,
    stores_by_id: Mapping[str, SubstrateStore],
    *,
    producer_world_resolver: Any,
) -> None:
    for requirement in selection.retention_policy_requirements:
        validate_retention_policy_kind(requirement)

    selected_head_pins = tuple(
        requirement for requirement in selection.retention_policy_requirements if requirement.kind == SELECTED_HEAD_PIN
    )
    if len(selected_head_pins) != 1:
        raise InvalidRepositoryStateError("selection requires exactly one selected-head-pin retention policy")
    selected_head_pin = selected_head_pins[0]
    if selected_head_pin.target != selection.selected_head:
        raise InvalidRepositoryStateError("selected-head-pin retention target must match selected head")
    if selected_head_pin.digest is not None:
        raise InvalidRepositoryStateError("selected-head-pin retention policy must not carry a digest")

    child_world_requirements = tuple(
        requirement
        for requirement in selection.retention_policy_requirements
        if requirement.kind == CHILD_WORLD_RETENTION
    )
    if not child_world_requirements and head.kind == WORLD_REF_SUBSTRATE_KIND:
        raise InvalidRepositoryStateError("world-ref selection requires child-world-retention")
    if not child_world_requirements:
        return
    if head.kind != WORLD_REF_SUBSTRATE_KIND:
        raise InvalidRepositoryStateError("child-world retention requires a vcscore.world_ref selected head")
    if len(child_world_requirements) != 1:
        raise InvalidRepositoryStateError("child-world retention requires exactly one child-world-retention policy")
    requirement = child_world_requirements[0]
    if not requirement.target.startswith("world:"):
        raise InvalidRepositoryStateError("child-world retention target must be world:<oid>")
    if requirement.digest is None:
        raise InvalidRepositoryStateError("child-world retention requires referenced world snapshot digest")
    try:
        store = stores_by_id[head.store_id]
    except KeyError as exc:
        raise InvalidRepositoryStateError("child-world retention selected head store is missing") from exc
    commit = require_commit(store.repo, pygit2.Oid(hex=head.head), context="world-ref substrate revision")
    try:
        payload = WorldRefPayload.from_json(_read_json_blob(store.repo, commit.tree, "revision.json"))
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidRepositoryStateError("invalid world-ref substrate payload") from exc
    if requirement.target.removeprefix("world:") != payload.world_oid:
        raise InvalidRepositoryStateError("child-world retention target disagrees with world-ref payload")
    if requirement.digest != payload.snapshot_digest:
        raise InvalidRepositoryStateError("child-world retention digest disagrees with world-ref payload")
    try:
        referenced = producer_world_resolver(payload.world_oid)
    except (KeyError, TypeError, ValueError, InvalidRepositoryStateError) as exc:
        raise InvalidRepositoryStateError("child-world retention target is not a readable world") from exc
    if not isinstance(referenced, WorldCommit):
        raise InvalidRepositoryStateError("child-world retention resolver returned invalid world")
    if referenced.snapshot.digest() != payload.snapshot_digest:
        raise InvalidRepositoryStateError("child-world retention snapshot digest disagrees with referenced world")


def _head_descends_from(repo: pygit2.Repository, *, selected_head: str, required_head: str) -> bool:
    if selected_head == required_head:
        return True
    try:
        selected = pygit2.Oid(hex=selected_head)
        required = pygit2.Oid(hex=required_head)
    except ValueError as exc:
        raise InvalidRepositoryStateError("relationship requirement contains malformed head") from exc
    if not isinstance(repo.get(selected), pygit2.Commit) or not isinstance(repo.get(required), pygit2.Commit):
        raise InvalidRepositoryStateError("relationship requirement names a missing commit")
    return bool(repo.descendant_of(selected, required))


def _relationship_requirement_digests(requirements: tuple[Any, ...]) -> list[str]:
    return sorted(canonical_digest(requirement.to_json()) for requirement in requirements)


def _validate_candidate_commit_records(
    final: Mapping[str, Any],
) -> dict[tuple[str, str, str, str], CandidateCommitRecord]:
    raw_commits = final.get("candidate_commits")
    if not isinstance(raw_commits, list):
        raise InvalidRepositoryStateError("operation-final candidate_commits must be a list")
    records: dict[tuple[str, str, str, str], CandidateCommitRecord] = {}
    for item in raw_commits:
        if not isinstance(item, dict):
            raise InvalidRepositoryStateError("operation-final candidate commit entries must be objects")
        try:
            record = CandidateCommitRecord.from_json(item)
        except (TypeError, ValueError) as exc:
            raise InvalidRepositoryStateError(str(exc)) from exc
        expected_ref = candidate_ref(record.operation_id, record.binding, record.candidate_id)
        if record.candidate_ref != expected_ref:
            raise InvalidRepositoryStateError("candidate commit record candidate_ref disagrees with producer operation")
        key = (record.operation_id, record.binding, record.candidate_id, record.candidate_head)
        if key in records:
            raise InvalidRepositoryStateError("duplicate candidate commit record")
        records[key] = record
    return records


def _validate_candidate_outcome(
    outcome: object,
    world: WorldCommit,
    stores_by_id: Mapping[str, SubstrateStore],
    selected: Mapping[str, str],
    selections_by_binding: Mapping[str, HeadSelectionRecord],
    evidence_by_binding: Mapping[str, HeadSelectionEvidence],
    candidate_commits: Mapping[tuple[str, str, str, str], CandidateCommitRecord],
    operation_id: str,
    *,
    require_selected_candidate_refs: bool,
    evidence_resolver: Any,
    producer_world_resolver: Any,
    producer_world_validator: Any,
    profile: WorldValidationProfile,
) -> None:
    if not isinstance(outcome, dict):
        raise InvalidRepositoryStateError("operation-final candidate outcome must be an object")
    try:
        outcome_record = CandidateOutcomeRecord.from_operation_final_json(outcome)
    except (TypeError, ValueError) as exc:
        raise InvalidRepositoryStateError(str(exc)) from exc
    candidate = outcome_record.candidate
    binding = outcome_record.binding
    status = outcome_record.outcome
    if binding not in selected:
        raise InvalidRepositoryStateError(f"candidate outcome names unknown binding {binding!r}")
    producer_operation_id = outcome_record.producer_operation_id or operation_id
    candidate_id = outcome_record.candidate_id
    record = candidate_commits.get((producer_operation_id, binding, candidate_id, candidate))
    if record is None:
        raise InvalidRepositoryStateError("candidate outcome lacks matching candidate commit record")
    head = world.snapshot.head_for(binding)
    store = stores_by_id[head.store_id]
    if record.store_id != head.store_id:
        raise InvalidRepositoryStateError("candidate commit record store_id disagrees with world snapshot")
    if record.resource_id != head.resource_id:
        raise InvalidRepositoryStateError("candidate commit record resource_id disagrees with world snapshot")
    if status == "selected" and candidate != selected[binding]:
        raise InvalidRepositoryStateError("selected candidate outcome disagrees with world snapshot")
    if profile is WorldValidationProfile.STRUCTURAL:
        if status == "archived" and candidate == selected[binding]:
            raise InvalidRepositoryStateError("archived candidate outcome must not name selected head")
        return
    provenance = store.validate_prepared_candidate(
        candidate,
        expected_revision_preparation_digest=record.revision_preparation_digest,
        evidence_resolver=evidence_resolver,
    )
    _validate_candidate_commit_provenance(record, provenance)
    _validate_candidate_outcome_provenance(outcome_record, record, provenance)
    if status == "selected":
        selection = selections_by_binding[binding]
        evidence = evidence_by_binding[binding]
        if not is_candidate_backed_selection_kind(selection.selection_kind):
            raise InvalidRepositoryStateError("selected candidate outcome requires candidate-backed head selection")
        if evidence.revision_preparation_digest != record.revision_preparation_digest:
            raise InvalidRepositoryStateError("selection evidence revision_preparation_digest disagrees with commit")
        if evidence.candidate_commit_digest != record.candidate_commit_digest():
            raise InvalidRepositoryStateError("selection evidence candidate_commit_digest disagrees with commit")
        if evidence.candidate_ref != record.candidate_ref:
            raise InvalidRepositoryStateError("selection evidence candidate_ref disagrees with commit")
        if _relationship_requirement_digests(selection.relationship_requirements) != _relationship_requirement_digests(
            provenance.preparation.relationship_requirements
        ):
            raise InvalidRepositoryStateError("selected candidate relationship requirements disagree with preparation")
        if selection.selection_kind == "child-produced":
            producer_world_oid = outcome_record.producer_world_oid
            if producer_world_oid is None:
                raise InvalidRepositoryStateError("child-produced selection requires producer_world_oid")
            if evidence.producer_operation_id != producer_operation_id:
                raise InvalidRepositoryStateError(
                    "child-produced evidence producer_operation_id disagrees with outcome"
                )
            _validate_child_produced_world(
                world,
                producer_world_oid=producer_world_oid,
                producer_operation_id=producer_operation_id,
                binding=binding,
                candidate_id=candidate_id,
                candidate=candidate,
                producer_world_resolver=producer_world_resolver,
                producer_world_validator=producer_world_validator,
            )
        elif evidence.producer_operation_id not in {None, producer_operation_id}:
            raise InvalidRepositoryStateError("selection evidence producer_operation_id disagrees with outcome")
        if require_selected_candidate_refs and not _ref_targets(store.repo, record.candidate_ref, candidate):
            raise InvalidRepositoryStateError("selected candidate outcome lacks a durable candidate ref")
        return
    if candidate == selected[binding]:
        raise InvalidRepositoryStateError("archived candidate outcome must not name selected head")
    if not (
        _ref_targets(store.repo, candidate_archive_ref(operation_id, binding, candidate_id), candidate)
        or _ref_targets(store.repo, candidate_archive_ref(producer_operation_id, binding, candidate_id), candidate)
        or _ref_targets(store.repo, record.candidate_ref, candidate)
    ):
        raise InvalidRepositoryStateError("archived candidate outcome lacks a durable candidate or archive ref")


def _validate_child_produced_world(
    world: WorldCommit,
    *,
    producer_world_oid: str,
    producer_operation_id: str,
    binding: str,
    candidate_id: str,
    candidate: str,
    producer_world_resolver: Any,
    producer_world_validator: Any,
) -> None:
    if producer_world_oid not in world.parent_oids:
        raise InvalidRepositoryStateError("child-produced producer_world_oid must be a parent world")
    producer_world_validator(producer_world_oid)
    try:
        producer_world = producer_world_resolver(producer_world_oid)
    except (KeyError, TypeError, ValueError, InvalidRepositoryStateError) as exc:
        raise InvalidRepositoryStateError("child-produced producer_world_oid is not a readable world") from exc
    producer_final_operation_id = producer_world.operation_final.get("operation_id")
    if producer_final_operation_id != producer_operation_id:
        raise InvalidRepositoryStateError("child-produced producer world operation_id disagrees with outcome")
    for raw_outcome in producer_world.operation_final.get("candidate_outcomes", []):
        if not isinstance(raw_outcome, dict):
            continue
        try:
            outcome = CandidateOutcomeRecord.from_operation_final_json(raw_outcome)
        except (TypeError, ValueError):
            continue
        if (
            outcome.binding == binding
            and outcome.candidate_id == candidate_id
            and outcome.candidate == candidate
            and outcome.outcome == "selected"
        ):
            return
    raise InvalidRepositoryStateError("child-produced producer world does not select candidate")


def _world_validation_profile(profile: WorldValidationProfile | str) -> WorldValidationProfile:
    if isinstance(profile, WorldValidationProfile):
        return profile
    try:
        return WorldValidationProfile(profile)
    except ValueError as exc:
        raise InvalidRepositoryStateError(f"unsupported world validation profile: {profile!r}") from exc


def _validate_candidate_commit_provenance(
    record: CandidateCommitRecord,
    provenance: PreparedCandidateProvenance,
) -> None:
    preparation = provenance.preparation
    metadata = provenance.metadata
    if record.operation_id != preparation.operation_id or metadata.produced_by_operation_id != record.operation_id:
        raise InvalidRepositoryStateError(
            "candidate commit record producer operation_id disagrees with prepared candidate"
        )
    if record.binding != preparation.binding:
        raise InvalidRepositoryStateError("candidate commit record binding disagrees with prepared candidate")
    if record.store_id != preparation.store_id:
        raise InvalidRepositoryStateError("candidate commit record store_id disagrees with prepared candidate")
    if record.resource_id != preparation.resource_id:
        raise InvalidRepositoryStateError("candidate commit record resource_id disagrees with prepared candidate")
    if record.candidate_head != provenance.head:
        raise InvalidRepositoryStateError("candidate commit record candidate_head disagrees with prepared candidate")
    if record.revision_preparation_digest != preparation.revision_preparation_digest():
        raise InvalidRepositoryStateError(
            "candidate commit record revision_preparation_digest disagrees with prepared candidate"
        )


def _validate_candidate_outcome_provenance(
    outcome: CandidateOutcomeRecord,
    record: CandidateCommitRecord,
    provenance: PreparedCandidateProvenance,
) -> None:
    if outcome.store_id is None:
        raise InvalidRepositoryStateError("candidate outcome must include store_id")
    if outcome.store_id != record.store_id:
        raise InvalidRepositoryStateError("candidate outcome store_id disagrees with candidate commit")
    if outcome.resource_id is None:
        raise InvalidRepositoryStateError("candidate outcome must include resource_id")
    if outcome.resource_id != record.resource_id:
        raise InvalidRepositoryStateError("candidate outcome resource_id disagrees with candidate commit")
    if outcome.transition_digest is None:
        raise InvalidRepositoryStateError("candidate outcome must include transition_digest")
    if outcome.transition_digest != provenance.transition.transition_digest():
        raise InvalidRepositoryStateError("candidate outcome transition_digest disagrees with prepared candidate")
    if outcome.revision_plan_digest is None:
        raise InvalidRepositoryStateError("candidate outcome must include revision_plan_digest")
    if outcome.revision_plan_digest != provenance.plan.revision_plan_digest():
        raise InvalidRepositoryStateError("candidate outcome revision_plan_digest disagrees with prepared candidate")
    if outcome.content_digest is None:
        raise InvalidRepositoryStateError("candidate outcome must include content_digest")
    if outcome.content_digest != provenance.plan.content_digest:
        raise InvalidRepositoryStateError("candidate outcome content_digest disagrees with prepared candidate")
    if outcome.revision_preparation_digest is None:
        raise InvalidRepositoryStateError("candidate outcome must include revision_preparation_digest")
    if outcome.revision_preparation_digest != provenance.preparation.revision_preparation_digest():
        raise InvalidRepositoryStateError(
            "candidate outcome revision_preparation_digest disagrees with prepared candidate"
        )
    if outcome.candidate_commit_digest is None:
        raise InvalidRepositoryStateError("candidate outcome must include candidate_commit_digest")
    if outcome.candidate_commit_digest != record.candidate_commit_digest():
        raise InvalidRepositoryStateError("candidate outcome candidate_commit_digest disagrees with candidate commit")
    if not outcome.evidence_digests:
        raise InvalidRepositoryStateError("candidate outcome must include evidence_digests")
    if sorted(outcome.evidence_digests) != sorted(provenance.preparation.evidence_digests):
        raise InvalidRepositoryStateError("candidate outcome evidence_digests disagree with prepared candidate")
    if not outcome.evidence_refs:
        raise InvalidRepositoryStateError("candidate outcome must include evidence_refs")
    if sorted(canonical_digest(ref.to_json()) for ref in outcome.evidence_refs) != sorted(
        canonical_digest(ref.to_json()) for ref in provenance.preparation.evidence_refs
    ):
        raise InvalidRepositoryStateError("candidate outcome evidence_refs disagree with prepared candidate")


def _candidate_outcome_key(outcome: object, final_operation_id: str) -> tuple[str, str, str, str]:
    if not isinstance(outcome, dict):
        raise InvalidRepositoryStateError("operation-final candidate outcome must be an object")
    candidate = outcome.get("candidate")
    binding = outcome.get("binding")
    if not isinstance(candidate, str) or not isinstance(binding, str):
        raise InvalidRepositoryStateError("candidate outcome must include string binding and candidate")
    return (_candidate_producer_operation_id(outcome, final_operation_id), binding, _candidate_id(outcome), candidate)


def _candidate_producer_operation_id(outcome: Mapping[str, Any], final_operation_id: str) -> str:
    producer_operation_id = outcome.get("producer_operation_id", final_operation_id)
    if not isinstance(producer_operation_id, str) or not producer_operation_id:
        raise InvalidRepositoryStateError("candidate outcome producer_operation_id must be a non-empty string")
    return producer_operation_id


def _candidate_id(outcome: Mapping[str, Any]) -> str:
    candidate_id = outcome.get("candidate_id", "primary")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise InvalidRepositoryStateError("candidate outcome candidate_id must be a non-empty string")
    return candidate_id


def _ref_targets(repo: pygit2.Repository, ref: str, oid: str) -> bool:
    try:
        return str(repo.references[ref].target) == oid
    except KeyError:
        return False


def _world_is_reachable_from_refs(repo: pygit2.Repository, oid: str, authority_refs: tuple[str, ...]) -> bool:
    try:
        world_oid = pygit2.Oid(hex=oid)
    except ValueError:
        return False
    if not isinstance(repo.get(world_oid), pygit2.Commit):
        return False
    for ref in authority_refs:
        try:
            target = repo.references[ref].target
        except KeyError:
            continue
        if target == world_oid:
            return True
        if isinstance(repo.get(target), pygit2.Commit) and repo.descendant_of(target, world_oid):
            return True
    return False


def _current_ref_target(repo: pygit2.Repository, ref: str) -> str | None:
    try:
        return str(repo.references[ref].target)
    except KeyError:
        return None


def _validated_oid(value: str, *, context: str) -> pygit2.Oid:
    try:
        return pygit2.Oid(hex=value)
    except ValueError as exc:
        raise InvalidRepositoryStateError(f"{context} is not a valid object id: {value!r}") from exc


def _validated_commit_oid(repo: pygit2.Repository, value: str, *, context: str) -> pygit2.Oid:
    oid = _validated_oid(value, context=context)
    try:
        require_commit(repo, oid, context=context)
    except (KeyError, TypeError) as exc:
        raise InvalidRepositoryStateError(f"{context} must be an existing commit: {value!r}") from exc
    return oid


def _stores_by_id(bound_stores: Mapping[str, SubstrateStore]) -> dict[str, SubstrateStore]:
    stores: dict[str, SubstrateStore] = {}
    for store in bound_stores.values():
        prior = stores.get(store.identity.store_id)
        if prior is not None and prior is not store:
            raise InvalidRepositoryStateError(f"duplicate substrate store binding for {store.identity.store_id!r}")
        stores[store.identity.store_id] = store
    return stores


def _validate_head_identity(head: SubstrateHead, store: SubstrateStore) -> None:
    identity = store.identity
    mismatches = [
        key
        for key, expected, actual in (
            ("store_id", identity.store_id, head.store_id),
            ("kind", identity.kind, head.kind),
            ("resource_id", identity.resource_id, head.resource_id),
            ("object_format", identity.object_format, head.object_format),
        )
        if expected != actual
    ]
    if mismatches:
        raise InvalidRepositoryStateError(f"substrate identity mismatch for {', '.join(mismatches)}")
