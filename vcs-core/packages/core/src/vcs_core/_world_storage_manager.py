"""Installation-local manager for v2 world storage."""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pygit2

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._incremental import ActiveLeaseIndex, Health, OpenOperationJournalIndex, atomic_co_write
from vcs_core._pygit2_helpers import require_blob, require_commit
from vcs_core._substrate_store import SubstrateStore
from vcs_core._transition_kernel_records import (
    EvidenceRef,
    RetainedRef,
    RetentionPolicyRequirement,
    RevisionPreparationRecord,
)
from vcs_core._world_closure import ClosureEvidenceRef, ClosureHead, ClosureWorld, WorldClosure, compute_world_closure
from vcs_core._world_operation_journal import OperationJournalStore
from vcs_core._world_publication_plan import PublicationPlan
from vcs_core._world_refs import (
    is_open_operation_journal_ref,
    operation_journal_ref,
    world_fork_origin_receipt_ref,
    world_open_operation_journal_index_ref,
    world_pin_ref,
    world_publication_lease_index_ref,
    world_publication_lease_prefix,
    world_publication_lease_ref,
    world_retention_receipt_ref,
)
from vcs_core._world_retention import (
    CHILD_WORLD_RETENTION,
    EVIDENCE_REF,
    SELECTED_HEAD_PIN,
    validate_retained_ref,
)
from vcs_core._world_store import WorldStore, WorldValidationProfile
from vcs_core._world_transition_coordinator import (
    CoordinatorEvidenceOnlyIngress,
    WorldTransitionCoordinator,
    WorldTransitionCoordinatorProtocol,
)
from vcs_core._world_types import (
    OPERATION_FINAL_SCHEMA,
    CandidateRevision,
    OperationFinalRecord,
    StructuredIssue,
    SubstrateHead,
    SubstrateStoreIdentity,
    WorldCommit,
    WorldRefPayload,
    WorldSnapshot,
    canonical_bytes,
    canonical_digest,
    compact_json_bytes,
    load_canonical_json,
)
from vcs_core.git_store import create_commit_with_recovery, create_or_update_reference, insert_tree_entry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from vcs_core._substrate_driver import (
        DriverContext,
        DriverIngressResult,
        IngressRequest,
        ReductionBatch,
        SubstrateDriver,
    )
    from vcs_core._transition_kernel import TransitionKernelDriver
    from vcs_core._transition_kernel_records import (
        CandidateCommitRecord,
        LogicalTransition,
        PreparedRevisionPlan,
        RelationshipRequirement,
    )
    from vcs_core._world_operation_builder import (
        CandidateSelection,
        CandidateSelectionPlan,
        PreparedCandidateTupleRecord,
        PreparedWorldOperation,
        SelectionRequirementPlan,
    )
    from vcs_core._world_operation_journal import (
        OperationJournalEntry,
        OperationJournalHistory,
        OperationJournalSummary,
    )

INSTALLATION_SCHEMA = "vcscore/world-storage-installation/v1"
WORLD_RETENTION_RECEIPT_SCHEMA = "vcscore/world-retention-receipt/v1"
WORLD_RETENTION_RECEIPT_PATH = "meta/world-retention-receipt.json"
WORLD_PUBLICATION_LEASE_SCHEMA = "vcscore/world-publication-lease/v1"
WORLD_PUBLICATION_LEASE_PATH = "meta/world-publication-lease.json"
WORLD_FORK_ORIGIN_RECEIPT_SCHEMA = "vcscore/world-fork-origin-receipt/v1"
WORLD_FORK_ORIGIN_RECEIPT_PATH = "meta/world-fork-origin-receipt.json"
DEFAULT_COORDINATOR_LOCATOR = "worlds.git"
DEFAULT_GROUND_REF = "refs/vcscore/ground"


@dataclass(frozen=True)
class SubstrateStoreSpec:
    """Installation-local locator plus stable substrate store identity."""

    identity: SubstrateStoreIdentity
    locator: str

    def __post_init__(self) -> None:
        _validate_relative_locator(self.locator)

    def to_json(self) -> dict[str, object]:
        return {
            "identity": self.identity.to_json(),
            "locator": self.locator,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> SubstrateStoreSpec:
        raw_identity = value.get("identity")
        if not isinstance(raw_identity, dict):
            raise TypeError("substrate store spec identity must be an object")
        locator = value.get("locator")
        if not isinstance(locator, str) or not locator:
            raise ValueError("substrate store spec locator is required")
        return cls(identity=SubstrateStoreIdentity.from_json(raw_identity), locator=locator)


@dataclass(frozen=True)
class WorldFsckReport:
    """Validation and pin-health report for one world commit."""

    world_oid: str
    pin_classification: dict[str, tuple[str, ...]]
    issue_details: tuple[StructuredIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issue_details

    @property
    def issues(self) -> tuple[str, ...]:
        return tuple(issue.message for issue in self.issue_details)


@dataclass(frozen=True)
class OperationJournalFsckReport:
    """Validation report for one operation journal."""

    operation_id: str
    issue_details: tuple[StructuredIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issue_details

    @property
    def issues(self) -> tuple[str, ...]:
        return tuple(issue.message for issue in self.issue_details)


@dataclass(frozen=True)
class OperationJournalsFsckReport:
    """Store-global integrity report over EVERY operation-journal ref.

    Covers all families, including unknown/unsupported ones and corrupt terminals. This is the
    canonical, explicit, off-hot-path home for terminal-journal integrity: open-only admission
    deliberately stops *blocking* on corrupt non-`open` journals (over-blocking), but their
    *detection* must not be lost — it surfaces here (and via the read-only inspect path) instead.
    Distinct from the per-operation :class:`OperationJournalFsckReport`. ``scanned`` is the number
    of v2-shaped refs walked.
    """

    scanned: int
    issue_details: tuple[StructuredIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issue_details

    @property
    def issues(self) -> tuple[str, ...]:
        return tuple(issue.message for issue in self.issue_details)


@dataclass(frozen=True)
class OperationFinalEvidence:
    """Final operation evidence derived from an immutable world commit."""

    operation_id: str
    operation_final_digest: str
    selected: dict[str, str]
    candidate_outcomes: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class SelectedHeadCandidateProvenance:
    """Candidate custody for a selected binding head, traced through unchanged worlds."""

    world_oid: str
    producer_world_oid: str
    producer_operation_id: str
    selected_head: SubstrateHead
    candidate_tuple: PreparedCandidateTupleRecord


@dataclass(frozen=True)
class PreparedCandidateBundle:
    """Manager-produced candidate plus typed evidence needed by operation-final records."""

    candidate: CandidateRevision
    candidate_commit: CandidateCommitRecord
    transition: LogicalTransition
    plan: PreparedRevisionPlan
    preparation: RevisionPreparationRecord


@dataclass(frozen=True)
class PreparedRevisionBundle:
    """Manager-produced non-candidate revision plus typed provenance records."""

    head: str
    ref: str
    transition: LogicalTransition
    plan: PreparedRevisionPlan
    preparation: RevisionPreparationRecord


@dataclass(frozen=True)
class PreparedPublication:
    """Publication side effects prepared before the authority-ref CAS."""

    plan: PublicationPlan
    lease_refs: tuple[str, ...]


@dataclass(frozen=True)
class _ProtectedRetention:
    world_oids: frozenset[str]
    refs: frozenset[str]


@dataclass(frozen=True)
class _PublicationLease:
    authority_ref: str
    world_store_id: str
    world_oid: str
    operation_id: str
    created_at_unix_ns: int

    def to_json(self) -> dict[str, object]:
        payload = {
            "schema": WORLD_PUBLICATION_LEASE_SCHEMA,
            "authority_ref": self.authority_ref,
            "world_store_id": self.world_store_id,
            "world_oid": self.world_oid,
            "operation_id": self.operation_id,
            "created_at_unix_ns": self.created_at_unix_ns,
        }
        return {**payload, "lease_digest": canonical_digest(payload)}


@dataclass(frozen=True)
class _ForkOriginReceipt:
    authority_ref: str
    world_store_id: str
    first_world_oid: str
    forked_from_authority_ref: str
    forked_from_world_oid: str

    def to_json(self) -> dict[str, object]:
        payload = {
            "schema": WORLD_FORK_ORIGIN_RECEIPT_SCHEMA,
            "authority_ref": self.authority_ref,
            "world_store_id": self.world_store_id,
            "first_world_oid": self.first_world_oid,
            "forked_from_authority_ref": self.forked_from_authority_ref,
            "forked_from_world_oid": self.forked_from_world_oid,
        }
        return {**payload, "receipt_digest": canonical_digest(payload)}


@dataclass(frozen=True)
class _AuthorityLineageSegments:
    local_world_oids: tuple[str, ...]
    fork_origin: _ForkOriginReceipt | None = None
    corrupt_fork_origin: str | None = None


class WorldStorageManager:
    """Private manager that binds one v2 world installation to its stores."""

    def __init__(
        self,
        *,
        root: Path,
        world_store: WorldStore,
        store_specs: Mapping[str, SubstrateStoreSpec],
        stores: Mapping[str, SubstrateStore],
    ) -> None:
        self._root = root
        self._world_store = world_store
        self._operation_journal = OperationJournalStore(world_store.repo)
        self._store_specs = dict(store_specs)
        self._stores = dict(stores)
        self._transition_coordinator: WorldTransitionCoordinatorProtocol = WorldTransitionCoordinator(
            world_store=world_store,
            stores=self._stores,
        )

    @classmethod
    def open_or_init(
        cls,
        root: str | Path,
        *,
        world_store_id: str,
        stores: tuple[SubstrateStoreSpec, ...],
        substrate_shared_object_repo_path: str | Path | None = None,
    ) -> WorldStorageManager:
        """Open or initialize a v2 world installation.

        ``substrate_shared_object_repo_path``, when set, points at a Git
        repository whose ODB substrate stores need to read from.  In the
        production install, this is the scalar vcs-core store: the workspace
        bytes used by tree-backed substrate revisions live there, and the
        substrate store needs alternates pointing at it before libgit2 will
        accept a ``workspace/`` tree entry referencing a foreign tree oid.
        """
        root_path = Path(root)
        specs_by_id = _specs_by_id(stores)
        root_path.mkdir(parents=True, exist_ok=True)
        if _installation_config_path(root_path).exists():
            _validate_installation_config(root_path, world_store_id=world_store_id, specs_by_id=specs_by_id)
            world_store = WorldStore.open_existing(
                root_path / DEFAULT_COORDINATOR_LOCATOR,
                world_store_id=world_store_id,
            )
            substrate_stores = {
                store_id: SubstrateStore.open_existing(
                    root_path / spec.locator,
                    spec.identity,
                    shared_object_repo_path=substrate_shared_object_repo_path,
                )
                for store_id, spec in specs_by_id.items()
            }
        else:
            _write_installation_config(root_path, world_store_id=world_store_id, specs_by_id=specs_by_id)
            world_store = WorldStore.open_or_init(
                root_path / DEFAULT_COORDINATOR_LOCATOR,
                world_store_id=world_store_id,
            )
            substrate_stores = {
                store_id: SubstrateStore.open_or_init(
                    root_path / spec.locator,
                    spec.identity,
                    shared_object_repo_path=substrate_shared_object_repo_path,
                )
                for store_id, spec in specs_by_id.items()
            }
        return cls(root=root_path, world_store=world_store, store_specs=specs_by_id, stores=substrate_stores)

    @classmethod
    def open_existing(
        cls,
        root: str | Path,
        *,
        world_store_id: str,
        stores: tuple[SubstrateStoreSpec, ...],
        substrate_shared_object_repo_path: str | Path | None = None,
    ) -> WorldStorageManager:
        """Open an existing v2 world installation without creating filesystem state.

        See :meth:`open_or_init` for the ``substrate_shared_object_repo_path``
        alternates contract.
        """
        root_path = Path(root)
        specs_by_id = _specs_by_id(stores)
        _validate_installation_config(root_path, world_store_id=world_store_id, specs_by_id=specs_by_id)
        world_store = WorldStore.open_existing(
            root_path / DEFAULT_COORDINATOR_LOCATOR,
            world_store_id=world_store_id,
        )
        substrate_stores = {
            store_id: SubstrateStore.open_existing(
                root_path / spec.locator,
                spec.identity,
                shared_object_repo_path=substrate_shared_object_repo_path,
            )
            for store_id, spec in specs_by_id.items()
        }
        return cls(root=root_path, world_store=world_store, store_specs=specs_by_id, stores=substrate_stores)

    @classmethod
    def rebind_store_locator(
        cls,
        root: str | Path,
        *,
        world_store_id: str,
        store_id: str,
        locator: str,
    ) -> None:
        """Rewrite one install-local locator after validating the target existing store."""
        root_path = Path(root)
        _validate_relative_locator(locator)
        current = _read_installation_config(root_path)
        if current.get("world_store_id") != world_store_id:
            raise InvalidRepositoryStateError("world storage installation world_store_id mismatch")
        specs_by_id = _store_specs_from_config(current)
        try:
            current_spec = specs_by_id[store_id]
        except KeyError as exc:
            raise InvalidRepositoryStateError(f"world storage installation has no store {store_id!r}") from exc
        target_path = root_path / locator
        SubstrateStore.open_existing(target_path, current_spec.identity)
        next_specs = {
            existing_store_id: (
                SubstrateStoreSpec(identity=spec.identity, locator=locator) if existing_store_id == store_id else spec
            )
            for existing_store_id, spec in specs_by_id.items()
        }
        _write_installation_config(root_path, world_store_id=world_store_id, specs_by_id=next_specs)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def world_store(self) -> WorldStore:
        return self._world_store

    @property
    def stores(self) -> dict[str, SubstrateStore]:
        return dict(self._stores)

    def store(self, store_id: str) -> SubstrateStore:
        try:
            return self._stores[store_id]
        except KeyError as exc:
            raise KeyError(f"world installation has no substrate store {store_id!r}") from exc

    def locator_hints(self) -> dict[str, str]:
        return {store_id: spec.locator for store_id, spec in sorted(self._store_specs.items())}

    def create_unsafe_unprepared_json_revision(
        self,
        store_id: str,
        ref: str,
        payload: dict[str, Any],
        *,
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
    ) -> str:
        """Create a provenance-free JSON revision for tests and migration tools."""
        return self.store(store_id).create_unsafe_unprepared_json_revision(
            ref, payload, parents=parents, message=message
        )

    def create_unsafe_unprepared_candidate(
        self,
        store_id: str,
        *,
        operation_id: str,
        binding: str,
        candidate_id: str = "primary",
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
    ) -> CandidateRevision:
        """Create a legacy candidate ref without full transition-kernel sidecars."""
        return self.store(store_id).create_unsafe_unprepared_candidate(
            operation_id=operation_id,
            binding=binding,
            candidate_id=candidate_id,
            payload=payload,
            parents=parents,
            message=message,
        )

    def create_prepared_json_candidate(
        self,
        store_id: str,
        *,
        operation_id: str,
        binding: str,
        candidate_id: str = "primary",
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...] = (),
        ingress_kind: str = "command",
        semantic_op: str = "json-revision",
        driver: TransitionKernelDriver | None = None,
        relationship_requirements: tuple[RelationshipRequirement, ...] = (),
        message: str | None = None,
    ) -> tuple[CandidateRevision, CandidateCommitRecord]:
        bundle = self.create_prepared_json_candidate_bundle(
            store_id,
            operation_id=operation_id,
            binding=binding,
            candidate_id=candidate_id,
            payload=payload,
            parents=parents,
            ingress_kind=ingress_kind,
            semantic_op=semantic_op,
            driver=driver,
            relationship_requirements=relationship_requirements,
            message=message,
        )
        return bundle.candidate, bundle.candidate_commit

    def create_prepared_json_revision(
        self,
        store_id: str,
        ref: str,
        *,
        operation_id: str,
        binding: str,
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...] = (),
        ingress_kind: str = "command",
        semantic_op: str = "json-revision",
        driver: TransitionKernelDriver | None = None,
        relationship_requirements: tuple[RelationshipRequirement, ...] = (),
        message: str | None = None,
    ) -> str:
        return self.create_prepared_json_revision_bundle(
            store_id,
            ref,
            operation_id=operation_id,
            binding=binding,
            payload=payload,
            parents=parents,
            ingress_kind=ingress_kind,
            semantic_op=semantic_op,
            driver=driver,
            relationship_requirements=relationship_requirements,
            message=message,
        ).head

    def create_prepared_json_revision_bundle(
        self,
        store_id: str,
        ref: str,
        *,
        operation_id: str,
        binding: str,
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...] = (),
        ingress_kind: str = "command",
        semantic_op: str = "json-revision",
        driver: TransitionKernelDriver | None = None,
        relationship_requirements: tuple[RelationshipRequirement, ...] = (),
        message: str | None = None,
    ) -> PreparedRevisionBundle:
        prepared = self._transition_coordinator.create_prepared_json_revision(
            store_id,
            ref,
            operation_id=operation_id,
            binding=binding,
            payload=payload,
            parents=parents,
            ingress_kind=ingress_kind,
            semantic_op=semantic_op,
            driver=driver,
            relationship_requirements=relationship_requirements,
            message=message,
        )
        return PreparedRevisionBundle(
            head=prepared.head,
            ref=prepared.ref,
            transition=prepared.transition,
            plan=prepared.plan,
            preparation=prepared.preparation,
        )

    def create_prepared_driver_revision_bundle(
        self,
        store_id: str,
        ref: str,
        *,
        operation_id: str,
        binding: str,
        result: DriverIngressResult,
        driver_id: str,
        driver_version: str,
        parents: tuple[str | pygit2.Oid, ...] = (),
        ingress_kind: str = "command",
        relationship_requirements: tuple[RelationshipRequirement, ...] = (),
        reduction_batch: ReductionBatch | None = None,
        message: str | None = None,
    ) -> PreparedRevisionBundle:
        prepared = self._transition_coordinator.create_prepared_driver_revision(
            store_id,
            ref,
            operation_id=operation_id,
            binding=binding,
            result=result,
            driver_id=driver_id,
            driver_version=driver_version,
            parents=parents,
            ingress_kind=ingress_kind,
            relationship_requirements=relationship_requirements,
            reduction_batch=reduction_batch,
            message=message,
        )
        return PreparedRevisionBundle(
            head=prepared.head,
            ref=prepared.ref,
            transition=prepared.transition,
            plan=prepared.plan,
            preparation=prepared.preparation,
        )

    def create_prepared_json_candidate_bundle(
        self,
        store_id: str,
        *,
        operation_id: str,
        binding: str,
        candidate_id: str = "primary",
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...] = (),
        ingress_kind: str = "command",
        semantic_op: str = "json-revision",
        driver: TransitionKernelDriver | None = None,
        relationship_requirements: tuple[RelationshipRequirement, ...] = (),
        message: str | None = None,
    ) -> PreparedCandidateBundle:
        prepared = self._transition_coordinator.create_prepared_json_candidate(
            store_id,
            operation_id=operation_id,
            binding=binding,
            candidate_id=candidate_id,
            payload=payload,
            parents=parents,
            ingress_kind=ingress_kind,
            semantic_op=semantic_op,
            driver=driver,
            relationship_requirements=relationship_requirements,
            message=message,
        )
        return PreparedCandidateBundle(
            candidate=prepared.candidate,
            candidate_commit=prepared.candidate_commit,
            transition=prepared.transition,
            plan=prepared.plan,
            preparation=prepared.preparation,
        )

    def create_prepared_driver_candidate_bundle(
        self,
        store_id: str,
        *,
        operation_id: str,
        binding: str,
        result: DriverIngressResult,
        driver_id: str,
        driver_version: str,
        candidate_id: str = "primary",
        parents: tuple[str | pygit2.Oid, ...] = (),
        ingress_kind: str = "command",
        relationship_requirements: tuple[RelationshipRequirement, ...] = (),
        reduction_batch: ReductionBatch | None = None,
        message: str | None = None,
    ) -> PreparedCandidateBundle:
        prepared = self._transition_coordinator.create_prepared_driver_candidate(
            store_id,
            operation_id=operation_id,
            binding=binding,
            result=result,
            driver_id=driver_id,
            driver_version=driver_version,
            candidate_id=candidate_id,
            parents=parents,
            ingress_kind=ingress_kind,
            relationship_requirements=relationship_requirements,
            reduction_batch=reduction_batch,
            message=message,
        )
        return PreparedCandidateBundle(
            candidate=prepared.candidate,
            candidate_commit=prepared.candidate_commit,
            transition=prepared.transition,
            plan=prepared.plan,
            preparation=prepared.preparation,
        )

    def dispatch_driver_ingress(
        self,
        driver: SubstrateDriver,
        context: DriverContext,
        request: IngressRequest,
    ) -> DriverIngressResult:
        return self._transition_coordinator.dispatch(driver, context, request)

    def validate_active_surface_result(
        self,
        driver: SubstrateDriver,
        context: DriverContext,
        result: DriverIngressResult,
    ) -> None:
        self._transition_coordinator.validate_active_surface_result(driver, context, result)

    def persist_driver_evidence_only(
        self,
        store_id: str,
        *,
        operation_id: str,
        binding: str,
        result: DriverIngressResult,
        ingress_kind: str,
        driver_id: str,
        driver_version: str,
        envelope_id: str = "primary",
    ) -> CoordinatorEvidenceOnlyIngress:
        return self._transition_coordinator.persist_driver_evidence_only(
            store_id,
            operation_id=operation_id,
            binding=binding,
            result=result,
            ingress_kind=ingress_kind,
            driver_id=driver_id,
            driver_version=driver_version,
            envelope_id=envelope_id,
        )

    def persist_driver_diagnostics(
        self,
        store_id: str,
        *,
        operation_id: str,
        binding: str,
        result: DriverIngressResult,
        ingress_kind: str,
        driver_id: str,
        driver_version: str,
        envelope_id: str = "diagnostics",
    ) -> CoordinatorEvidenceOnlyIngress:
        return self._transition_coordinator.persist_driver_diagnostics(
            store_id,
            operation_id=operation_id,
            binding=binding,
            result=result,
            ingress_kind=ingress_kind,
            driver_id=driver_id,
            driver_version=driver_version,
            envelope_id=envelope_id,
        )

    def build_reduction_batch(
        self,
        evidence_refs: tuple[EvidenceRef, ...],
        *,
        citation_prefix: str = "evidence",
    ) -> ReductionBatch:
        return self._transition_coordinator.build_reduction_batch(
            evidence_refs,
            citation_prefix=citation_prefix,
        )

    def substrate_head(
        self,
        store_id: str,
        *,
        binding: str,
        head: str,
        role: str,
        store_scope: str = "resource",
    ) -> SubstrateHead:
        return self.store(store_id).substrate_head(
            binding=binding,
            head=head,
            role=role,
            store_scope=store_scope,
        )

    def create_existing_head_selection_evidence(
        self,
        *,
        operation_id: str,
        head: SubstrateHead,
        selection_kind: Literal["bootstrap", "checkpoint", "import", "revert"],
        selected_from: str | None = None,
        mechanism: str | None = None,
        correlation_id: str | None = None,
    ) -> EvidenceRef:
        """Persist operation-local evidence for selecting an existing prepared substrate head."""
        return self._transition_coordinator.create_existing_head_selection_evidence(
            operation_id=operation_id,
            head=head,
            selection_kind=selection_kind,
            selected_from=selected_from,
            mechanism=mechanism,
            correlation_id=correlation_id,
        )

    def plan_existing_head_selection(
        self,
        *,
        operation_id: str,
        head: SubstrateHead,
        selection_kind: Literal["bootstrap", "checkpoint", "import", "revert"],
        selected_from: str | None = None,
        relationship_requirements: tuple[RelationshipRequirement, ...] = (),
        retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = (),
        selection_policy_digest: str | None = None,
        mechanism: str | None = None,
        correlation_id: str | None = None,
    ) -> SelectionRequirementPlan:
        """Validate and plan coordinator-owned selection of an existing prepared head."""
        return self._transition_coordinator.plan_existing_head_selection(
            operation_id=operation_id,
            head=head,
            selection_kind=selection_kind,
            selected_from=selected_from,
            relationship_requirements=relationship_requirements,
            retention_policy_requirements=retention_policy_requirements,
            selection_policy_digest=selection_policy_digest,
            mechanism=mechanism,
            correlation_id=correlation_id,
        )

    def plan_unchanged_selection(
        self,
        *,
        operation_id: str,
        head: SubstrateHead,
        input_world_oid: str,
        relationship_requirements: tuple[RelationshipRequirement, ...] = (),
        retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = (),
        selection_policy_digest: str | None = None,
    ) -> SelectionRequirementPlan:
        """Validate and plan selection of an input-world head without a new candidate."""
        return self._transition_coordinator.plan_unchanged_selection(
            operation_id=operation_id,
            head=head,
            input_world_oid=input_world_oid,
            relationship_requirements=relationship_requirements,
            retention_policy_requirements=retention_policy_requirements,
            selection_policy_digest=selection_policy_digest,
        )

    def plan_candidate_selection(
        self,
        *,
        operation_id: str,
        selection: CandidateSelection,
        selection_kind: Literal["new-candidate", "child-produced"] | None = None,
        producer_operation_id: str | None = None,
        producer_world_oid: str | None = None,
        role: str = "",
        relationship_requirements: tuple[RelationshipRequirement, ...] = (),
        retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = (),
        selection_policy_digest: str | None = None,
    ) -> CandidateSelectionPlan:
        """Validate and plan coordinator-owned selection of a prepared candidate."""
        return self._transition_coordinator.plan_candidate_selection(
            operation_id=operation_id,
            selection=selection,
            selection_kind=selection_kind,
            producer_operation_id=producer_operation_id,
            producer_world_oid=producer_world_oid,
            role=role,
            relationship_requirements=relationship_requirements,
            retention_policy_requirements=retention_policy_requirements,
            selection_policy_digest=selection_policy_digest,
        )

    def _selection_retention_policy_requirements(
        self,
        head: SubstrateHead,
        *,
        explicit_requirements: tuple[RetentionPolicyRequirement, ...] = (),
    ) -> tuple[RetentionPolicyRequirement, ...]:
        return self._transition_coordinator.selection_retention_policy_requirements(
            head,
            explicit_requirements=explicit_requirements,
        )

    def _read_world_ref_payload(self, head: SubstrateHead) -> WorldRefPayload:
        return self._transition_coordinator.read_world_ref_payload(head)

    def create_world_from_prepared(
        self,
        prepared: PreparedWorldOperation,
        *,
        include_gitlinks: bool = False,
    ) -> str:
        prepared.require_candidate_tuples()
        self._validate_prepared_operation_admission(prepared)
        finalized = prepared.finalize()
        return self._world_store.create_world_commit(
            snapshot=finalized.snapshot,
            transition=finalized.transition,
            operation_final=finalized.operation_final,
            parents=finalized.parents,
            locator_hints=self.locator_hints(),
            include_gitlinks=include_gitlinks,
        )

    def create_unsafe_world(
        self,
        *,
        snapshot: WorldSnapshot,
        transition: Mapping[str, Any],
        operation_final: Mapping[str, Any] | OperationFinalRecord,
        parents: tuple[str | pygit2.Oid, ...] = (),
        include_gitlinks: bool = False,
    ) -> str:
        """Create a world commit from caller-assembled evidence.

        This is test/migration scaffolding for low-level storage scenarios. New
        publication paths should use ``create_world_from_prepared`` so the
        operation-final evidence is derived from the prepared operation tuple.
        """
        return self._world_store.create_world_commit(
            snapshot=snapshot,
            transition=transition,
            operation_final=operation_final,
            parents=parents,
            locator_hints=self.locator_hints(),
            include_gitlinks=include_gitlinks,
        )

    def read_world(self, ref_or_oid: str) -> WorldCommit:
        if ref_or_oid.startswith("refs/"):
            target = self._world_store.repo.references[ref_or_oid].target
            return self._world_store.read_world_commit(str(target))
        return self._world_store.read_world_commit(ref_or_oid)

    def resolve_selected_head_candidate_provenance(
        self,
        world_oid: str,
        *,
        binding: str,
    ) -> SelectedHeadCandidateProvenance:
        """Resolve candidate custody for the selected head of one binding."""
        selected_world = self.read_world(world_oid)
        try:
            selected_head = selected_world.snapshot.head_for(binding)
        except KeyError as exc:
            raise InvalidRepositoryStateError(
                f"world {selected_world.oid!r} has no selected binding {binding!r}"
            ) from exc

        for lineage_world_oid in self._input_world_lineage(selected_world.oid):
            lineage_world = self._world_store.read_world_commit(lineage_world_oid)
            try:
                lineage_head = lineage_world.snapshot.head_for(binding)
            except KeyError:
                continue
            if not _same_selected_head(lineage_head, selected_head):
                continue
            outcome = _selected_candidate_outcome_for_head(lineage_world, selected_head)
            if outcome is None:
                continue
            operation_id = _world_operation_id(lineage_world)
            producer_operation_id = _candidate_outcome_producer_operation_id(lineage_world, outcome)
            candidate_id = _candidate_outcome_candidate_id(outcome)
            candidate_tuple = self._candidate_tuple_for_selected_head(
                operation_id=operation_id,
                producer_operation_id=producer_operation_id,
                candidate_id=candidate_id,
                head=selected_head,
            )
            candidate = candidate_tuple.candidate
            self.store(candidate.store_id).validate_candidate_ref(
                operation_id=candidate.operation_id,
                binding=candidate.binding,
                candidate_id=candidate.candidate_id,
                expected_head=candidate.head,
            )
            return SelectedHeadCandidateProvenance(
                world_oid=selected_world.oid,
                producer_world_oid=lineage_world.oid,
                producer_operation_id=producer_operation_id,
                selected_head=selected_head,
                candidate_tuple=candidate_tuple,
            )

        raise InvalidRepositoryStateError(
            "selected head has no full candidate custody in input-world lineage: "
            f"{binding}@{selected_head.store_id}/{selected_head.resource_id}:{selected_head.head}"
        )

    def open_operation_journal(
        self,
        *,
        operation_id: str,
        operation_kind: str,
        target_ref: str,
        input_world_oid: str | None,
        parent_operation_id: str | None = None,
        causal_links: Mapping[str, object] | None = None,
    ) -> OperationJournalEntry:
        store = self._operation_journal
        index = self._open_journal_index()
        open_ref = operation_journal_ref("open", operation_id)
        # Open the journal and add it to the open-journal index in ONE atomic transaction (the
        # create-open ref + the index add), under the store's in-process lock.
        with store.mutation_transaction():
            entry, authority_moves = store.prepare_open(
                operation_id=operation_id,
                operation_kind=operation_kind,
                target_ref=target_ref,
                input_world_oid=input_world_oid,
                parent_operation_id=parent_operation_id,
                causal_links=causal_links,
            )
            atomic_co_write(
                self._world_store.repo,
                authority_moves=authority_moves,
                prepare=lambda: index.prepare_add(open_ref),
            )
            return entry

    def _open_journal_index(self) -> OpenOperationJournalIndex:
        return OpenOperationJournalIndex(
            self._world_store.repo,
            self._world_store.world_store_id,
            rebuild_source=self._scan_open_operation_journal_refs,
        )

    def _scan_open_operation_journal_refs(self) -> frozenset[str]:
        """Live open operation-journal ref set — the index's rebuild oracle (O(total refs), off hot path)."""
        return frozenset(ref for ref in self._world_store.repo.references if is_open_operation_journal_ref(ref))

    def verify_open_operation_journal_index(self) -> Health:
        """Deep health (fsck only): is the open-journal accelerator consistent with the authority?

        ``fresh`` iff the live index reproduces the authoritative open-ref scan; ``missing``
        (fallback exists, not a blocker), ``corrupt``, or ``stale`` otherwise — the last being
        drift from an out-of-model writer that bypassed the co-write. Performs the authoritative
        full ref scan, so it must NOT run on the admission hot path. Never mutates.
        """
        return self._open_journal_index().verify_against_authority()

    def rebuild_open_operation_journal_index(self) -> None:
        """Rebuild the open-journal accelerator from the authoritative open refs (recovery self-heal).

        Reconciles a missing, corrupt, OR stale index (the stale case being out-of-model drift),
        mirroring :meth:`rebuild_active_lease_index`. The authority is unaffected.
        """
        self._open_journal_index().rebuild_from_durable_history()

    def read_open_operation_journal_index(self) -> frozenset[str] | None:
        """The indexed open-journal ref set, or ``None`` when the record is missing (caller falls back).

        The bounded admission read: one blob, never a ref-namespace scan. Raises
        :class:`InvalidRepositoryStateError` (fail closed) if the present record is corrupt, so
        admission surfaces a blocking fact rather than silently falling back to an authority scan.
        """
        return self._open_journal_index().read_open_refs()

    def open_operation_journal_index_corruption(self) -> str | None:
        """Cheap, index-only corruption check (one blob; **no** authoritative ref scan).

        Returns the corruption detail if the present index is unreadable/corrupt, else ``None``
        (missing, or present-and-valid). Mirrors :meth:`active_lease_index_corruption`; stale-vs-
        authority detection needs the full scan (:meth:`verify_open_operation_journal_index`).
        """
        try:
            self.read_open_operation_journal_index()
        except InvalidRepositoryStateError as exc:
            return str(exc)
        return None

    def record_operation_prepared(
        self,
        operation_id: str,
        *,
        prepared: PreparedWorldOperation,
    ) -> OperationJournalEntry:
        if prepared.operation_id != operation_id:
            raise InvalidRepositoryStateError("operation journal operation_id disagrees with prepared operation")
        try:
            prepared.require_candidate_tuples()
        except ValueError as exc:
            raise InvalidRepositoryStateError(str(exc)) from exc
        self._validate_prepared_operation_admission(prepared)
        selected = dict(prepared.selected or {})
        candidate_outcomes = tuple(
            outcome.to_json(final_operation_id=operation_id) for outcome in prepared.candidate_outcomes
        )
        prepared_json = prepared.to_json()
        _prepared_operation_from_json(prepared_json)
        return self._operation_journal.append(
            operation_id,
            status="prepared",
            updates={
                "candidate_refs": [_candidate_revision_to_json(candidate) for candidate in prepared.candidate_refs],
                "candidate_outcomes": [dict(outcome) for outcome in candidate_outcomes],
                "selected": selected,
                "prepared_world_operation": prepared_json,
                "prepared_world_operation_digest": prepared.prepared_operation_digest(),
            },
        )

    def record_operation_finalized(
        self,
        operation_id: str,
    ) -> OperationJournalEntry:
        prepared = self._prepared_operation_from_journal_tip(operation_id)
        if prepared is None:
            raise InvalidRepositoryStateError("operation finalization requires a prepared operation")
        finalized = prepared.finalize()
        if finalized.operation_id != operation_id:
            raise InvalidRepositoryStateError("operation journal operation_id disagrees with finalized operation")
        return self._operation_journal.append(
            operation_id,
            status="finalized",
            updates={
                "candidate_refs": [_candidate_revision_to_json(candidate) for candidate in finalized.candidate_refs],
                "candidate_commits": list(finalized.operation_final.payload["candidate_commits"]),
                "candidate_outcomes": list(finalized.candidate_outcome_payloads),
                "selected": dict(finalized.selected or {}),
                "snapshot": finalized.snapshot.to_json(),
                "snapshot_digest": finalized.snapshot_digest,
                "transition": dict(finalized.transition),
                "parents": list(finalized.parents),
                "operation_final": finalized.operation_final.payload,
                "operation_final_digest": finalized.operation_final_digest,
            },
        )

    def _validate_prepared_operation_admission(self, prepared: PreparedWorldOperation) -> None:
        """Validate coordinator-owned evidence before journaling or committing a prepared world."""
        self._transition_coordinator.validate_prepared_operation_admission(prepared)

    def _prepared_operation_from_journal_tip(self, operation_id: str) -> PreparedWorldOperation | None:
        history = self.read_operation_journal(operation_id)
        prepared_value = history.tip.payload.get("prepared_world_operation")
        if prepared_value is None:
            return None
        if not isinstance(prepared_value, dict):
            raise InvalidRepositoryStateError("operation journal prepared_world_operation must be an object")
        try:
            return _prepared_operation_from_json(prepared_value)
        except (TypeError, ValueError) as exc:
            raise InvalidRepositoryStateError(str(exc)) from exc

    def _prepared_operation_from_any_journal_tip(self, operation_id: str) -> PreparedWorldOperation:
        last_error: Exception | None = None
        for family in ("closed", "open", "archived"):
            try:
                history = self.read_operation_journal(operation_id, family=family)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
            prepared_value = history.tip.payload.get("prepared_world_operation")
            if not isinstance(prepared_value, dict):
                raise InvalidRepositoryStateError(
                    f"operation journal {operation_id!r} has no prepared_world_operation payload"
                )
            try:
                return _prepared_operation_from_json(prepared_value)
            except (TypeError, ValueError) as exc:
                raise InvalidRepositoryStateError(str(exc)) from exc
        raise InvalidRepositoryStateError(f"operation journal is missing for {operation_id!r}") from last_error

    def _candidate_tuple_for_selected_head(
        self,
        *,
        operation_id: str,
        producer_operation_id: str,
        candidate_id: str,
        head: SubstrateHead,
    ) -> PreparedCandidateTupleRecord:
        prepared = self._prepared_operation_from_any_journal_tip(operation_id)
        matches = tuple(
            candidate_tuple
            for candidate_tuple in prepared.candidate_tuples
            if _candidate_tuple_matches_head(
                candidate_tuple,
                head,
                producer_operation_id=producer_operation_id,
                candidate_id=candidate_id,
            )
        )
        if len(matches) != 1:
            raise InvalidRepositoryStateError(
                "operation "
                f"{operation_id!r} has no unique prepared candidate tuple for "
                f"{head.binding}@{head.store_id}/{head.resource_id}:{head.head}"
            )
        return matches[0]

    def record_operation_world_committed(
        self,
        operation_id: str,
        *,
        world_oid: str,
    ) -> OperationJournalEntry:
        evidence = self._operation_final_evidence(operation_id, world_oid)
        return self._operation_journal.append(
            operation_id,
            status="world_committed",
            updates={
                "world_oid": world_oid,
                "operation_final_digest": evidence.operation_final_digest,
                "selected": evidence.selected,
                "candidate_outcomes": list(evidence.candidate_outcomes),
            },
        )

    def record_operation_publishing(
        self,
        operation_id: str,
        *,
        world_oid: str,
        publication_plan: PublicationPlan,
    ) -> OperationJournalEntry:
        history = self.read_operation_journal(operation_id)
        tip = history.tip.payload
        self._validate_publication_plan(
            publication_plan,
            expected_world_oid=world_oid,
            expected_authority_ref=_required_payload_str(tip, "operation journal", "target_ref"),
            expected_input_world_oid=_optional_payload_str(tip, "operation journal", "input_world_oid"),
        )
        evidence = self._operation_final_evidence(operation_id, world_oid)
        updates: dict[str, object] = {
            "world_oid": world_oid,
            "operation_final_digest": evidence.operation_final_digest,
            "selected": evidence.selected,
            "candidate_outcomes": list(evidence.candidate_outcomes),
        }
        updates["publication_plan"] = publication_plan.to_json()
        updates["publication_plan_digest"] = publication_plan.digest()
        return self._operation_journal.append(
            operation_id,
            status="publishing",
            updates=updates,
        )

    def record_operation_published(
        self,
        operation_id: str,
        *,
        world_oid: str,
    ) -> OperationJournalEntry:
        evidence = self._operation_final_evidence(operation_id, world_oid)
        return self._operation_journal.append(
            operation_id,
            status="published",
            updates={
                "world_oid": world_oid,
                "operation_final_digest": evidence.operation_final_digest,
                "selected": evidence.selected,
                "candidate_outcomes": list(evidence.candidate_outcomes),
            },
        )

    def close_operation_journal(
        self,
        operation_id: str,
        *,
        world_oid: str,
    ) -> OperationJournalEntry:
        evidence = self._operation_final_evidence(operation_id, world_oid)
        return self._terminal_operation_journal(
            operation_id,
            family="closed",
            status="closed",
            updates={
                "selected": evidence.selected,
                "candidate_outcomes": list(evidence.candidate_outcomes),
                "world_oid": world_oid,
                "operation_final_digest": evidence.operation_final_digest,
            },
        )

    def fail_operation_journal(self, operation_id: str, *, error: str) -> OperationJournalEntry:
        # A non-terminal append retargets the open ref; it does NOT change open-set membership, so it
        # stays off the index co-write.
        return self._operation_journal.append(operation_id, status="failed", updates={"error": error})

    def archive_operation_journal(self, operation_id: str, *, error: str | None = None) -> OperationJournalEntry:
        updates = {} if error is None else {"error": error}
        return self._terminal_operation_journal(operation_id, family="archived", status="archived", updates=updates)

    def _terminal_operation_journal(
        self,
        operation_id: str,
        *,
        family: str,
        status: str,
        updates: Mapping[str, object],
    ) -> OperationJournalEntry:
        """Publish a terminal journal + tombstone its open-index entry in one atomic transaction.

        Create terminal + delete open + index tombstone, all-or-none, under the store lock.
        """
        store = self._operation_journal
        index = self._open_journal_index()
        open_ref = operation_journal_ref("open", operation_id)
        with store.mutation_transaction():
            entry, authority_moves = store.prepare_terminal(operation_id, family=family, status=status, updates=updates)
            atomic_co_write(
                self._world_store.repo,
                authority_moves=authority_moves,
                prepare=lambda: index.prepare_remove(open_ref),
            )
            return entry

    def cleanup_stale_terminal_operation_open_ref(self, operation_id: str, *, terminal_family: str) -> bool:
        """Delete a stale open ref + tombstone its open-index entry in one atomic transaction.

        The THIRD ``ops/open/*`` membership writer, on the co-write like open/terminal. For an
        out-of-model stale ref the index never indexed, ``prepare_remove`` is an idempotent no-op,
        so the batch atomically deletes just the open ref; for a co-written ref it also tombstones.
        """
        store = self._operation_journal
        index = self._open_journal_index()
        with store.mutation_transaction():
            open_ref, authority_moves = store.prepare_cleanup_stale_open_ref(
                operation_id, terminal_family=terminal_family
            )
            if open_ref is None:
                return False
            atomic_co_write(
                self._world_store.repo,
                authority_moves=authority_moves,
                prepare=lambda: index.prepare_remove(open_ref),
            )
            return True

    def read_operation_journal(self, operation_id: str, *, family: str = "open") -> OperationJournalHistory:
        return self._operation_journal.read(operation_id, family=family)

    def list_operation_journals(self, *, family: str | None = None) -> tuple[OperationJournalSummary, ...]:
        return self._operation_journal.list(family=family)

    def fsck_operation_journal(self, operation_id: str, *, family: str = "open") -> OperationJournalFsckReport:
        issues: list[StructuredIssue] = []
        try:
            history = self.read_operation_journal(operation_id, family=family)
        except (InvalidRepositoryStateError, KeyError, TypeError, ValueError) as exc:
            return OperationJournalFsckReport(
                operation_id=operation_id,
                issue_details=(
                    _issue(
                        "journal_read_failed",
                        str(exc),
                        operation_id=operation_id,
                        recovery_hint="Inspect or archive the operation journal before retrying.",
                    ),
                ),
            )
        tip = history.tip.payload
        _extend_candidate_ref_issues(issues, tip.get("candidate_refs", []), stores=self._stores)
        world_oid = tip.get("world_oid")
        if isinstance(world_oid, str):
            try:
                world = self._world_store.read_world_commit(world_oid)
            except (InvalidRepositoryStateError, KeyError, TypeError, ValueError) as exc:
                issues.append(_issue("journal_world_invalid", str(exc), operation_id=operation_id, world_oid=world_oid))
            else:
                _extend_final_evidence_issues(issues, tip, world, operation_id=operation_id)
        return OperationJournalFsckReport(operation_id=operation_id, issue_details=tuple(issues))

    def fsck_operation_journals(self) -> OperationJournalsFsckReport:
        """Store-global integrity scan over every v2-shaped operation-journal ref.

        The canonical, explicit, off-hot-path surface for terminal-journal integrity: open-only
        admission no longer *blocks* on corrupt non-`open` journals, so this is where their
        corruption is *detected*. Enumerates via the ref-walk inventory probe (``family=None``),
        which **preserves** present-invalid refs — unlike ``OperationJournalStore.list()``, which
        silently skips refs that fail to parse (the exact corruption being moved off admission).
        For each ref:

        * present-invalid (unreadable, unsupported/unknown family, identity mismatch, corrupt
          chain) → report its issues directly; do **not** attempt targeted fsck, because
          ``operation_id`` / ``family`` may be absent or untrustworthy in precisely these cases;
        * valid + known family + usable operation id → run the deeper targeted
          :meth:`fsck_operation_journal` and fold its issues in.

        Store-global, so it is its OWN entry point — deliberately NOT hung on per-world
        ``fsck_world(mode="deep")``, which reports a single world's integrity.
        """
        from vcs_core._operation_journal_inventory import probe_operation_journals

        issues: list[StructuredIssue] = []
        items = probe_operation_journals(self._world_store.repo)  # family=None: all v2 refs; invalid preserved
        for item in items:
            operation_id = _usable_journal_operation_id(item.fields)
            if item.health.validity == "invalid":
                # This surface recovers nothing — it is the diagnostic home for corruption moved
                # off admission. Override the probe's generic "recover this journal" hint with the
                # diagnostic-only framing, rather than inheriting a hint that implies recoverability.
                issues.extend(
                    _issue(
                        issue.code,
                        issue.message,
                        operation_id=operation_id,
                        ref=issue.locator or item.locator,
                        recovery_hint=_TERMINAL_JOURNAL_DIAGNOSTIC_HINT,
                    )
                    for issue in item.issues
                )
                continue
            family = item.fields.get("family")
            if isinstance(family, str) and operation_id is not None:
                issues.extend(self.fsck_operation_journal(operation_id, family=family).issue_details)
        # Deep, off-hot-path drift check of the open-journal accelerator against the authoritative
        # open refs. Atomic co-write precludes phantoms in the normal writer model, but an
        # out-of-model writer (manual/private-ref edit) can leave the index STALE, so the store-wide
        # journal fsck is where that drift surfaces (mirrors the lease verify in fsck_world).
        index_health = self.verify_open_operation_journal_index()
        if index_health.status in ("stale", "corrupt"):
            issues.append(
                _issue(
                    f"open_operation_journal_index_{index_health.status}",
                    f"open-operation-journal accelerator is {index_health.status} versus the authoritative "
                    f"open journal refs: {index_health.detail}",
                    store_id=self._world_store.world_store_id,
                    ref=world_open_operation_journal_index_ref(self._world_store.world_store_id),
                    recovery_hint=(
                        "Run rebuild_open_operation_journal_index() to reconcile the accelerator; "
                        "the authority is unaffected."
                    ),
                )
            )
        return OperationJournalsFsckReport(scanned=len(items), issue_details=tuple(issues))

    def publish_root_world(
        self,
        *,
        ref: str,
        world_oid: str,
        allow_same_resource_alias: bool = False,
        authority_refs: tuple[str, ...] | None = None,
    ) -> bool:
        plan = self.build_root_publication_plan(
            ref=ref,
            world_oid=world_oid,
            allow_same_resource_alias=allow_same_resource_alias,
            authority_refs=authority_refs,
        )
        prepared = self.prepare_publication(plan)
        published = self.advance_publication(prepared)
        self.complete_publication(prepared)
        return published

    def advance_world_ref(
        self,
        *,
        ref: str,
        world_oid: str,
        input_world_oid: str,
        allow_same_resource_alias: bool = False,
        authority_refs: tuple[str, ...] | None = None,
    ) -> bool:
        plan = self.build_advance_publication_plan(
            ref=ref,
            world_oid=world_oid,
            expected_oid=input_world_oid,
            input_world_oid=input_world_oid,
            allow_same_resource_alias=allow_same_resource_alias,
            authority_refs=authority_refs,
        )
        prepared = self.prepare_publication(plan)
        published = self.advance_publication(prepared)
        self.complete_publication(prepared)
        return published

    def build_root_publication_plan(
        self,
        *,
        ref: str,
        world_oid: str,
        allow_same_resource_alias: bool = False,
        authority_refs: tuple[str, ...] | None = None,
    ) -> PublicationPlan:
        world = self._world_store.read_world_commit(world_oid)
        if world.parent_oids:
            raise InvalidRepositoryStateError("root world publication requires an unparented world")
        if world.transition.get("input_world") is not None:
            raise InvalidRepositoryStateError("root world publication requires no input_world")
        return self.build_publication_plan(
            ref=ref,
            world_oid=world_oid,
            expected_oid=None,
            input_world_oid=None,
            allow_same_resource_alias=allow_same_resource_alias,
            authority_refs=authority_refs,
        )

    def build_advance_publication_plan(
        self,
        *,
        ref: str,
        world_oid: str,
        expected_oid: str,
        input_world_oid: str,
        allow_same_resource_alias: bool = False,
        authority_refs: tuple[str, ...] | None = None,
    ) -> PublicationPlan:
        world = self._world_store.read_world_commit(world_oid)
        _validate_advance_basis(world, input_world_oid=input_world_oid)
        if expected_oid != input_world_oid:
            raise InvalidRepositoryStateError("advance publication expected_oid must equal input_world_oid")
        return self.build_publication_plan(
            ref=ref,
            world_oid=world_oid,
            expected_oid=expected_oid,
            input_world_oid=input_world_oid,
            allow_same_resource_alias=allow_same_resource_alias,
            authority_refs=authority_refs,
        )

    def build_publication_plan(
        self,
        *,
        ref: str,
        world_oid: str,
        expected_oid: str | None,
        input_world_oid: str | None,
        allow_same_resource_alias: bool = False,
        authority_refs: tuple[str, ...] | None = None,
    ) -> PublicationPlan:
        resolved_authority_refs = _publish_authority_refs(ref, authority_refs)
        return PublicationPlan(
            authority_ref=ref,
            authority_refs=resolved_authority_refs,
            world_store_id=self._world_store.world_store_id,
            world_oid=world_oid,
            expected_oid=expected_oid,
            input_world_oid=input_world_oid,
            allow_same_resource_alias=allow_same_resource_alias,
        )

    def prepare_publication(self, plan: PublicationPlan) -> PreparedPublication:
        self._validate_publication_plan(plan)
        # Trust-by-default (260623-0640-plan.md, Part A): the prior-lineage retention
        # re-validation (_validate_authority_retention_preflight) is OFF the publish hot path.
        # It re-walked every prior world's publish closure on each publish (2N-1 closure
        # computations, Sigma = N^2) and was redundant with the durable pins/receipts written
        # below. The detector survives and now runs on demand in fsck_world(mode="deep") (Part B).
        closure = self.validate_publish_closure(
            plan.world_oid,
            authority_refs=plan.authority_refs,
            allow_same_resource_alias=plan.allow_same_resource_alias,
        )
        world = self._world_store.read_world_commit(plan.world_oid)
        lease_refs = self._write_publication_leases((plan.authority_ref,), world)
        retained_refs = self.pin_world_closure(closure)
        self.write_world_retention_receipt(
            authority_ref=plan.authority_ref,
            world_oid=plan.world_oid,
            closure=closure,
            retained_refs=retained_refs,
        )
        return PreparedPublication(plan=plan, lease_refs=lease_refs)

    def advance_publication(self, prepared: PreparedPublication) -> bool:
        return self._world_store._publish_ref_unchecked(
            prepared.plan.authority_ref,
            prepared.plan.world_oid,
            prepared.plan.expected_oid,
        )

    def complete_publication(self, prepared: PreparedPublication) -> None:
        self._release_publication_leases(prepared.lease_refs, world_oid=prepared.plan.world_oid)

    def _validate_publication_plan(
        self,
        plan: PublicationPlan,
        *,
        expected_world_oid: str | None = None,
        expected_authority_ref: str | None = None,
        expected_input_world_oid: str | None = None,
    ) -> None:
        if plan.world_store_id != self._world_store.world_store_id:
            raise InvalidRepositoryStateError("publication plan world_store_id disagrees with manager")
        if expected_world_oid is not None and plan.world_oid != expected_world_oid:
            raise InvalidRepositoryStateError("publication plan world_oid disagrees with operation journal")
        if expected_authority_ref is not None and plan.authority_ref != expected_authority_ref:
            raise InvalidRepositoryStateError("publication plan authority_ref disagrees with operation journal")
        if expected_input_world_oid is not None and plan.input_world_oid != expected_input_world_oid:
            raise InvalidRepositoryStateError("publication plan input_world_oid disagrees with operation journal")
        if plan.authority_refs[0] != plan.authority_ref:
            raise InvalidRepositoryStateError("publication plan authority_refs must start with authority_ref")
        if plan.authority_refs != _publish_authority_refs(plan.authority_ref, plan.authority_refs[1:]):
            raise InvalidRepositoryStateError("publication plan authority_refs are not canonical")
        world = self._world_store.read_world_commit(plan.world_oid)
        if plan.input_world_oid is None:
            if plan.expected_oid is not None:
                raise InvalidRepositoryStateError("root publication plan expected_oid must be null")
            if world.parent_oids:
                raise InvalidRepositoryStateError("root publication plan requires an unparented world")
            if world.transition.get("input_world") is not None:
                raise InvalidRepositoryStateError("root publication plan requires no input_world")
            return
        if plan.expected_oid != plan.input_world_oid:
            raise InvalidRepositoryStateError("advance publication plan expected_oid must equal input_world_oid")
        _validate_advance_basis(world, input_world_oid=plan.input_world_oid)

    def fork_world_ref(
        self,
        *,
        ref: str,
        world_oid: str,
        forked_from_ref: str,
        forked_from_world_oid: str,
        allow_same_resource_alias: bool = False,
    ) -> bool:
        if _current_ref_target(self._world_store.repo, ref) is not None:
            return False
        forked_from_target = _current_ref_target(self._world_store.repo, forked_from_ref)
        if forked_from_target != forked_from_world_oid:
            raise InvalidRepositoryStateError("fork origin authority ref does not target forked_from_world_oid")
        world = self._world_store.read_world_commit(world_oid)
        if world_oid != forked_from_world_oid:
            _validate_advance_basis(world, input_world_oid=forked_from_world_oid)
        # Trust-by-default (260623-0640-plan.md, Part A): prior-lineage retention re-validation
        # is off the fork-publish hot path too; the immediate fork-origin shape check above
        # (_validate_advance_basis) stays. Deep lineage integrity runs in fsck_world(deep) (Part B).
        closure = self.validate_publish_closure(
            world_oid,
            authority_refs=(forked_from_ref,),
            allow_same_resource_alias=allow_same_resource_alias,
        )
        lease_refs = self._write_publication_leases((ref,), world)
        retained_refs = self.pin_world_closure(closure)
        self.write_world_retention_receipt(
            authority_ref=ref,
            world_oid=world_oid,
            closure=closure,
            retained_refs=retained_refs,
        )
        self.write_world_fork_origin_receipt(
            authority_ref=ref,
            first_world_oid=world_oid,
            forked_from_authority_ref=forked_from_ref,
            forked_from_world_oid=forked_from_world_oid,
        )
        published = self._world_store._publish_ref_unchecked(ref, world_oid, expected_oid=None)
        self._release_publication_leases(lease_refs, world_oid=world_oid)
        return published

    def fork_origin_world_oid(self, authority_ref: str, *, expected_forked_from_ref: str | None = None) -> str:
        """Return the parent world basis recorded when an authority ref was forked."""
        receipt = _read_world_fork_origin_receipt(self._world_store.repo, world_fork_origin_receipt_ref(authority_ref))
        if receipt.authority_ref != authority_ref:
            raise InvalidRepositoryStateError("fork origin receipt authority_ref disagrees with ref")
        if receipt.world_store_id != self._world_store.world_store_id:
            raise InvalidRepositoryStateError("fork origin receipt world_store_id disagrees with coordinator")
        if expected_forked_from_ref is not None and receipt.forked_from_authority_ref != expected_forked_from_ref:
            raise InvalidRepositoryStateError("fork origin receipt parent authority disagrees with retained handoff")
        return receipt.forked_from_world_oid

    def _publish_world(
        self,
        *,
        ref: str,
        world_oid: str,
        expected_oid: str | None,
        allow_same_resource_alias: bool,
        authority_refs: tuple[str, ...] | None,
    ) -> bool:
        plan = self.build_publication_plan(
            ref=ref,
            world_oid=world_oid,
            expected_oid=expected_oid,
            input_world_oid=expected_oid,
            allow_same_resource_alias=allow_same_resource_alias,
            authority_refs=authority_refs,
        )
        prepared = self.prepare_publication(plan)
        published = self.advance_publication(prepared)
        self.complete_publication(prepared)
        return published

    def validate_publish_closure(
        self,
        oid: str,
        *,
        authority_refs: tuple[str, ...] = (),
        allow_same_resource_alias: bool = False,
    ) -> WorldClosure:
        """Validate the semantic closure needed to publish one new world."""
        closure = self.compute_publish_retention_closure(oid)
        pin_classification = self.classify_world_closure_retention(closure, authority_refs=authority_refs)
        protected_retention = self._protected_retention(authority_refs)
        for world in closure.worlds:
            selected_pins_are_authoritative = _world_selected_pins_are_authoritative(
                closure,
                world_store_id=self._world_store.world_store_id,
                world_oid=world.oid,
                protected_world_oids=protected_retention.world_oids,
                pin_classification=pin_classification,
            )
            self._world_store.validate_world_commit(
                world.oid,
                self._stores,
                allow_same_resource_alias=allow_same_resource_alias,
                require_selected_candidate_refs=not selected_pins_are_authoritative,
                validate_input_worlds=False,
                profile=WorldValidationProfile.DEEP,
            )
        return closure

    def validate_world_closure(
        self,
        oid: str,
        *,
        authority_refs: tuple[str, ...] = (),
        allow_same_resource_alias: bool = False,
    ) -> WorldClosure:
        """Validate every world reachable through the root's required recursive closure."""
        closure = self.compute_resume_retention_closure(oid)
        pin_classification = self.classify_world_closure_retention(closure, authority_refs=authority_refs)
        protected_retention = self._protected_retention(authority_refs)
        for world in closure.worlds:
            selected_pins_are_authoritative = _world_selected_pins_are_authoritative(
                closure,
                world_store_id=self._world_store.world_store_id,
                world_oid=world.oid,
                protected_world_oids=protected_retention.world_oids,
                pin_classification=pin_classification,
            )
            self._world_store.validate_world_commit(
                world.oid,
                self._stores,
                allow_same_resource_alias=allow_same_resource_alias,
                require_selected_candidate_refs=not selected_pins_are_authoritative,
                validate_input_worlds=False,
                profile=WorldValidationProfile.DEEP,
            )
        return closure

    def fsck_world(
        self,
        oid: str,
        *,
        authority_refs: tuple[str, ...] = (DEFAULT_GROUND_REF,),
        mode: Literal["structural", "deep"] = "structural",
    ) -> WorldFsckReport:
        if mode == "structural":
            return self._fsck_world_structural(oid)
        if mode != "deep":
            raise ValueError(f"unsupported world fsck mode: {mode!r}")
        issues: list[StructuredIssue] = []
        pin_classification: dict[str, tuple[str, ...]] = {}
        closure: WorldClosure | None = None
        protected_retention = _ProtectedRetention(world_oids=frozenset(), refs=frozenset())
        try:
            closure = self.compute_resume_retention_closure(oid)
            pin_classification = self.classify_world_closure_retention(closure, authority_refs=authority_refs)
            protected_retention = self._protected_retention(authority_refs)
        except (InvalidRepositoryStateError, KeyError, TypeError, ValueError) as exc:
            issues.append(_issue("pin_classification_failed", str(exc), world_oid=oid))
            closure = None
        if closure is not None:
            for world in closure.worlds:
                selected_pins_are_authoritative = _world_selected_pins_are_authoritative(
                    closure,
                    world_store_id=self._world_store.world_store_id,
                    world_oid=world.oid,
                    protected_world_oids=protected_retention.world_oids,
                    pin_classification=pin_classification,
                )
                try:
                    self._world_store.validate_world_commit(
                        world.oid,
                        self._stores,
                        require_selected_candidate_refs=not selected_pins_are_authoritative,
                        validate_input_worlds=False,
                        profile=WorldValidationProfile.DEEP,
                    )
                except (InvalidRepositoryStateError, KeyError, TypeError, ValueError) as exc:
                    issues.append(_world_validation_issue(str(exc), world_oid=world.oid))
        if pin_classification.get("missing_for_published_world"):
            issues.append(
                _issue(
                    "missing_selected_head_pins",
                    "published world is missing selected-head pins",
                    world_oid=oid,
                    recovery_hint="Re-pin the world's retention closure (repin_world_retention) or repair the substrate store.",
                )
            )
        if pin_classification.get("corrupt"):
            issues.append(
                _issue(
                    "corrupt_selected_head_pins",
                    "world selected-head pins disagree with snapshot",
                    world_oid=oid,
                    recovery_hint="Do not trust the corrupted pins; inspect the affected substrate refs before repair.",
                )
            )
        if closure is not None:
            self._extend_authority_lineage_retention_receipt_issues(
                issues,
                oid,
                authority_refs=authority_refs,
            )
        # Deep, off-hot-path stale/corrupt check of the active-lease accelerator against the
        # authoritative lease refs. This is the ONLY place the authority-comparing verify (a full
        # ref scan) runs from health reporting; the readiness/recovery-inventory probe is cheap
        # (index-only, corrupt detection only) — see _recovery_inventory._active_lease_index_items.
        lease_index_health = self.verify_active_lease_index()
        if lease_index_health.status in ("stale", "corrupt"):
            issues.append(
                _issue(
                    f"active_lease_index_{lease_index_health.status}",
                    f"active-lease accelerator is {lease_index_health.status} versus the authoritative "
                    f"lease refs: {lease_index_health.detail}",
                    store_id=self._world_store.world_store_id,
                    ref=world_publication_lease_index_ref(self._world_store.world_store_id),
                    recovery_hint="Run rebuild_active_lease_index() to reconcile the accelerator; the authority is unaffected.",
                )
            )
        return WorldFsckReport(world_oid=oid, pin_classification=pin_classification, issue_details=tuple(issues))

    def fsck_world_structural(self, oid: str) -> WorldFsckReport:
        return self.fsck_world(oid, mode="structural")

    def fsck_world_deep(
        self,
        oid: str,
        *,
        authority_refs: tuple[str, ...] = (DEFAULT_GROUND_REF,),
    ) -> WorldFsckReport:
        return self.fsck_world(oid, authority_refs=authority_refs, mode="deep")

    def _fsck_world_structural(self, oid: str) -> WorldFsckReport:
        issues: list[StructuredIssue] = []
        pin_classification: dict[str, tuple[str, ...]] = {}
        closure: WorldClosure | None = None
        protected_retention = _ProtectedRetention(world_oids=frozenset(), refs=frozenset())
        try:
            closure = self.compute_resume_retention_closure(oid)
            pin_classification = self.classify_world_closure_retention(closure, authority_refs=(DEFAULT_GROUND_REF,))
            protected_retention = self._protected_retention((DEFAULT_GROUND_REF,))
        except (InvalidRepositoryStateError, KeyError, TypeError, ValueError) as exc:
            issues.append(_issue("pin_classification_failed", str(exc), world_oid=oid))
            closure = None
        worlds = closure.worlds if closure is not None else (self._world_store.read_world_commit(oid),)
        for world in worlds:
            selected_pins_are_authoritative = closure is not None and _world_selected_pins_are_authoritative(
                closure,
                world_store_id=self._world_store.world_store_id,
                world_oid=world.oid,
                protected_world_oids=protected_retention.world_oids,
                pin_classification=pin_classification,
            )
            try:
                self._world_store.validate_world_commit(
                    world.oid,
                    self._stores,
                    require_selected_candidate_refs=not selected_pins_are_authoritative,
                    validate_input_worlds=False,
                    profile=WorldValidationProfile.STRUCTURAL,
                )
            except (InvalidRepositoryStateError, KeyError, TypeError, ValueError) as exc:
                issues.append(_world_validation_issue(str(exc), world_oid=world.oid))
        if pin_classification.get("missing_for_published_world"):
            issues.append(
                _issue(
                    "missing_selected_head_pins",
                    "published world is missing selected-head pins",
                    world_oid=oid,
                    recovery_hint="Re-pin the world's retention closure (repin_world_retention) or repair the substrate store.",
                )
            )
        if pin_classification.get("corrupt"):
            issues.append(
                _issue(
                    "corrupt_selected_head_pins",
                    "world selected-head pins disagree with snapshot",
                    world_oid=oid,
                    recovery_hint="Do not trust the corrupted pins; inspect the affected substrate refs before repair.",
                )
            )
        return WorldFsckReport(world_oid=oid, pin_classification=pin_classification, issue_details=tuple(issues))

    def cleanup_orphan_pins(
        self,
        oid: str,
        *,
        authority_refs: tuple[str, ...] = (DEFAULT_GROUND_REF,),
    ) -> tuple[str, ...]:
        closure = self.compute_publish_retention_closure(oid)
        classification = self.classify_world_closure_retention(closure, authority_refs=authority_refs)
        refs_by_ref = _closure_refs_by_ref(
            closure, stores=self._stores, world_store_id=self._world_store.world_store_id
        )
        deleted: list[str] = []
        for ref in classification["orphaned"]:
            owner = refs_by_ref.get(ref)
            if owner is None:
                continue
            owner_id, expected_oid, _world_oid = owner
            repo = self._world_store.repo if owner_id == "__world_store__" else self._stores[owner_id].repo
            if _delete_ref_if_targets(repo, ref, expected_oid):
                deleted.append(ref)
        if not _world_is_protected_by_authorities(
            self._world_store.repo,
            oid,
            authority_refs,
        ) and not self._world_is_protected_by_publication_lease(oid):
            for authority_ref in authority_refs:
                receipt_ref = world_retention_receipt_ref(authority_ref, oid)
                target = _current_ref_target(self._world_store.repo, receipt_ref)
                if target is not None and _delete_ref_if_targets(self._world_store.repo, receipt_ref, target):
                    deleted.append(receipt_ref)
                fork_ref = world_fork_origin_receipt_ref(authority_ref)
                fork_target = _current_ref_target(self._world_store.repo, fork_ref)
                if fork_target is None:
                    continue
                try:
                    fork_origin = _read_world_fork_origin_receipt(self._world_store.repo, fork_ref)
                except (InvalidRepositoryStateError, KeyError, TypeError, ValueError):
                    continue
                if fork_origin.first_world_oid == oid and _delete_ref_if_targets(
                    self._world_store.repo,
                    fork_ref,
                    fork_target,
                ):
                    deleted.append(fork_ref)
        return tuple(deleted)

    def cleanup_stale_publication_leases(
        self,
        *,
        authority_refs: tuple[str, ...] = (DEFAULT_GROUND_REF,),
        abandon_journalless: bool = False,
    ) -> tuple[str, ...]:
        del authority_refs
        deleted: list[str] = []
        for lease_ref in self._active_publication_lease_refs():
            lease_target = _current_ref_target(self._world_store.repo, lease_ref)
            if lease_target is None:
                continue
            try:
                lease = _read_publication_lease(self._world_store.repo, lease_ref)
            except (InvalidRepositoryStateError, KeyError, TypeError, ValueError):
                continue
            if not self._publication_lease_is_stale(lease, abandon_journalless=abandon_journalless):
                continue
            if _delete_ref_if_targets(self._world_store.repo, lease_ref, lease_target):
                deleted.append(lease_ref)
                self._record_lease_index(remove=lease_ref)
        return tuple(deleted)

    def compute_world_closure(self, oid: str) -> WorldClosure:
        return compute_world_closure(self._world_store, oid, self._stores)

    def compute_resume_retention_closure(self, oid: str) -> WorldClosure:
        return compute_world_closure(
            self._world_store,
            oid,
            self._stores,
            closure_mode="authority",
        )

    def compute_publish_retention_closure(self, oid: str) -> WorldClosure:
        return compute_world_closure(
            self._world_store,
            oid,
            self._stores,
            closure_mode="publish",
        )

    def pin_world_closure(self, closure: WorldClosure) -> tuple[str, ...]:
        retained: list[str] = []
        for head in closure.heads:
            store = self._stores[head.store_id]
            retained.append(
                store.pin_world_head(
                    world_store_id=self._world_store.world_store_id,
                    world_oid=head.world_oid,
                    binding=head.binding,
                    head=head.head,
                )
            )
        for world in closure.worlds:
            if world.retention_ref is None:
                continue
            create_or_update_reference(
                self._world_store.repo,
                world.retention_ref,
                pygit2.Oid(hex=world.oid),
                force=True,
            )
            retained.append(world.retention_ref)
        return tuple(retained)

    def pin_resume_retention_closure(self, closure: WorldClosure) -> tuple[str, ...]:
        retained = list(self.pin_world_closure(closure))
        seen_refs: set[str] = set(retained)
        for world in closure.worlds:
            semantic = self.compute_world_closure(world.oid)
            for semantic_world in semantic.worlds:
                if semantic_world.retention_ref is None or semantic_world.retention_ref in seen_refs:
                    continue
                create_or_update_reference(
                    self._world_store.repo,
                    semantic_world.retention_ref,
                    pygit2.Oid(hex=semantic_world.oid),
                    force=True,
                )
                retained.append(semantic_world.retention_ref)
                seen_refs.add(semantic_world.retention_ref)
        return tuple(retained)

    def repin_world_retention(self, oid: str) -> tuple[str, ...]:
        """Repair a published world's retention by re-pinning its authority closure.

        The trust-by-default on-demand repair (260623-0640-plan.md, Part B) for a broken
        prior-lineage pin that deep fsck flagged as ``missing_selected_head_pins``: re-derive the
        world's authority closure (which transitively includes the ancestor lineage) and re-pin every
        head and retention ref from the immutable world commits. It does not repair an authority
        rewrite (a moved fork-origin parent, ``corrupt_fork_origin_receipt``) — that is a separate,
        higher recovery, not a missing pin.
        """
        closure = self.compute_resume_retention_closure(oid)
        return self.pin_resume_retention_closure(closure)

    def write_world_retention_receipt(
        self,
        *,
        authority_ref: str,
        world_oid: str,
        closure: WorldClosure,
        retained_refs: tuple[str, ...],
    ) -> str:
        expected_refs = _expected_retained_refs_for_closure(
            closure,
            world_store_id=self._world_store.world_store_id,
        )
        if tuple(sorted(retained_refs)) != expected_refs:
            raise InvalidRepositoryStateError("retention receipt retained refs disagree with publish closure")
        ref = world_retention_receipt_ref(authority_ref, world_oid)
        payload = _world_retention_receipt_payload(
            authority_ref=authority_ref,
            world_store_id=self._world_store.world_store_id,
            world_oid=world_oid,
            closure=closure,
            retained_refs=retained_refs,
        )
        meta_builder = self._world_store.repo.TreeBuilder()
        insert_tree_entry(
            self._world_store.repo,
            meta_builder,
            "world-retention-receipt.json",
            self._world_store.repo.create_blob(canonical_bytes(payload)),
            pygit2.GIT_FILEMODE_BLOB,
        )
        root_builder = self._world_store.repo.TreeBuilder()
        insert_tree_entry(self._world_store.repo, root_builder, "meta", meta_builder.write(), pygit2.GIT_FILEMODE_TREE)
        signature = pygit2.Signature("vcs-core world retention", "vcs-core@example.invalid")
        receipt_oid = create_commit_with_recovery(
            self._world_store.repo,
            None,
            signature,
            signature,
            f"world retention receipt {world_oid}",
            root_builder.write(),
            [],
        )
        create_or_update_reference(self._world_store.repo, ref, receipt_oid, force=True)
        return ref

    def write_world_fork_origin_receipt(
        self,
        *,
        authority_ref: str,
        first_world_oid: str,
        forked_from_authority_ref: str,
        forked_from_world_oid: str,
    ) -> str:
        ref = world_fork_origin_receipt_ref(authority_ref)
        payload = _world_fork_origin_receipt_payload(
            authority_ref=authority_ref,
            world_store_id=self._world_store.world_store_id,
            first_world_oid=first_world_oid,
            forked_from_authority_ref=forked_from_authority_ref,
            forked_from_world_oid=forked_from_world_oid,
        )
        try:
            existing = _read_world_fork_origin_receipt(self._world_store.repo, ref)
        except KeyError:
            pass
        else:
            if existing.to_json() != payload:
                raise InvalidRepositoryStateError("fork origin receipt already exists for a different origin")
            return ref
        meta_builder = self._world_store.repo.TreeBuilder()
        insert_tree_entry(
            self._world_store.repo,
            meta_builder,
            "world-fork-origin-receipt.json",
            self._world_store.repo.create_blob(canonical_bytes(payload)),
            pygit2.GIT_FILEMODE_BLOB,
        )
        root_builder = self._world_store.repo.TreeBuilder()
        insert_tree_entry(self._world_store.repo, root_builder, "meta", meta_builder.write(), pygit2.GIT_FILEMODE_TREE)
        signature = pygit2.Signature("vcs-core world fork", "vcs-core@example.invalid")
        receipt_oid = create_commit_with_recovery(
            self._world_store.repo,
            None,
            signature,
            signature,
            f"world fork origin receipt {authority_ref}",
            root_builder.write(),
            [],
        )
        create_or_update_reference(self._world_store.repo, ref, receipt_oid, force=True)
        return ref

    def classify_world_closure_retention(
        self,
        closure: WorldClosure,
        *,
        authority_refs: tuple[str, ...] = (DEFAULT_GROUND_REF,),
    ) -> dict[str, tuple[str, ...]]:
        protected_retention = self._protected_retention(authority_refs)
        expected_refs = self._expected_refs_for_closure(closure)
        result: dict[str, list[str]] = {
            "published": [],
            "orphaned": [],
            "missing_for_published_world": [],
            "corrupt": [],
        }
        for ref, (owner_id, expected_oid, world_oid) in expected_refs.items():
            repo = self._world_store.repo if owner_id == "__world_store__" else self._stores[owner_id].repo
            published = (
                ref in protected_retention.refs
                if owner_id == "__world_store__"
                else world_oid in protected_retention.world_oids
            )
            _classify_ref(
                result,
                repo,
                ref=ref,
                expected_oid=expected_oid,
                published=published,
            )
        return {key: tuple(values) for key, values in result.items()}

    def _operation_final_evidence(self, operation_id: str, world_oid: str) -> OperationFinalEvidence:
        world = self._world_store.read_world_commit(world_oid)
        evidence = _operation_final_evidence_from_world(world)
        if evidence.operation_id != operation_id:
            raise InvalidRepositoryStateError("operation journal operation_id disagrees with world operation-final")
        return evidence

    def _protected_retention(self, authority_refs: tuple[str, ...]) -> _ProtectedRetention:
        world_oids: set[str] = set()
        refs: set[str] = set()
        for world_oid in _authority_world_targets(self._world_store.repo, authority_refs):
            closure = self.compute_resume_retention_closure(world_oid)
            world_oids.update(world.oid for world in closure.worlds)
        for world_oid in self._active_lease_targets_via_index():
            closure = self.compute_publish_retention_closure(world_oid)
            world_oids.update(world.oid for world in closure.worlds)
            refs.update(_expected_retained_refs_for_closure(closure, world_store_id=self._world_store.world_store_id))
        pending = list(world_oids)
        while pending:
            world_oid = pending.pop()
            semantic = self.compute_world_closure(world_oid)
            for world in semantic.worlds:
                if world.oid not in world_oids:
                    world_oids.add(world.oid)
                    pending.append(world.oid)
                if world.retention_ref is not None:
                    refs.add(world.retention_ref)
        return _ProtectedRetention(world_oids=frozenset(world_oids), refs=frozenset(refs))

    def _write_publication_leases(self, authority_refs: tuple[str, ...], world: WorldCommit) -> tuple[str, ...]:
        operation_id = _world_operation_id(world)
        lease_refs: list[str] = []
        for authority_ref in authority_refs:
            lease_ref = world_publication_lease_ref(authority_ref, world.oid, operation_id)
            current = _current_ref_target(self._world_store.repo, lease_ref)
            if current is not None:
                lease = _read_publication_lease(self._world_store.repo, lease_ref)
                if lease.world_oid != world.oid or lease.authority_ref != authority_ref:
                    raise InvalidRepositoryStateError("publication lease ref targets a different publication")
            # The accelerator must LEAD the authoritative lease ref on creation: a crash
            # between this index update and the ref create then leaves the index a
            # SUPERSET of the live lease set (over-protect — conservative/safe), never a
            # subset (under-protect — the corrupting direction). Releases tombstone AFTER
            # the ref delete, for the same superset reason.
            self._record_lease_index(add=(lease_ref, world.oid, operation_id))
            if current is None:
                lease_oid = _write_publication_lease(
                    self._world_store.repo,
                    _PublicationLease(
                        authority_ref=authority_ref,
                        world_store_id=self._world_store.world_store_id,
                        world_oid=world.oid,
                        operation_id=operation_id,
                        created_at_unix_ns=time.time_ns(),
                    ),
                )
                create_or_update_reference(self._world_store.repo, lease_ref, lease_oid)
            lease_refs.append(lease_ref)
        return tuple(lease_refs)

    def _release_publication_leases(self, lease_refs: tuple[str, ...], *, world_oid: str) -> None:
        for lease_ref in lease_refs:
            try:
                lease_target = _current_ref_target(self._world_store.repo, lease_ref)
                if lease_target is None:
                    continue
                lease = _read_publication_lease(self._world_store.repo, lease_ref)
                if lease.world_oid != world_oid:
                    continue
                _delete_ref_if_targets(self._world_store.repo, lease_ref, lease_target)
                self._record_lease_index(remove=lease_ref)
            except InvalidRepositoryStateError:
                continue

    def _active_publication_lease_targets(self) -> frozenset[str]:
        targets: set[str] = set()
        for ref in self._active_publication_lease_refs():
            try:
                targets.add(_read_publication_lease(self._world_store.repo, ref).world_oid)
            except (InvalidRepositoryStateError, KeyError, TypeError, ValueError):
                continue
        return frozenset(targets)

    def _active_publication_lease_refs(self) -> tuple[str, ...]:
        prefix = world_publication_lease_prefix() + "/"
        return tuple(sorted(ref for ref in self._world_store.repo.references if ref.startswith(prefix)))

    def _scan_active_leases(self) -> dict[str, dict[str, str]]:
        """Authoritative active-lease entries (the full ref-namespace scan; rebuild oracle)."""
        entries: dict[str, dict[str, str]] = {}
        for lease_ref in self._active_publication_lease_refs():
            try:
                lease = _read_publication_lease(self._world_store.repo, lease_ref)
            except (InvalidRepositoryStateError, KeyError, TypeError, ValueError):
                continue
            entries[lease_ref] = {"world_oid": lease.world_oid, "operation_id": lease.operation_id}
        return entries

    def _active_lease_index(self) -> ActiveLeaseIndex:
        return ActiveLeaseIndex(
            self._world_store.repo,
            self._world_store.world_store_id,
            rebuild_source=self._scan_active_leases,
        )

    def _active_lease_targets_via_index(self) -> frozenset[str]:
        """Leased world oids via the durable accelerator.

        Missing index → fall back to the authoritative scan and self-heal for next
        time. Corrupt index → fail closed (``read_world_oids`` raises). This is the
        boundary-bounded hot-path read that replaces the O(total-refs) scan.
        """
        index = self._active_lease_index()
        members = index.read_world_oids()
        if members is not None:
            # We trust a *present* index without re-verifying against the authority. This is sound
            # ONLY because the consumer is a GC protection set and ActiveLeaseIndex.CONTRACT declares
            # read_safety="superset" / crash_lag="index-leads": staleness can only over-protect. A
            # future *exact*-read consumer MUST NOT copy this pattern — it must verify against the
            # authority or declare a different DerivedViewContract (see _incremental/_contract.py).
            return members
        targets = self._active_publication_lease_targets()
        with contextlib.suppress(InvalidRepositoryStateError):
            index.rebuild_from_durable_history()  # best-effort self-heal; the fallback already returned correct targets
        return targets

    def _record_lease_index(self, *, add: tuple[str, str, str] | None = None, remove: str | None = None) -> None:
        """Update the active-lease accelerator around an authoritative lease ref change.

        On add this runs BEFORE the lease ref is created, and on release/cleanup AFTER the
        ref is deleted, so the index is always a superset of the live lease set. The lease
        refs are the authority, so a corrupt or contended accelerator must never block a
        publish: on failure we best-effort reset the index to *missing*, and the read path
        then falls back to the authoritative scan and self-heals — we never leave or trust a
        stale subset.
        """
        index = self._active_lease_index()
        try:
            if add is not None:
                lease_ref, world_oid, operation_id = add
                index.add(lease_ref, world_oid=world_oid, operation_id=operation_id)
            if remove is not None:
                index.remove(remove)
        except InvalidRepositoryStateError:
            self._reset_lease_index()

    def _reset_lease_index(self) -> None:
        """Best-effort drop of the accelerator so the next read falls back to the authority.

        Called only to recover from an accelerator write that already failed, so it must
        never raise — otherwise a derived-view hiccup becomes a blocked publish, and the
        accelerator is never authority. An already-absent ref (``KeyError``) or a lower-level
        pygit2 / OS deletion failure is swallowed: worst case the index stays corrupt and the
        read path fails closed on it (surfaced by fsck, repaired by ``rebuild_active_lease_index``),
        but the publish on the authoritative lease refs proceeds.
        """
        ref = world_publication_lease_index_ref(self._world_store.world_store_id)
        with contextlib.suppress(KeyError, pygit2.GitError, OSError):
            self._world_store.repo.references.delete(ref)

    def active_lease_index_corruption(self) -> str | None:
        """Cheap, index-only corruption check for the readiness/recovery hot path.

        Reads ONLY the index record (one blob; **no** authoritative ref scan), so it stays off the
        O(total-refs) scan the lease index exists to avoid. Returns the corruption detail if the
        present index is unreadable/corrupt, else ``None`` (missing, or present-and-self-consistent).
        Stale-vs-authority detection needs the full scan and lives in
        :meth:`verify_active_lease_index` (deep fsck only), never on readiness.
        """
        try:
            self._active_lease_index().read_world_oids()
        except InvalidRepositoryStateError as exc:
            return str(exc)
        return None

    def verify_active_lease_index(self) -> Health:
        """Deep health (fsck only): is the accelerator consistent with the authoritative lease refs?

        ``fresh`` iff the live index reproduces the full-scan authority bit-for-bit;
        ``missing`` (fallback exists, not a blocker), ``corrupt``, or ``stale`` otherwise.
        Performs the authoritative full ref scan, so it must NOT run on the readiness/recovery
        hot path — use :meth:`active_lease_index_corruption` there. Never mutates.
        """
        return self._active_lease_index().verify_against_authority()

    def rebuild_active_lease_index(self) -> None:
        """Rebuild the active-lease accelerator from the authoritative lease refs (recovery self-heal)."""
        self._active_lease_index().rebuild_from_durable_history()

    def _world_is_protected_by_publication_lease(self, oid: str) -> bool:
        for leased_world_oid in self._active_publication_lease_targets():
            try:
                closure = self.compute_publish_retention_closure(leased_world_oid)
            except (InvalidRepositoryStateError, KeyError, TypeError, ValueError):
                if leased_world_oid == oid:
                    return True
                continue
            if any(world.oid == oid for world in closure.worlds):
                return True
        return False

    def _publication_lease_is_stale(self, lease: _PublicationLease, *, abandon_journalless: bool = False) -> bool:
        if lease.world_store_id != self._world_store.world_store_id:
            return False
        authority_refs = (lease.authority_ref,)
        if _world_is_protected_by_authorities(self._world_store.repo, lease.world_oid, authority_refs):
            return self.fsck_world(lease.world_oid, authority_refs=authority_refs).ok
        try:
            world = self._world_store.read_world_commit(lease.world_oid)
            operation_id = _world_operation_id(world)
        except (InvalidRepositoryStateError, KeyError, TypeError, ValueError):
            return False
        if operation_id != lease.operation_id:
            return False
        try:
            self.read_operation_journal(operation_id, family="archived")
        except (InvalidRepositoryStateError, KeyError, TypeError, ValueError):
            pass
        else:
            return True
        try:
            history = self.read_operation_journal(operation_id)
        except (InvalidRepositoryStateError, KeyError, TypeError, ValueError):
            return abandon_journalless
        return history.tip.payload.get("status") == "failed"

    def _expected_refs_for_closure(self, closure: WorldClosure) -> dict[str, tuple[str, str, str | None]]:
        refs = _closure_refs_by_ref(closure, stores=self._stores, world_store_id=self._world_store.world_store_id)
        for world in closure.worlds:
            semantic = self.compute_world_closure(world.oid)
            refs.update(
                _closure_refs_by_ref(semantic, stores=self._stores, world_store_id=self._world_store.world_store_id)
            )
        return refs

    def _validate_authority_retention_preflight(
        self,
        authority_refs: tuple[str, ...],
        *,
        allow_same_resource_alias: bool,
    ) -> None:
        if not authority_refs:
            return
        seen_worlds: set[str] = set()
        for authority_ref in authority_refs:
            world_oid = _current_ref_target(self._world_store.repo, authority_ref)
            if world_oid is None or world_oid in seen_worlds:
                continue
            seen_worlds.add(world_oid)
            try:
                self._validate_authority_lineage_retention(
                    authority_ref,
                    world_oid,
                    allow_same_resource_alias=allow_same_resource_alias,
                    seen=frozenset(),
                )
            except (InvalidRepositoryStateError, KeyError, TypeError, ValueError) as exc:
                raise InvalidRepositoryStateError(
                    f"authority retention preflight failed for {authority_ref!r}: {exc}"
                ) from exc

    def _validate_authority_lineage_retention(
        self,
        authority_ref: str,
        world_oid: str,
        *,
        allow_same_resource_alias: bool,
        seen: frozenset[tuple[str, str]],
    ) -> None:
        lineage_key = (authority_ref, world_oid)
        if lineage_key in seen:
            raise InvalidRepositoryStateError("authority fork lineage contains a cycle")
        seen = seen | {lineage_key}
        lineage = self._authority_lineage_segments(authority_ref, world_oid)
        if lineage.corrupt_fork_origin is not None:
            raise InvalidRepositoryStateError(lineage.corrupt_fork_origin)
        for lineage_world_oid in lineage.local_world_oids:
            closure = self.compute_publish_retention_closure(lineage_world_oid)
            self._validate_retained_refs_exist(
                closure,
                allow_same_resource_alias=allow_same_resource_alias,
                authority_ref=authority_ref,
                validate_worlds=True,
            )
        issues: list[StructuredIssue] = []
        for lineage_world_oid in lineage.local_world_oids:
            closure = self.compute_publish_retention_closure(lineage_world_oid)
            _extend_retention_receipt_issues(
                issues,
                self._world_store.repo,
                lineage_world_oid,
                authority_refs=(authority_ref,),
                world_store_id=self._world_store.world_store_id,
                closure=closure,
            )
        if issues:
            raise InvalidRepositoryStateError(issues[0].message)
        inherited = lineage.fork_origin
        if inherited is None:
            return
        inherited_oid = inherited.forked_from_world_oid
        if not _world_is_protected_by_authority(
            self._world_store.repo,
            inherited_oid,
            inherited.forked_from_authority_ref,
        ):
            raise InvalidRepositoryStateError("fork origin authority no longer protects inherited world")
        self._validate_authority_lineage_retention(
            inherited.forked_from_authority_ref,
            inherited_oid,
            allow_same_resource_alias=allow_same_resource_alias,
            seen=seen,
        )

    def _validate_retained_refs_exist(
        self,
        closure: WorldClosure,
        *,
        allow_same_resource_alias: bool,
        authority_ref: str,
        validate_worlds: bool = True,
    ) -> None:
        try:
            if validate_worlds:
                for world in closure.worlds:
                    self._world_store.validate_world_commit(
                        world.oid,
                        self._stores,
                        allow_same_resource_alias=allow_same_resource_alias,
                        require_selected_candidate_refs=False,
                        validate_input_worlds=False,
                        profile=WorldValidationProfile.DEEP,
                    )
            for ref, (owner_id, expected_oid, _world_oid) in self._expected_refs_for_closure(closure).items():
                repo = self._world_store.repo if owner_id == "__world_store__" else self._stores[owner_id].repo
                target = _current_ref_target(repo, ref)
                if target is None:
                    raise InvalidRepositoryStateError("published world is missing retained refs")
                if target != expected_oid:
                    raise InvalidRepositoryStateError("published world has corrupt retained refs")
        except (InvalidRepositoryStateError, KeyError, TypeError, ValueError) as exc:
            raise InvalidRepositoryStateError(
                f"authority retention preflight failed for {authority_ref!r}: {exc}"
            ) from exc

    def _extend_authority_lineage_retention_receipt_issues(
        self,
        issues: list[StructuredIssue],
        oid: str,
        *,
        authority_refs: tuple[str, ...],
    ) -> None:
        for authority_ref in authority_refs:
            try:
                authority_target = _current_ref_target(self._world_store.repo, authority_ref)
                if authority_target is None or oid not in self._input_world_lineage(authority_target):
                    continue
                self._extend_retention_receipt_issues_for_authority_lineage(
                    issues,
                    oid,
                    authority_ref=authority_ref,
                    seen=frozenset(),
                )
            except (InvalidRepositoryStateError, KeyError, TypeError, ValueError) as exc:
                issues.append(_issue("retention_receipt_check_failed", str(exc), world_oid=oid))

    def _extend_retention_receipt_issues_for_authority_lineage(
        self,
        issues: list[StructuredIssue],
        oid: str,
        *,
        authority_ref: str,
        seen: frozenset[tuple[str, str]],
    ) -> None:
        lineage_key = (authority_ref, oid)
        if lineage_key in seen:
            issues.append(
                _issue("corrupt_fork_origin_receipt", "authority fork lineage contains a cycle", world_oid=oid)
            )
            return
        seen = seen | {lineage_key}
        lineage = self._authority_lineage_segments(authority_ref, oid)
        if lineage.corrupt_fork_origin is not None:
            issues.append(
                _issue(
                    "corrupt_fork_origin_receipt",
                    lineage.corrupt_fork_origin,
                    world_oid=oid,
                    ref=world_fork_origin_receipt_ref(authority_ref),
                )
            )
            return
        for closure in tuple(
            self.compute_publish_retention_closure(world_oid) for world_oid in lineage.local_world_oids
        ):
            _extend_retention_receipt_issues(
                issues,
                self._world_store.repo,
                closure.root_world_oid,
                authority_refs=(authority_ref,),
                world_store_id=self._world_store.world_store_id,
                closure=closure,
            )
        inherited = lineage.fork_origin
        if inherited is None:
            return
        inherited_oid = inherited.forked_from_world_oid
        if not _world_is_protected_by_authority(
            self._world_store.repo,
            inherited_oid,
            inherited.forked_from_authority_ref,
        ):
            issues.append(
                _issue(
                    "corrupt_fork_origin_receipt",
                    "fork origin authority no longer protects inherited world",
                    world_oid=oid,
                    ref=world_fork_origin_receipt_ref(authority_ref),
                )
            )
            return
        self._extend_retention_receipt_issues_for_authority_lineage(
            issues,
            inherited_oid,
            authority_ref=inherited.forked_from_authority_ref,
            seen=seen,
        )

    def _authority_lineage_segments(
        self,
        authority_ref: str,
        oid: str,
    ) -> _AuthorityLineageSegments:
        fork_origin = _read_optional_world_fork_origin_receipt(
            self._world_store.repo,
            world_fork_origin_receipt_ref(authority_ref),
        )
        if fork_origin is None:
            return _AuthorityLineageSegments(local_world_oids=self._input_world_lineage(oid))
        if fork_origin.authority_ref != authority_ref:
            raise InvalidRepositoryStateError("fork origin receipt authority_ref disagrees with ref")
        if fork_origin.world_store_id != self._world_store.world_store_id:
            raise InvalidRepositoryStateError("fork origin receipt world_store_id disagrees with coordinator")
        lineage = self._input_world_lineage(oid)
        try:
            fork_base_index = lineage.index(fork_origin.forked_from_world_oid)
        except ValueError:
            return _AuthorityLineageSegments(
                local_world_oids=(),
                fork_origin=fork_origin,
                corrupt_fork_origin="fork origin forked_from_world_oid is not in child input lineage",
            )
        local_world_oids = tuple(lineage[:fork_base_index])
        if fork_origin.first_world_oid != fork_origin.forked_from_world_oid and (
            fork_origin.first_world_oid not in local_world_oids
        ):
            raise InvalidRepositoryStateError("fork origin first_world_oid is not in local authority lineage")
        return _AuthorityLineageSegments(local_world_oids=local_world_oids, fork_origin=fork_origin)

    def _input_world_lineage(self, oid: str) -> tuple[str, ...]:
        lineage: list[str] = []
        seen: set[str] = set()
        current_oid: str | None = oid
        while current_oid is not None:
            if current_oid in seen:
                raise InvalidRepositoryStateError("authority input_world lineage contains a cycle")
            seen.add(current_oid)
            lineage.append(current_oid)
            world = self._world_store.read_world_commit(current_oid)
            input_world = world.transition.get("input_world")
            if input_world is None:
                current_oid = None
                continue
            if not isinstance(input_world, str) or not input_world:
                raise InvalidRepositoryStateError("authority input_world lineage contains an invalid input_world")
            current_oid = input_world
        return tuple(lineage)


def _specs_by_id(stores: tuple[SubstrateStoreSpec, ...]) -> dict[str, SubstrateStoreSpec]:
    specs: dict[str, SubstrateStoreSpec] = {}
    for spec in stores:
        store_id = spec.identity.store_id
        if store_id in specs:
            raise ValueError(f"duplicate substrate store spec for {store_id!r}")
        specs[store_id] = spec
    return specs


def _installation_config_path(root: Path) -> Path:
    return root / "world-stores.json"


def _write_installation_config(
    root: Path,
    *,
    world_store_id: str,
    specs_by_id: Mapping[str, SubstrateStoreSpec],
) -> None:
    _installation_config_path(root).write_bytes(
        compact_json_bytes(_installation_config(world_store_id=world_store_id, specs_by_id=specs_by_id)) + b"\n"
    )


def _read_installation_config(root: Path) -> dict[str, object]:
    config_path = _installation_config_path(root)
    if not config_path.exists():
        raise InvalidRepositoryStateError(f"world storage installation config is missing: {config_path}")
    current = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(current, dict):
        raise InvalidRepositoryStateError("world-stores.json must contain a JSON object")
    return current


def _validate_installation_config(
    root: Path,
    *,
    world_store_id: str,
    specs_by_id: Mapping[str, SubstrateStoreSpec],
) -> None:
    current = _read_installation_config(root)
    if current.get("schema") != INSTALLATION_SCHEMA:
        raise InvalidRepositoryStateError(f"unsupported world storage installation schema: {current.get('schema')!r}")
    if current.get("world_store_id") != world_store_id:
        raise InvalidRepositoryStateError("world storage installation world_store_id mismatch")
    current_specs = _store_specs_from_config(current)
    if set(current_specs) != set(specs_by_id):
        raise InvalidRepositoryStateError("world storage installation store set mismatch")
    for store_id, spec in specs_by_id.items():
        current_spec = current_specs[store_id]
        if current_spec.identity != spec.identity:
            raise InvalidRepositoryStateError(f"world storage installation identity mismatch for {store_id!r}")
        if current_spec.locator != spec.locator:
            raise InvalidRepositoryStateError(f"world storage installation locator mismatch for {store_id!r}")
    expected = _installation_config(world_store_id=world_store_id, specs_by_id=specs_by_id)
    if current != expected:
        raise InvalidRepositoryStateError("world storage installation config mismatch")


def _installation_config(
    *,
    world_store_id: str,
    specs_by_id: Mapping[str, SubstrateStoreSpec],
) -> dict[str, object]:
    return {
        "schema": INSTALLATION_SCHEMA,
        "world_store_id": world_store_id,
        "coordinator": DEFAULT_COORDINATOR_LOCATOR,
        "stores": {store_id: spec.to_json() for store_id, spec in sorted(specs_by_id.items())},
    }


def _store_specs_from_config(current: Mapping[str, object]) -> dict[str, SubstrateStoreSpec]:
    raw_stores = current.get("stores")
    if not isinstance(raw_stores, dict):
        raise InvalidRepositoryStateError("world storage installation stores must be an object")
    specs: dict[str, SubstrateStoreSpec] = {}
    for store_id, raw_spec in raw_stores.items():
        if not isinstance(store_id, str) or not isinstance(raw_spec, dict):
            raise InvalidRepositoryStateError("world storage installation stores must map strings to objects")
        specs[store_id] = SubstrateStoreSpec.from_json(raw_spec)
    return specs


def _candidate_revision_to_json(candidate: CandidateRevision) -> dict[str, object]:
    return {
        "operation_id": candidate.operation_id,
        "binding": candidate.binding,
        "candidate_id": candidate.candidate_id,
        "store_id": candidate.store_id,
        "resource_id": candidate.resource_id,
        "head": candidate.head,
        "ref": candidate.ref,
    }


def _prepared_operation_from_json(value: Mapping[str, object]) -> PreparedWorldOperation:
    from vcs_core._world_operation_builder import PreparedWorldOperation

    return PreparedWorldOperation.from_json(value)


def _operation_final_evidence_from_world(world: WorldCommit) -> OperationFinalEvidence:
    operation_id = world.operation_final.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise InvalidRepositoryStateError("world operation-final operation_id is required")
    if world.operation_final.get("schema") != OPERATION_FINAL_SCHEMA:
        raise InvalidRepositoryStateError(
            f"unsupported world operation-final schema: {world.operation_final.get('schema')!r}"
        )
    transition_operation_id = world.transition.get("operation_id")
    if operation_id != transition_operation_id:
        raise InvalidRepositoryStateError("world operation-final operation_id disagrees with transition")
    transition_final = world.transition.get("operation_final")
    if not isinstance(transition_final, dict):
        raise InvalidRepositoryStateError("world transition operation_final is required")
    digest = transition_final.get("digest")
    if not isinstance(digest, str) or not digest:
        raise InvalidRepositoryStateError("world transition operation_final.digest is required")
    return OperationFinalEvidence(
        operation_id=operation_id,
        operation_final_digest=digest,
        selected=_string_map(world.operation_final.get("selected"), "operation-final selected"),
        candidate_outcomes=tuple(
            _object_list(world.operation_final.get("candidate_outcomes"), "operation-final candidate_outcomes")
        ),
    )


def _same_selected_head(left: SubstrateHead, right: SubstrateHead) -> bool:
    return (
        left.binding == right.binding
        and left.store_id == right.store_id
        and left.resource_id == right.resource_id
        and left.head == right.head
    )


def _selected_candidate_outcome_for_head(
    world: WorldCommit,
    head: SubstrateHead,
) -> dict[str, object] | None:
    matches: list[dict[str, object]] = []
    for outcome in _object_list(world.operation_final.get("candidate_outcomes"), "operation-final candidate_outcomes"):
        if outcome.get("binding") != head.binding or outcome.get("outcome") != "selected":
            continue
        if outcome.get("candidate") != head.head:
            continue
        store_id = outcome.get("store_id")
        if store_id is not None and store_id != head.store_id:
            raise InvalidRepositoryStateError("selected candidate outcome store_id disagrees with selected head")
        resource_id = outcome.get("resource_id")
        if resource_id is not None and resource_id != head.resource_id:
            raise InvalidRepositoryStateError("selected candidate outcome resource_id disagrees with selected head")
        matches.append(outcome)
    if len(matches) > 1:
        raise InvalidRepositoryStateError("operation-final contains duplicate selected candidate outcomes")
    return matches[0] if matches else None


def _candidate_outcome_producer_operation_id(world: WorldCommit, outcome: Mapping[str, object]) -> str:
    producer_operation_id = outcome.get("producer_operation_id")
    if producer_operation_id is None:
        return _world_operation_id(world)
    if not isinstance(producer_operation_id, str) or not producer_operation_id:
        raise InvalidRepositoryStateError("candidate outcome producer_operation_id must be a non-empty string")
    return producer_operation_id


def _candidate_outcome_candidate_id(outcome: Mapping[str, object]) -> str:
    candidate_id = outcome.get("candidate_id", "primary")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise InvalidRepositoryStateError("candidate outcome candidate_id must be a non-empty string")
    return candidate_id


def _candidate_tuple_matches_head(
    candidate_tuple: PreparedCandidateTupleRecord,
    head: SubstrateHead,
    *,
    producer_operation_id: str,
    candidate_id: str,
) -> bool:
    candidate = candidate_tuple.candidate
    return (
        candidate.operation_id == producer_operation_id
        and candidate.binding == head.binding
        and candidate.store_id == head.store_id
        and candidate.resource_id == head.resource_id
        and candidate.head == head.head
        and candidate.candidate_id == candidate_id
    )


def _world_operation_id(world: WorldCommit) -> str:
    operation_id = world.operation_final.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise InvalidRepositoryStateError("world operation-final operation_id is required")
    transition_operation_id = world.transition.get("operation_id")
    if operation_id != transition_operation_id:
        raise InvalidRepositoryStateError("world operation-final operation_id disagrees with transition")
    return operation_id


def _extend_final_evidence_issues(
    issues: list[StructuredIssue],
    journal_tip: Mapping[str, object],
    world: WorldCommit,
    *,
    operation_id: str,
) -> None:
    try:
        evidence = _operation_final_evidence_from_world(world)
    except (InvalidRepositoryStateError, KeyError, TypeError, ValueError) as exc:
        issues.append(_issue("journal_world_invalid", str(exc), operation_id=operation_id, world_oid=world.oid))
        return
    if evidence.operation_id != operation_id:
        issues.append(
            _issue(
                "journal_operation_id_mismatch",
                "operation journal operation_id disagrees with world operation-final",
                operation_id=operation_id,
                world_oid=world.oid,
            )
        )
    if journal_tip.get("operation_final_digest") != evidence.operation_final_digest:
        issues.append(
            _issue(
                "journal_final_digest_mismatch",
                "operation journal final digest disagrees with world transition",
                operation_id=operation_id,
                world_oid=world.oid,
            )
        )
    if journal_tip.get("selected") != evidence.selected:
        issues.append(
            _issue(
                "journal_selected_mismatch",
                "operation journal selected heads disagree with world operation-final",
                operation_id=operation_id,
                world_oid=world.oid,
            )
        )
    if journal_tip.get("candidate_outcomes") != list(evidence.candidate_outcomes):
        issues.append(
            _issue(
                "journal_candidate_outcomes_mismatch",
                "operation journal candidate outcomes disagree with world operation-final",
                operation_id=operation_id,
                world_oid=world.oid,
            )
        )


_TERMINAL_JOURNAL_DIAGNOSTIC_HINT = (
    "Diagnostic only: corrupt or unknown-family operation-journal refs no longer block admission. "
    "Inspect via `vcs-core inspect --domain operation_journal`; they are not auto-recoverable."
)


def _usable_journal_operation_id(fields: dict[str, object]) -> str | None:
    """First trustworthy operation id on a journal inventory item, or None if none is usable."""
    for key in ("operation_id", "payload_operation_id", "locator_operation_id"):
        value = fields.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _issue(
    code: str,
    message: str,
    *,
    world_oid: str | None = None,
    operation_id: str | None = None,
    store_id: str | None = None,
    binding: str | None = None,
    ref: str | None = None,
    recovery_hint: str | None = None,
) -> StructuredIssue:
    return StructuredIssue(
        code=code,
        message=message,
        world_oid=world_oid,
        operation_id=operation_id,
        store_id=store_id,
        binding=binding,
        ref=ref,
        recovery_hint=recovery_hint,
    )


def _world_validation_issue(message: str, *, world_oid: str) -> StructuredIssue:
    if "evidence ref is missing" in message:
        return _issue(
            "missing_evidence_ref",
            message,
            world_oid=world_oid,
            recovery_hint="Restore the coordinator evidence record or archive the affected operation history.",
        )
    if "selected candidate outcome lacks a durable candidate ref" in message:
        return _issue(
            "missing_candidate_ref",
            message,
            world_oid=world_oid,
            recovery_hint="Restore the operation-scoped candidate ref or rely on published selected-head pins.",
        )
    if "missing substrate store" in message:
        return _issue("missing_store", message, world_oid=world_oid)
    if "does not contain selected head" in message:
        return _issue("missing_selected_head", message, world_oid=world_oid)
    if "operation-final digest" in message:
        return _issue("operation_final_digest_mismatch", message, world_oid=world_oid)
    return _issue("world_validation_failed", message, world_oid=world_oid)


def _extend_candidate_ref_issues(
    issues: list[StructuredIssue],
    candidate_refs: object,
    *,
    stores: Mapping[str, SubstrateStore],
) -> None:
    if not isinstance(candidate_refs, list):
        issues.append(_issue("journal_candidate_refs_malformed", "operation journal candidate_refs must be a list"))
        return
    for candidate in candidate_refs:
        if not isinstance(candidate, dict):
            issues.append(
                _issue("journal_candidate_refs_malformed", "operation journal candidate_refs entries must be objects")
            )
            continue
        store_id = candidate.get("store_id")
        ref = candidate.get("ref")
        head = candidate.get("head")
        if not isinstance(store_id, str) or not isinstance(ref, str) or not isinstance(head, str):
            issues.append(
                _issue("journal_candidate_ref_malformed", "operation journal candidate ref entry is malformed")
            )
            continue
        store = stores.get(store_id)
        if store is None:
            issues.append(
                _issue(
                    "journal_unknown_store",
                    f"operation journal candidate ref names unknown store {store_id!r}",
                    store_id=store_id,
                    ref=ref,
                )
            )
            continue
        try:
            target = store.repo.references[ref].target
        except KeyError:
            issues.append(
                _issue(
                    "journal_missing_candidate_ref",
                    f"operation journal candidate ref is missing: {ref}",
                    store_id=store_id,
                    ref=ref,
                    recovery_hint="Restore the candidate ref or archive the failed operation.",
                )
            )
            continue
        if str(target) != head:
            issues.append(
                _issue(
                    "journal_candidate_ref_mismatch",
                    f"operation journal candidate ref target disagrees with record: {ref}",
                    store_id=store_id,
                    ref=ref,
                )
            )


def _classify_ref(
    result: dict[str, list[str]],
    repo: pygit2.Repository,
    *,
    ref: str,
    expected_oid: str,
    published: bool,
) -> None:
    try:
        target = repo.references[ref].target
    except KeyError:
        if published:
            result["missing_for_published_world"].append(ref)
        return
    if str(target) != expected_oid:
        result["corrupt"].append(ref)
    elif published:
        result["published"].append(ref)
    else:
        result["orphaned"].append(ref)


def _closure_refs_by_ref(
    closure: WorldClosure,
    *,
    stores: Mapping[str, SubstrateStore],
    world_store_id: str,
) -> dict[str, tuple[str, str, str | None]]:
    refs: dict[str, tuple[str, str, str | None]] = {}
    for head in closure.heads:
        if head.store_id in stores:
            refs[world_pin_ref(world_store_id, head.world_oid, head.binding)] = (
                head.store_id,
                head.head,
                head.world_oid,
            )
    for world in closure.worlds:
        if world.retention_ref is not None:
            refs[world.retention_ref] = ("__world_store__", world.oid, None)
    return refs


def _world_retention_receipt_payload(
    *,
    authority_ref: str,
    world_store_id: str,
    world_oid: str,
    closure: WorldClosure,
    retained_refs: tuple[str, ...],
) -> dict[str, object]:
    payload = {
        "schema": WORLD_RETENTION_RECEIPT_SCHEMA,
        "authority_ref": authority_ref,
        "world_store_id": world_store_id,
        "world_oid": world_oid,
        "closure_mode": "publish",
        "closure_digest": _closure_digest(closure),
        "retained_refs": sorted(retained_refs),
        "retained": [
            retained.to_json()
            for retained in _expected_retained_records_for_closure(closure, world_store_id=world_store_id)
        ],
    }
    return {**payload, "receipt_digest": canonical_digest(payload)}


def _world_fork_origin_receipt_payload(
    *,
    authority_ref: str,
    world_store_id: str,
    first_world_oid: str,
    forked_from_authority_ref: str,
    forked_from_world_oid: str,
) -> dict[str, object]:
    return _ForkOriginReceipt(
        authority_ref=authority_ref,
        world_store_id=world_store_id,
        first_world_oid=first_world_oid,
        forked_from_authority_ref=forked_from_authority_ref,
        forked_from_world_oid=forked_from_world_oid,
    ).to_json()


def _write_publication_lease(repo: pygit2.Repository, lease: _PublicationLease) -> pygit2.Oid:
    meta_builder = repo.TreeBuilder()
    insert_tree_entry(
        repo,
        meta_builder,
        "world-publication-lease.json",
        repo.create_blob(canonical_bytes(lease.to_json())),
        pygit2.GIT_FILEMODE_BLOB,
    )
    root_builder = repo.TreeBuilder()
    insert_tree_entry(repo, root_builder, "meta", meta_builder.write(), pygit2.GIT_FILEMODE_TREE)
    signature = pygit2.Signature("vcs-core world publication lease", "vcs-core@example.invalid")
    return create_commit_with_recovery(
        repo,
        None,
        signature,
        signature,
        f"world publication lease {lease.world_oid}",
        root_builder.write(),
        [],
    )


def _read_publication_lease(repo: pygit2.Repository, ref: str) -> _PublicationLease:
    try:
        target = repo.references[ref].target
    except KeyError as exc:
        raise KeyError(ref) from exc
    commit = require_commit(repo, pygit2.Oid(hex=str(target)), context="world publication lease")
    payload = load_canonical_json(_read_blob_bytes(repo, commit.tree, WORLD_PUBLICATION_LEASE_PATH))
    expected_keys = {
        "schema",
        "authority_ref",
        "world_store_id",
        "world_oid",
        "operation_id",
        "created_at_unix_ns",
        "lease_digest",
    }
    extra_keys = set(payload) - expected_keys
    if extra_keys:
        raise InvalidRepositoryStateError(f"unexpected publication lease fields: {sorted(extra_keys)!r}")
    missing_keys = expected_keys - set(payload)
    if missing_keys:
        raise InvalidRepositoryStateError(f"missing publication lease fields: {sorted(missing_keys)!r}")
    if payload.get("schema") != WORLD_PUBLICATION_LEASE_SCHEMA:
        raise InvalidRepositoryStateError(f"unsupported publication lease schema: {payload.get('schema')!r}")
    lease = _PublicationLease(
        authority_ref=_required_payload_str(payload, "publication lease", "authority_ref"),
        world_store_id=_required_payload_str(payload, "publication lease", "world_store_id"),
        world_oid=_required_payload_str(payload, "publication lease", "world_oid"),
        operation_id=_required_payload_str(payload, "publication lease", "operation_id"),
        created_at_unix_ns=_required_payload_int(payload, "publication lease", "created_at_unix_ns"),
    )
    if payload.get("lease_digest") != lease.to_json()["lease_digest"]:
        raise InvalidRepositoryStateError("publication lease digest disagrees with payload")
    return lease


def _expected_retained_refs_for_closure(closure: WorldClosure, *, world_store_id: str) -> tuple[str, ...]:
    refs = [world_pin_ref(world_store_id, head.world_oid, head.binding) for head in closure.heads]
    refs.extend(world.retention_ref for world in closure.worlds if world.retention_ref is not None)
    return tuple(sorted(refs))


def _expected_retained_records_for_closure(closure: WorldClosure, *, world_store_id: str) -> tuple[RetainedRef, ...]:
    retained: list[RetainedRef] = []
    for head in closure.heads:
        retained.append(
            RetainedRef(
                kind=SELECTED_HEAD_PIN,
                ref=world_pin_ref(world_store_id, head.world_oid, head.binding),
            )
        )
    for world in closure.worlds:
        if world.retention_ref is None:
            continue
        retained.append(RetainedRef(kind=CHILD_WORLD_RETENTION, ref=world.retention_ref))
    for evidence_ref in closure.evidence_refs:
        retained.append(RetainedRef(kind=EVIDENCE_REF, ref=evidence_ref.ref, digest=evidence_ref.evidence_digest))
    by_digest = {canonical_digest(item.to_json()): item for item in retained}
    return tuple(by_digest[key] for key in sorted(by_digest))


def _closure_digest(closure: WorldClosure) -> str:
    return canonical_digest(
        {
            "root_world_oid": closure.root_world_oid,
            "worlds": [_closure_world_json(world) for world in closure.worlds],
            "heads": [_closure_head_json(head) for head in closure.heads],
            "evidence_refs": [_closure_evidence_ref_json(ref) for ref in closure.evidence_refs],
        }
    )


def _closure_world_json(world: ClosureWorld) -> dict[str, object]:
    return {
        "oid": world.oid,
        "path": world.path,
        "edge_kind": world.edge_kind,
        "binding": world.binding,
        "retention_ref": world.retention_ref,
    }


def _closure_head_json(head: ClosureHead) -> dict[str, object]:
    return {
        "world_oid": head.world_oid,
        "path": head.path,
        "binding": head.binding,
        "store_id": head.store_id,
        "head": head.head,
    }


def _closure_evidence_ref_json(ref: ClosureEvidenceRef) -> dict[str, object]:
    return {
        "world_oid": ref.world_oid,
        "path": ref.path,
        "binding": ref.binding,
        "ref": ref.ref,
        "evidence_digest": ref.evidence_digest,
    }


def _extend_retention_receipt_issues(
    issues: list[StructuredIssue],
    repo: pygit2.Repository,
    world_oid: str,
    *,
    authority_refs: tuple[str, ...],
    world_store_id: str,
    closure: WorldClosure,
) -> None:
    for authority_ref in authority_refs:
        if not _world_is_protected_by_authority(repo, world_oid, authority_ref):
            continue
        receipt_ref = world_retention_receipt_ref(authority_ref, world_oid)
        try:
            receipt = _read_world_retention_receipt(repo, receipt_ref)
        except KeyError:
            issues.append(
                _issue(
                    "missing_retention_receipt",
                    "published world is missing retention receipt",
                    world_oid=world_oid,
                    ref=receipt_ref,
                    recovery_hint="Recreate the retention receipt after verifying selected-head pins.",
                )
            )
            continue
        except (InvalidRepositoryStateError, TypeError, ValueError) as exc:
            issues.append(
                _issue(
                    "corrupt_retention_receipt",
                    str(exc),
                    world_oid=world_oid,
                    ref=receipt_ref,
                    recovery_hint="Do not trust the corrupted receipt; verify pins before repair.",
                )
            )
            continue
        expected = {
            "schema": WORLD_RETENTION_RECEIPT_SCHEMA,
            "authority_ref": authority_ref,
            "world_store_id": world_store_id,
            "world_oid": world_oid,
            "closure_mode": "publish",
            "closure_digest": _closure_digest(closure),
            "retained_refs": list(_expected_retained_refs_for_closure(closure, world_store_id=world_store_id)),
            "retained": [
                retained.to_json()
                for retained in _expected_retained_records_for_closure(closure, world_store_id=world_store_id)
            ],
        }
        for key, expected_value in expected.items():
            if receipt.get(key) != expected_value:
                issues.append(
                    _issue(
                        "corrupt_retention_receipt",
                        f"retention receipt {key} disagrees with world",
                        world_oid=world_oid,
                        ref=receipt_ref,
                        recovery_hint="Regenerate the receipt from the published world closure.",
                    )
                )
                break


def _read_world_retention_receipt(repo: pygit2.Repository, ref: str) -> dict[str, object]:
    try:
        target = repo.references[ref].target
    except KeyError as exc:
        raise KeyError(ref) from exc
    commit = require_commit(repo, pygit2.Oid(hex=str(target)), context="world retention receipt")
    payload = load_canonical_json(_read_blob_bytes(repo, commit.tree, WORLD_RETENTION_RECEIPT_PATH))
    expected_keys = {
        "schema",
        "authority_ref",
        "world_store_id",
        "world_oid",
        "closure_mode",
        "closure_digest",
        "retained_refs",
        "retained",
        "receipt_digest",
    }
    extra_keys = set(payload) - expected_keys
    if extra_keys:
        raise InvalidRepositoryStateError(f"unexpected retention receipt fields: {sorted(extra_keys)!r}")
    missing_keys = expected_keys - set(payload)
    if missing_keys:
        raise InvalidRepositoryStateError(f"missing retention receipt fields: {sorted(missing_keys)!r}")
    if payload.get("schema") != WORLD_RETENTION_RECEIPT_SCHEMA:
        raise InvalidRepositoryStateError(f"unsupported retention receipt schema: {payload.get('schema')!r}")
    retained_refs = payload.get("retained_refs")
    if not isinstance(retained_refs, list) or not all(isinstance(item, str) and item for item in retained_refs):
        raise InvalidRepositoryStateError("retention receipt retained_refs must be a string list")
    if len(set(retained_refs)) != len(retained_refs):
        raise InvalidRepositoryStateError("retention receipt retained_refs must not contain duplicates")
    retained = payload.get("retained")
    if not isinstance(retained, list):
        raise InvalidRepositoryStateError("retention receipt retained must be a list")
    retained_seen: set[str] = set()
    for item in retained:
        try:
            retained_ref = RetainedRef.from_json(item)
            validate_retained_ref(retained_ref)
        except (TypeError, ValueError, InvalidRepositoryStateError) as exc:
            raise InvalidRepositoryStateError(f"retention receipt retained entry is invalid: {exc}") from exc
        retained_digest = canonical_digest(retained_ref.to_json())
        if retained_digest in retained_seen:
            raise InvalidRepositoryStateError("retention receipt retained entries must not contain duplicates")
        retained_seen.add(retained_digest)
    receipt_digest = payload.get("receipt_digest")
    unsigned = {key: value for key, value in payload.items() if key != "receipt_digest"}
    if receipt_digest != canonical_digest(unsigned):
        raise InvalidRepositoryStateError("retention receipt digest disagrees with payload")
    return payload


def _read_optional_world_fork_origin_receipt(repo: pygit2.Repository, ref: str) -> _ForkOriginReceipt | None:
    try:
        return _read_world_fork_origin_receipt(repo, ref)
    except KeyError:
        return None


def _read_world_fork_origin_receipt(repo: pygit2.Repository, ref: str) -> _ForkOriginReceipt:
    try:
        target = repo.references[ref].target
    except KeyError as exc:
        raise KeyError(ref) from exc
    commit = require_commit(repo, pygit2.Oid(hex=str(target)), context="world fork origin receipt")
    payload = load_canonical_json(_read_blob_bytes(repo, commit.tree, WORLD_FORK_ORIGIN_RECEIPT_PATH))
    expected_keys = {
        "schema",
        "authority_ref",
        "world_store_id",
        "first_world_oid",
        "forked_from_authority_ref",
        "forked_from_world_oid",
        "receipt_digest",
    }
    extra_keys = set(payload) - expected_keys
    if extra_keys:
        raise InvalidRepositoryStateError(f"unexpected fork origin receipt fields: {sorted(extra_keys)!r}")
    missing_keys = expected_keys - set(payload)
    if missing_keys:
        raise InvalidRepositoryStateError(f"missing fork origin receipt fields: {sorted(missing_keys)!r}")
    if payload.get("schema") != WORLD_FORK_ORIGIN_RECEIPT_SCHEMA:
        raise InvalidRepositoryStateError(f"unsupported fork origin receipt schema: {payload.get('schema')!r}")
    receipt_digest = payload.get("receipt_digest")
    unsigned = {key: value for key, value in payload.items() if key != "receipt_digest"}
    if receipt_digest != canonical_digest(unsigned):
        raise InvalidRepositoryStateError("fork origin receipt digest disagrees with payload")
    return _ForkOriginReceipt(
        authority_ref=_required_payload_str(payload, "fork origin receipt", "authority_ref"),
        world_store_id=_required_payload_str(payload, "fork origin receipt", "world_store_id"),
        first_world_oid=_required_payload_str(payload, "fork origin receipt", "first_world_oid"),
        forked_from_authority_ref=_required_payload_str(payload, "fork origin receipt", "forked_from_authority_ref"),
        forked_from_world_oid=_required_payload_str(payload, "fork origin receipt", "forked_from_world_oid"),
    )


def _required_payload_str(payload: Mapping[str, object], label: str, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidRepositoryStateError(f"{label} {key} must be a non-empty string")
    return value


def _optional_payload_str(payload: Mapping[str, object], label: str, key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InvalidRepositoryStateError(f"{label} {key} must be a non-empty string when present")
    return value


def _required_payload_int(payload: Mapping[str, object], label: str, key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidRepositoryStateError(f"{label} {key} must be an integer")
    return value


def _read_blob_bytes(repo: pygit2.Repository, tree: pygit2.Tree, path: str) -> bytes:
    obj: pygit2.Object = tree
    for component in path.split("/"):
        if not isinstance(obj, pygit2.Tree):
            raise TypeError(f"{path!r} did not resolve to a blob")
        obj = repo[obj[component].id]
    blob = require_blob(repo, obj.id, context=path)
    return bytes(blob.data)


def _world_is_protected_by_authorities(
    repo: pygit2.Repository, world_oid: str, authority_refs: tuple[str, ...]
) -> bool:
    return any(_world_is_protected_by_authority(repo, world_oid, authority_ref) for authority_ref in authority_refs)


def _world_is_protected_by_authority(repo: pygit2.Repository, world_oid: str, authority_ref: str) -> bool:
    try:
        target = str(repo.references[authority_ref].target)
    except KeyError:
        return False
    if target == world_oid:
        return True
    try:
        return bool(repo.descendant_of(pygit2.Oid(hex=target), pygit2.Oid(hex=world_oid)))
    except (ValueError, TypeError, pygit2.GitError):
        return False


def _world_selected_pins_are_authoritative(
    closure: WorldClosure,
    *,
    world_store_id: str,
    world_oid: str,
    protected_world_oids: frozenset[str],
    pin_classification: Mapping[str, tuple[str, ...]],
) -> bool:
    if world_oid not in protected_world_oids:
        return False
    bad_refs = set(pin_classification.get("missing_for_published_world", ())) | set(
        pin_classification.get("corrupt", ())
    )
    selected_pin_refs = {
        world_pin_ref(world_store_id, head.world_oid, head.binding)
        for head in closure.heads
        if head.world_oid == world_oid
    }
    return not selected_pin_refs.intersection(bad_refs)


def _authority_world_targets(repo: pygit2.Repository, authority_refs: tuple[str, ...]) -> frozenset[str]:
    targets: set[str] = set()
    for ref in authority_refs:
        try:
            targets.add(str(repo.references[ref].target))
        except KeyError:
            continue
    return frozenset(targets)


def _publish_authority_refs(ref: str, authority_refs: tuple[str, ...] | None) -> tuple[str, ...]:
    refs = (ref,) if authority_refs is None else (ref, *authority_refs)
    return tuple(dict.fromkeys(refs))


def _validate_advance_basis(world: WorldCommit, *, input_world_oid: str) -> None:
    if not input_world_oid:
        raise InvalidRepositoryStateError("advance publication requires input_world_oid")
    transition_input_world = world.transition.get("input_world")
    if transition_input_world != input_world_oid:
        raise InvalidRepositoryStateError("advance publication input_world_oid disagrees with world transition")
    if input_world_oid not in world.parent_oids:
        raise InvalidRepositoryStateError("advance publication input_world_oid must be a Git parent of the world")


def _current_ref_target(repo: pygit2.Repository, ref: str) -> str | None:
    try:
        return str(repo.references[ref].target)
    except KeyError:
        return None


def _delete_ref_if_targets(repo: pygit2.Repository, ref: str, oid: str) -> bool:
    if not _ref_targets(repo, ref, oid):
        return False
    result = subprocess.run(
        ["git", "update-ref", "-d", ref, oid], cwd=repo.path, capture_output=True, check=False, text=True
    )
    if result.returncode == 0:
        return True
    if not _ref_targets(repo, ref, oid):
        return False
    detail = (result.stderr or result.stdout or "git update-ref -d failed").strip()
    raise InvalidRepositoryStateError(f"failed to delete orphan retention ref {ref!r}: {detail}")


def _string_map(value: object, name: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise InvalidRepositoryStateError(f"{name} must be a string map")
    return dict(value)


def _object_list(value: object, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise InvalidRepositoryStateError(f"{name} must be an object list")
    return [dict(item) for item in value]


def _same_selected_head(left: SubstrateHead, right: SubstrateHead) -> bool:
    return (
        left.binding == right.binding
        and left.store_id == right.store_id
        and left.resource_id == right.resource_id
        and left.head == right.head
    )


def _selected_candidate_outcome_for_head(
    world: WorldCommit,
    head: SubstrateHead,
) -> dict[str, object] | None:
    matches: list[dict[str, object]] = []
    for outcome in _object_list(world.operation_final.get("candidate_outcomes"), "operation-final candidate_outcomes"):
        if outcome.get("binding") != head.binding or outcome.get("outcome") != "selected":
            continue
        if outcome.get("candidate") != head.head:
            continue
        store_id = outcome.get("store_id")
        if store_id is not None and store_id != head.store_id:
            raise InvalidRepositoryStateError("selected candidate outcome store_id disagrees with selected head")
        resource_id = outcome.get("resource_id")
        if resource_id is not None and resource_id != head.resource_id:
            raise InvalidRepositoryStateError("selected candidate outcome resource_id disagrees with selected head")
        matches.append(outcome)
    if len(matches) > 1:
        raise InvalidRepositoryStateError("operation-final contains duplicate selected candidate outcomes")
    return matches[0] if matches else None


def _candidate_outcome_producer_operation_id(world: WorldCommit, outcome: Mapping[str, object]) -> str:
    producer_operation_id = outcome.get("producer_operation_id")
    if producer_operation_id is None:
        return _world_operation_id(world)
    if not isinstance(producer_operation_id, str) or not producer_operation_id:
        raise InvalidRepositoryStateError("candidate outcome producer_operation_id must be a non-empty string")
    return producer_operation_id


def _candidate_outcome_candidate_id(outcome: Mapping[str, object]) -> str:
    candidate_id = outcome.get("candidate_id", "primary")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise InvalidRepositoryStateError("candidate outcome candidate_id must be a non-empty string")
    return candidate_id


def _candidate_tuple_matches_head(
    candidate_tuple: PreparedCandidateTupleRecord,
    head: SubstrateHead,
    *,
    producer_operation_id: str,
    candidate_id: str,
) -> bool:
    candidate = candidate_tuple.candidate
    return (
        candidate.operation_id == producer_operation_id
        and candidate.binding == head.binding
        and candidate.store_id == head.store_id
        and candidate.resource_id == head.resource_id
        and candidate.head == head.head
        and candidate.candidate_id == candidate_id
    )


def _ref_targets(repo: pygit2.Repository, ref: str, oid: str) -> bool:
    try:
        return str(repo.references[ref].target) == oid
    except KeyError:
        return False


def _validate_relative_locator(locator: str) -> None:
    path = Path(locator)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"substrate store locator must be a relative path without traversal: {locator!r}")
