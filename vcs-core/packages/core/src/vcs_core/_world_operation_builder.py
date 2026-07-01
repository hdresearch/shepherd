"""Build canonical operation-final evidence for private v2 world workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from vcs_core._transition_kernel_records import (
    CandidateCommitRecord,
    CandidateOutcomeRecord,
    EvidenceRef,
    HeadSelectionEvidence,
    HeadSelectionRecord,
    LogicalTransition,
    PreparedRevisionPlan,
    RelationshipRequirement,
    RetentionPolicyRequirement,
    RevisionPreparationRecord,
    validate_head_selection,
)
from vcs_core._world_retention import SELECTED_HEAD_PIN
from vcs_core._world_types import (
    OPERATION_FINAL_SCHEMA,
    CandidateRevision,
    OperationFinalRecord,
    WorldSnapshot,
    canonical_digest,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

SelectionKind = Literal["unchanged", "new-candidate", "child-produced", "bootstrap", "checkpoint", "revert", "import"]
PREPARED_WORLD_OPERATION_SCHEMA = "vcscore/prepared-world-operation/v1"
PREPARED_CANDIDATE_TUPLE_SCHEMA = "vcscore/prepared-candidate-tuple/v1"


@dataclass(frozen=True)
class PreparedCandidateTupleRecord:
    """Full pre-publication provenance tuple for one prepared candidate."""

    candidate: CandidateRevision
    transition: LogicalTransition
    plan: PreparedRevisionPlan
    preparation: RevisionPreparationRecord
    candidate_commit: CandidateCommitRecord

    def __post_init__(self) -> None:
        checks = (
            (self.candidate.operation_id, self.candidate_commit.operation_id, "operation_id"),
            (self.candidate.binding, self.candidate_commit.binding, "binding"),
            (self.candidate.candidate_id, self.candidate_commit.candidate_id, "candidate_id"),
            (self.candidate.store_id, self.candidate_commit.store_id, "store_id"),
            (self.candidate.resource_id, self.candidate_commit.resource_id, "resource_id"),
            (self.candidate.head, self.candidate_commit.candidate_head, "candidate_head"),
            (self.candidate.ref, self.candidate_commit.candidate_ref, "candidate_ref"),
            (self.candidate.binding, self.transition.binding, "transition binding"),
            (self.candidate.store_id, self.transition.store_id, "transition store_id"),
            (self.candidate.resource_id, self.transition.resource_id, "transition resource_id"),
            (self.candidate.binding, self.plan.binding, "plan binding"),
            (self.candidate.store_id, self.plan.store_id, "plan store_id"),
            (self.candidate.operation_id, self.preparation.operation_id, "preparation operation_id"),
            (self.candidate.binding, self.preparation.binding, "preparation binding"),
            (self.candidate.store_id, self.preparation.store_id, "preparation store_id"),
            (self.candidate.resource_id, self.preparation.resource_id, "preparation resource_id"),
            (self.transition.transition_digest(), self.plan.transition_digest, "plan transition_digest"),
            (self.transition.transition_digest(), self.preparation.transition_digest, "preparation transition_digest"),
            (
                self.plan.revision_plan_digest(),
                self.preparation.revision_plan_digest,
                "preparation revision_plan_digest",
            ),
            (
                self.preparation.revision_preparation_digest(),
                self.candidate_commit.revision_preparation_digest,
                "revision_preparation_digest",
            ),
        )
        for left, right, field in checks:
            if left != right:
                raise ValueError(f"prepared candidate tuple {field} disagrees")

    @classmethod
    def from_bundle(cls, bundle: Any) -> PreparedCandidateTupleRecord:
        return cls(
            candidate=bundle.candidate,
            transition=bundle.transition,
            plan=bundle.plan,
            preparation=bundle.preparation,
            candidate_commit=bundle.candidate_commit,
        )

    def tuple_digest(self) -> str:
        return canonical_digest(self._record_payload())

    def to_json(self) -> dict[str, object]:
        return {**self._record_payload(), "tuple_digest": self.tuple_digest()}

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> PreparedCandidateTupleRecord:
        _reject_unexpected_keys(
            value,
            {
                "schema",
                "candidate",
                "transition",
                "revision_plan",
                "preparation",
                "candidate_commit",
                "tuple_digest",
            },
            "prepared candidate tuple",
        )
        schema = value.get("schema")
        if schema != PREPARED_CANDIDATE_TUPLE_SCHEMA:
            raise ValueError(f"unsupported prepared candidate tuple schema: {schema!r}")
        record = cls(
            candidate=_candidate_revision_from_json(
                _object_map(value.get("candidate"), "prepared candidate tuple candidate")
            ),
            transition=LogicalTransition.from_json(
                _object_map(value.get("transition"), "prepared candidate tuple transition")
            ),
            plan=PreparedRevisionPlan.from_json(
                _object_map(value.get("revision_plan"), "prepared candidate tuple revision_plan")
            ),
            preparation=RevisionPreparationRecord.from_json(
                _object_map(value.get("preparation"), "prepared candidate tuple preparation")
            ),
            candidate_commit=CandidateCommitRecord.from_json(
                _object_map(value.get("candidate_commit"), "prepared candidate tuple candidate_commit")
            ),
        )
        tuple_digest = _required_digest(value.get("tuple_digest"), "tuple_digest")
        if record.tuple_digest() != tuple_digest:
            raise ValueError("prepared candidate tuple_digest disagrees with tuple")
        return record

    def _record_payload(self) -> dict[str, object]:
        return {
            "schema": PREPARED_CANDIDATE_TUPLE_SCHEMA,
            "candidate": _candidate_revision_to_json(self.candidate),
            "transition": self.transition.to_json(),
            "revision_plan": self.plan.to_json(),
            "preparation": self.preparation.to_json(),
            "candidate_commit": self.candidate_commit.to_json(),
        }


@dataclass(frozen=True)
class CandidateSelection:
    """A candidate revision paired with its durable commit evidence."""

    candidate: CandidateRevision
    candidate_commit: CandidateCommitRecord
    candidate_tuple: PreparedCandidateTupleRecord

    @classmethod
    def from_bundle(cls, bundle: Any) -> CandidateSelection:
        candidate_tuple = PreparedCandidateTupleRecord.from_bundle(bundle)
        return cls(candidate_tuple.candidate, candidate_tuple.candidate_commit, candidate_tuple)

    def __post_init__(self) -> None:
        checks = (
            (self.candidate.operation_id, self.candidate_commit.operation_id, "operation_id"),
            (self.candidate.binding, self.candidate_commit.binding, "binding"),
            (self.candidate.candidate_id, self.candidate_commit.candidate_id, "candidate_id"),
            (self.candidate.store_id, self.candidate_commit.store_id, "store_id"),
            (self.candidate.resource_id, self.candidate_commit.resource_id, "resource_id"),
            (self.candidate.head, self.candidate_commit.candidate_head, "candidate_head"),
            (self.candidate.ref, self.candidate_commit.candidate_ref, "candidate_ref"),
        )
        for candidate_value, commit_value, field in checks:
            if candidate_value != commit_value:
                raise ValueError(f"candidate selection {field} disagrees with candidate commit")
        if self.candidate_tuple is None:
            raise ValueError("candidate selection requires prepared candidate tuple")
        if self.candidate_tuple.candidate != self.candidate:
            raise ValueError("candidate selection tuple candidate disagrees")
        if self.candidate_tuple.candidate_commit != self.candidate_commit:
            raise ValueError("candidate selection tuple candidate_commit disagrees")


@dataclass(frozen=True)
class SelectionRequirementPlan:
    """Coordinator-owned plan for selecting an existing substrate head."""

    operation_id: str
    binding: str
    store_id: str
    resource_id: str
    selected_head: str
    selection_kind: SelectionKind
    selected_from: str | None = None
    relationship_requirements: tuple[RelationshipRequirement, ...] = ()
    retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = ()
    selection_policy_digest: str | None = None
    evidence_refs: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        for value, field in (
            (self.operation_id, "operation_id"),
            (self.binding, "binding"),
            (self.store_id, "store_id"),
            (self.resource_id, "resource_id"),
            (self.selected_head, "selected_head"),
            (self.selection_kind, "selection_kind"),
        ):
            _require_non_empty(value, field)
        if self.selection_kind in {"new-candidate", "child-produced"}:
            raise ValueError("selection requirement plans are only for existing heads")
        if self.selection_kind not in {"unchanged", "bootstrap", "checkpoint", "revert", "import"}:
            raise ValueError(f"unsupported selection kind: {self.selection_kind!r}")
        if self.selection_kind == "revert" and self.selected_from is None:
            raise ValueError("revert selection plan requires selected_from")
        if self.selection_kind != "revert" and self.selected_from is not None:
            raise ValueError("selected_from is only valid for revert selection plans")
        if self.selection_kind == "unchanged":
            if self.evidence_refs:
                raise ValueError("unchanged selection plan must not carry evidence refs")
        elif not self.evidence_refs:
            raise ValueError("existing-head selection plan requires coordinator evidence refs")
        _validate_selection_retention_requirements(
            self.retention_policy_requirements,
            selected_head=self.selected_head,
        )


@dataclass(frozen=True)
class CandidateSelectionPlan:
    """Coordinator-owned plan for selecting a prepared candidate revision."""

    operation_id: str
    selection: CandidateSelection
    selection_kind: Literal["new-candidate", "child-produced"]
    producer_operation_id: str
    producer_world_oid: str | None = None
    relationship_requirements: tuple[RelationshipRequirement, ...] = ()
    retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = ()
    selection_policy_digest: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.operation_id, "operation_id")
        _require_non_empty(self.producer_operation_id, "producer_operation_id")
        if self.selection_kind not in {"new-candidate", "child-produced"}:
            raise ValueError(f"unsupported candidate selection kind: {self.selection_kind!r}")
        if self.selection_kind == "new-candidate" and self.producer_world_oid is not None:
            raise ValueError("new-candidate selection plan must not carry producer_world_oid")
        if self.selection_kind == "new-candidate" and self.producer_operation_id != self.operation_id:
            raise ValueError("new-candidate selection plan requires current operation producer")
        if self.selection_kind == "child-produced" and self.producer_world_oid is None:
            raise ValueError("child-produced selection plan requires producer_world_oid")
        if self.selection_policy_digest is not None:
            _required_digest(self.selection_policy_digest, "selection_policy_digest")
        _validate_selection_retention_requirements(
            self.retention_policy_requirements,
            selected_head=self.selection.candidate.head,
        )


@dataclass(frozen=True)
class FinalizedWorldOperation:
    """Single artifact consumed by journaled world publication."""

    operation_id: str
    operation_kind: str
    target_ref: str
    input_world_oid: str | None
    snapshot: WorldSnapshot
    transition: Mapping[str, Any]
    operation_final: OperationFinalRecord
    candidate_refs: tuple[CandidateRevision, ...] = ()
    candidate_commits: tuple[CandidateCommitRecord, ...] = ()
    candidate_outcomes: tuple[CandidateOutcomeRecord, ...] = ()
    selected: Mapping[str, str] | None = None
    parents: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        final_selected = _string_map(self.operation_final.payload["selected"], "operation-final selected")
        selected = dict(final_selected if self.selected is None else self.selected)
        if selected != final_selected:
            raise ValueError("finalized operation selected heads disagree with operation-final")
        if selected != {binding: head.head for binding, head in self.snapshot.by_binding().items()}:
            raise ValueError("finalized operation selected heads disagree with snapshot")
        if self.operation_final.payload["operation_id"] != self.operation_id:
            raise ValueError("finalized operation id disagrees with operation-final")
        if self.transition.get("operation_id") != self.operation_id:
            raise ValueError("finalized operation id disagrees with transition")
        _validate_finalized_input_world(
            transition=self.transition,
            input_world_oid=self.input_world_oid,
            parents=self.parents,
        )
        final_candidate_commits = tuple(
            CandidateCommitRecord.from_json(dict(item))
            for item in _object_list(self.operation_final.payload["candidate_commits"], "candidate_commits")
        )
        final_candidate_outcomes = tuple(
            CandidateOutcomeRecord.from_operation_final_json(dict(item))
            for item in _object_list(self.operation_final.payload["candidate_outcomes"], "candidate_outcomes")
        )
        _reject_duplicate_candidate_outcomes(final_candidate_outcomes, final_operation_id=self.operation_id)
        if self.candidate_commits and _canonical_candidate_commits(
            self.candidate_commits
        ) != _canonical_candidate_commits(final_candidate_commits):
            raise ValueError("finalized candidate_commits disagree with operation-final")
        if self.candidate_outcomes and _canonical_outcome_payloads(
            [outcome.to_json(final_operation_id=self.operation_id) for outcome in self.candidate_outcomes]
        ) != _canonical_outcome_payloads(self.operation_final.payload["candidate_outcomes"]):
            raise ValueError("finalized candidate_outcomes disagree with operation-final")
        _validate_candidate_refs_have_outcomes(
            candidate_refs=self.candidate_refs,
            candidate_outcomes=self.operation_final.payload["candidate_outcomes"],
        )
        _validate_candidate_backed_selections(
            operation_id=self.operation_id,
            selected=selected,
            candidate_commits=final_candidate_commits,
            candidate_outcomes=final_candidate_outcomes,
            head_selections=self.operation_final.payload["head_selections"],
            selection_evidence=self.operation_final.payload["selection_evidence"],
        )
        object.__setattr__(self, "selected", selected)
        object.__setattr__(self, "candidate_commits", final_candidate_commits)
        object.__setattr__(self, "candidate_outcomes", final_candidate_outcomes)

    @property
    def snapshot_digest(self) -> str:
        return self.snapshot.digest()

    @property
    def operation_final_digest(self) -> str:
        return self.operation_final.digest()

    @property
    def candidate_outcome_payloads(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(item) for item in self.operation_final.payload["candidate_outcomes"])


@dataclass(frozen=True)
class PreparedWorldOperation:
    """Typed pre-publication operation state recorded before world commit."""

    operation_id: str
    operation_kind: str
    target_ref: str
    input_world_oid: str | None
    snapshot: WorldSnapshot
    transition: Mapping[str, Any]
    candidate_tuples: tuple[PreparedCandidateTupleRecord, ...] = ()
    candidate_refs: tuple[CandidateRevision, ...] = ()
    candidate_commits: tuple[CandidateCommitRecord, ...] = ()
    candidate_outcomes: tuple[CandidateOutcomeRecord, ...] = ()
    head_selections: tuple[dict[str, object], ...] = ()
    selection_evidence: tuple[dict[str, object], ...] = ()
    selected: Mapping[str, str] | None = None
    parents: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> PreparedWorldOperation:
        expected_keys = {
            "schema",
            "operation_id",
            "operation_kind",
            "target_ref",
            "input_world_oid",
            "snapshot",
            "snapshot_digest",
            "transition",
            "parents",
            "selected",
            "candidate_tuples",
            "candidate_refs",
            "candidate_commits",
            "candidate_outcomes",
            "head_selections",
            "selection_evidence",
            "operation_final_digest",
            "prepared_operation_digest",
        }
        extra_keys = set(value) - expected_keys
        if extra_keys:
            raise ValueError(f"unexpected prepared world operation fields: {sorted(extra_keys)!r}")
        schema = value.get("schema")
        if schema != PREPARED_WORLD_OPERATION_SCHEMA:
            raise ValueError(f"unsupported prepared world operation schema: {schema!r}")
        snapshot = WorldSnapshot.from_json(_object_map(value.get("snapshot"), "prepared operation snapshot"))
        snapshot_digest = _required_digest(value.get("snapshot_digest"), "snapshot_digest")
        if snapshot.digest() != snapshot_digest:
            raise ValueError("prepared operation snapshot_digest disagrees with snapshot")
        operation_id = _required_str(value.get("operation_id"), "operation_id")
        raw_candidate_outcomes = _object_list(value.get("candidate_outcomes"), "candidate_outcomes")
        candidate_outcomes = tuple(CandidateOutcomeRecord.from_record_json(item) for item in raw_candidate_outcomes)
        _validate_candidate_outcome_record_operation_ids(raw_candidate_outcomes, operation_id=operation_id)
        prepared = cls(
            operation_id=operation_id,
            operation_kind=_required_str(value.get("operation_kind"), "operation_kind"),
            target_ref=_required_str(value.get("target_ref"), "target_ref"),
            input_world_oid=_optional_str_value(value.get("input_world_oid"), "input_world_oid"),
            snapshot=snapshot,
            transition=_object_map(value.get("transition"), "prepared operation transition"),
            candidate_tuples=tuple(
                PreparedCandidateTupleRecord.from_json(item)
                for item in _object_list(value.get("candidate_tuples", []), "candidate_tuples")
            ),
            candidate_refs=tuple(
                _candidate_revision_from_json(item)
                for item in _object_list(value.get("candidate_refs"), "candidate_refs")
            ),
            candidate_commits=tuple(
                CandidateCommitRecord.from_json(item)
                for item in _object_list(value.get("candidate_commits"), "candidate_commits")
            ),
            candidate_outcomes=candidate_outcomes,
            head_selections=_object_list(value.get("head_selections"), "head_selections"),
            selection_evidence=_object_list(value.get("selection_evidence"), "selection_evidence"),
            selected=_string_map(value.get("selected"), "prepared operation selected"),
            parents=_string_list(value.get("parents"), "parents"),
        )
        operation_final_digest = _required_digest(value.get("operation_final_digest"), "operation_final_digest")
        if prepared.derive_operation_final().digest() != operation_final_digest:
            raise ValueError("prepared operation operation_final_digest disagrees with derived operation-final")
        prepared_digest = _required_digest(value.get("prepared_operation_digest"), "prepared_operation_digest")
        if prepared.prepared_operation_digest() != prepared_digest:
            raise ValueError("prepared_operation_digest disagrees with prepared operation")
        return prepared

    def __post_init__(self) -> None:
        if self.candidate_tuples:
            tuple_candidates = tuple(item.candidate for item in self.candidate_tuples)
            tuple_commits = tuple(item.candidate_commit for item in self.candidate_tuples)
            if not self.candidate_refs:
                object.__setattr__(self, "candidate_refs", tuple_candidates)
            elif _canonical_candidate_revisions(self.candidate_refs) != _canonical_candidate_revisions(
                tuple_candidates
            ):
                raise ValueError("prepared operation candidate_refs disagree with candidate tuples")
            if not self.candidate_commits:
                object.__setattr__(self, "candidate_commits", tuple_commits)
            elif _canonical_candidate_commits(self.candidate_commits) != _canonical_candidate_commits(tuple_commits):
                raise ValueError("prepared operation candidate_commits disagree with candidate tuples")
        selected = dict(
            {binding: head.head for binding, head in self.snapshot.by_binding().items()}
            if self.selected is None
            else self.selected
        )
        if selected != {binding: head.head for binding, head in self.snapshot.by_binding().items()}:
            raise ValueError("prepared operation selected heads disagree with snapshot")
        if self.transition.get("operation_id") != self.operation_id:
            raise ValueError("prepared operation id disagrees with transition")
        _validate_finalized_input_world(
            transition=self.transition,
            input_world_oid=self.input_world_oid,
            parents=self.parents,
        )
        _validate_prepared_selection_records(
            operation_id=self.operation_id,
            input_world_oid=self.input_world_oid,
            selected=selected,
            head_selections=self.head_selections,
            selection_evidence=self.selection_evidence,
        )
        finalized = self.finalize()
        object.__setattr__(self, "selected", dict(finalized.selected) if finalized.selected is not None else {})
        object.__setattr__(self, "candidate_commits", finalized.candidate_commits)
        object.__setattr__(self, "candidate_outcomes", finalized.candidate_outcomes)
        self.require_candidate_tuples()

    def prepared_operation_digest(self) -> str:
        return canonical_digest(self._record_payload())

    def require_candidate_tuples(self) -> None:
        tuple_keys = {
            (
                candidate_tuple.candidate_commit.operation_id,
                candidate_tuple.candidate_commit.binding,
                candidate_tuple.candidate_commit.candidate_id,
                candidate_tuple.candidate_commit.candidate_head,
            )
            for candidate_tuple in self.candidate_tuples
        }
        commit_keys = {
            (commit.operation_id, commit.binding, commit.candidate_id, commit.candidate_head)
            for commit in self.candidate_commits
        }
        if commit_keys and commit_keys != tuple_keys:
            raise ValueError("candidate-backed publication requires prepared candidate tuples")
        for outcome in self.candidate_outcomes:
            producer_operation_id = outcome.producer_operation_id or self.operation_id
            key = (producer_operation_id, outcome.binding, outcome.candidate_id, outcome.candidate)
            if key not in tuple_keys:
                raise ValueError("candidate-backed publication requires prepared candidate tuples")

    def derive_operation_final(self) -> OperationFinalRecord:
        return OperationFinalRecord(
            {
                "schema": OPERATION_FINAL_SCHEMA,
                "operation_id": self.operation_id,
                "selected": dict(
                    {binding: head.head for binding, head in self.snapshot.by_binding().items()}
                    if self.selected is None
                    else self.selected
                ),
                "candidate_commits": [commit.to_json() for commit in self.candidate_commits],
                "candidate_outcomes": [
                    outcome.to_json(final_operation_id=self.operation_id) for outcome in self.candidate_outcomes
                ],
                "head_selections": [dict(item) for item in self.head_selections],
                "selection_evidence": [dict(item) for item in self.selection_evidence],
            }
        )

    def finalize(self) -> FinalizedWorldOperation:
        return FinalizedWorldOperation(
            operation_id=self.operation_id,
            operation_kind=self.operation_kind,
            target_ref=self.target_ref,
            input_world_oid=self.input_world_oid,
            snapshot=self.snapshot,
            transition=dict(self.transition),
            operation_final=self.derive_operation_final(),
            candidate_refs=self.candidate_refs,
            candidate_commits=self.candidate_commits,
            candidate_outcomes=self.candidate_outcomes,
            selected=dict(self.selected) if self.selected is not None else None,
            parents=self.parents,
        )

    def to_json(self) -> dict[str, object]:
        return {**self._record_payload(), "prepared_operation_digest": self.prepared_operation_digest()}

    def _record_payload(self) -> dict[str, object]:
        operation_final = self.derive_operation_final()
        return {
            "schema": PREPARED_WORLD_OPERATION_SCHEMA,
            "operation_id": self.operation_id,
            "operation_kind": self.operation_kind,
            "target_ref": self.target_ref,
            "input_world_oid": self.input_world_oid,
            "snapshot": self.snapshot.to_json(),
            "snapshot_digest": self.snapshot.digest(),
            "transition": dict(self.transition),
            "parents": list(self.parents),
            "selected": dict(self.selected) if self.selected is not None else {},
            "candidate_tuples": _canonical_json_records(
                candidate_tuple.to_json() for candidate_tuple in self.candidate_tuples
            ),
            "candidate_refs": _canonical_candidate_revisions(self.candidate_refs),
            "candidate_commits": _canonical_candidate_commits(self.candidate_commits),
            "candidate_outcomes": _canonical_json_records(
                outcome.to_record_json(final_operation_id=self.operation_id) for outcome in self.candidate_outcomes
            ),
            "head_selections": _canonical_json_records(dict(item) for item in self.head_selections),
            "selection_evidence": _canonical_json_records(dict(item) for item in self.selection_evidence),
            "operation_final_digest": operation_final.digest(),
        }


@dataclass(frozen=True)
class _SelectionIntent:
    selection_kind: SelectionKind
    candidate_commit: CandidateCommitRecord | None = None
    candidate_outcome: CandidateOutcomeRecord | None = None
    candidate_tuple: PreparedCandidateTupleRecord | None = None
    selected_from: str | None = None
    relationship_requirements: tuple[RelationshipRequirement, ...] = ()
    retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = ()
    selection_policy_digest: str | None = None
    evidence_refs: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class _ArchivedCandidateOutcome:
    outcome: CandidateOutcomeRecord
    candidate_commit: CandidateCommitRecord
    candidate_tuple: PreparedCandidateTupleRecord


class OperationFinalBuilder:
    """Construct operation-final records from typed selection intents."""

    def __init__(self, operation_id: str) -> None:
        _require_non_empty(operation_id, "operation_id")
        self._operation_id = operation_id
        self._intents: dict[str, _SelectionIntent] = {}
        self._archived_outcomes: list[_ArchivedCandidateOutcome] = []

    def select_unchanged(
        self,
        *,
        plan: SelectionRequirementPlan,
    ) -> OperationFinalBuilder:
        if plan.operation_id != self._operation_id:
            raise ValueError("unchanged selection plan operation_id disagrees with builder")
        if plan.selection_kind != "unchanged":
            raise ValueError("select_unchanged requires an unchanged selection plan")
        return self._set_intent(
            plan.binding,
            _SelectionIntent(
                selection_kind="unchanged",
                relationship_requirements=plan.relationship_requirements,
                retention_policy_requirements=plan.retention_policy_requirements,
                selection_policy_digest=plan.selection_policy_digest,
                evidence_refs=plan.evidence_refs,
            ),
        )

    def select_candidate_plan(self, *, plan: CandidateSelectionPlan) -> OperationFinalBuilder:
        if plan.operation_id != self._operation_id:
            raise ValueError("candidate selection plan operation_id disagrees with builder")
        candidate = plan.selection.candidate
        candidate_tuple = plan.selection.candidate_tuple
        outcome = CandidateOutcomeRecord(
            binding=candidate.binding,
            candidate=candidate.head,
            outcome="selected",
            candidate_id=candidate.candidate_id,
            store_id=candidate.store_id,
            resource_id=candidate.resource_id,
            transition_digest=candidate_tuple.transition.transition_digest(),
            revision_plan_digest=candidate_tuple.plan.revision_plan_digest(),
            content_digest=candidate_tuple.plan.content_digest,
            revision_preparation_digest=candidate_tuple.preparation.revision_preparation_digest(),
            candidate_commit_digest=candidate_tuple.candidate_commit.candidate_commit_digest(),
            evidence_digests=candidate_tuple.preparation.evidence_digests,
            evidence_refs=candidate_tuple.preparation.evidence_refs,
            producer_operation_id=plan.producer_operation_id,
            producer_world_oid=plan.producer_world_oid,
        )
        return self._set_intent(
            candidate.binding,
            _SelectionIntent(
                selection_kind=plan.selection_kind,
                candidate_commit=plan.selection.candidate_commit,
                candidate_outcome=outcome,
                candidate_tuple=plan.selection.candidate_tuple,
                relationship_requirements=plan.relationship_requirements,
                retention_policy_requirements=plan.retention_policy_requirements,
                selection_policy_digest=plan.selection_policy_digest,
            ),
        )

    def archive_candidate(
        self,
        *,
        selection: CandidateSelection,
    ) -> OperationFinalBuilder:
        candidate = selection.candidate
        candidate_commit = selection.candidate_commit
        candidate_tuple = selection.candidate_tuple
        self._archived_outcomes.append(
            _ArchivedCandidateOutcome(
                outcome=CandidateOutcomeRecord(
                    binding=candidate.binding,
                    candidate=candidate.head,
                    outcome="archived",
                    candidate_id=candidate.candidate_id,
                    store_id=candidate.store_id,
                    resource_id=candidate.resource_id,
                    transition_digest=candidate_tuple.transition.transition_digest(),
                    revision_plan_digest=candidate_tuple.plan.revision_plan_digest(),
                    content_digest=candidate_tuple.plan.content_digest,
                    revision_preparation_digest=candidate_tuple.preparation.revision_preparation_digest(),
                    candidate_commit_digest=candidate_tuple.candidate_commit.candidate_commit_digest(),
                    evidence_digests=candidate_tuple.preparation.evidence_digests,
                    producer_operation_id=candidate_commit.operation_id,
                    evidence_refs=candidate_tuple.preparation.evidence_refs,
                ),
                candidate_commit=candidate_commit,
                candidate_tuple=selection.candidate_tuple,
            )
        )
        return self

    def select_existing(
        self,
        *,
        plan: SelectionRequirementPlan,
    ) -> OperationFinalBuilder:
        if plan.operation_id != self._operation_id:
            raise ValueError("existing-head selection plan operation_id disagrees with builder")
        if plan.selection_kind not in {"bootstrap", "checkpoint", "revert", "import"}:
            raise ValueError("select_existing requires bootstrap/checkpoint/import/revert selection plan")
        return self._set_intent(
            plan.binding,
            _SelectionIntent(
                selection_kind=plan.selection_kind,
                selected_from=plan.selected_from,
                relationship_requirements=plan.relationship_requirements,
                retention_policy_requirements=plan.retention_policy_requirements,
                selection_policy_digest=plan.selection_policy_digest,
                evidence_refs=plan.evidence_refs,
            ),
        )

    def build(
        self,
        *,
        operation_kind: str,
        target_ref: str,
        input_world_oid: str | None,
        snapshot: WorldSnapshot,
        transition: Mapping[str, Any],
        parents: tuple[str, ...] = (),
        candidate_refs: tuple[CandidateRevision, ...] = (),
        candidate_tuples: tuple[PreparedCandidateTupleRecord, ...] = (),
    ) -> FinalizedWorldOperation:
        return self.build_prepared(
            operation_kind=operation_kind,
            target_ref=target_ref,
            input_world_oid=input_world_oid,
            snapshot=snapshot,
            transition=transition,
            parents=parents,
            candidate_refs=candidate_refs,
            candidate_tuples=candidate_tuples,
        ).finalize()

    def build_prepared(
        self,
        *,
        operation_kind: str,
        target_ref: str,
        input_world_oid: str | None,
        snapshot: WorldSnapshot,
        transition: Mapping[str, Any],
        parents: tuple[str, ...] = (),
        candidate_refs: tuple[CandidateRevision, ...] = (),
        candidate_tuples: tuple[PreparedCandidateTupleRecord, ...] = (),
    ) -> PreparedWorldOperation:
        heads_by_binding = snapshot.by_binding()
        selected = {binding: head.head for binding, head in heads_by_binding.items()}
        unused_intents = set(self._intents) - set(heads_by_binding)
        if unused_intents:
            raise ValueError(f"selection intent names unknown binding {sorted(unused_intents)[0]!r}")
        selections: list[dict[str, object]] = []
        evidence: list[dict[str, object]] = []
        selected_outcomes: list[CandidateOutcomeRecord] = []
        candidate_commits: dict[str, CandidateCommitRecord] = {}
        prepared_candidate_tuples: dict[str, PreparedCandidateTupleRecord] = {
            candidate_tuple.candidate_commit.candidate_commit_digest(): candidate_tuple
            for candidate_tuple in candidate_tuples
        }
        for binding, head in sorted(heads_by_binding.items()):
            intent = self._intents.get(binding)
            if intent is None:
                raise ValueError(f"prepared operation requires explicit selection plan for binding {binding!r}")
            if intent.candidate_outcome is not None and intent.candidate_outcome.candidate != head.head:
                raise ValueError(f"candidate selection for binding {binding!r} disagrees with snapshot head")
            retention = intent.retention_policy_requirements
            _validate_selection_retention_requirements(retention, selected_head=head.head)
            selection = HeadSelectionRecord(
                binding=binding,
                store_id=head.store_id,
                resource_id=head.resource_id,
                selected_head=head.head,
                selection_kind=intent.selection_kind,
                selected_from=intent.selected_from,
                relationship_requirements=intent.relationship_requirements,
                retention_policy_requirements=retention,
                selection_policy_digest=intent.selection_policy_digest
                or canonical_digest({"selection": binding, "head": head.head}),
            )
            selection_evidence = HeadSelectionEvidence(
                operation_id=self._operation_id,
                binding=binding,
                store_id=head.store_id,
                resource_id=head.resource_id,
                selected_head=head.head,
                selection_digest=selection.selection_digest(),
                revision_preparation_digest=(
                    intent.candidate_commit.revision_preparation_digest if intent.candidate_commit is not None else None
                ),
                candidate_commit_digest=(
                    intent.candidate_commit.candidate_commit_digest() if intent.candidate_commit is not None else None
                ),
                candidate_ref=intent.candidate_commit.candidate_ref if intent.candidate_commit is not None else None,
                producer_operation_id=(
                    intent.candidate_outcome.producer_operation_id if intent.candidate_outcome is not None else None
                ),
                evidence_refs=intent.evidence_refs,
                retention_policy_requirements=retention,
            )
            validate_head_selection(selection, selection_evidence)
            selections.append(selection.to_json())
            evidence.append(selection_evidence.to_json())
            if intent.candidate_outcome is not None:
                selected_outcomes.append(intent.candidate_outcome)
            if intent.candidate_commit is not None:
                candidate_commits[intent.candidate_commit.candidate_commit_digest()] = intent.candidate_commit
            if intent.candidate_tuple is not None:
                prepared_candidate_tuples[intent.candidate_tuple.candidate_commit.candidate_commit_digest()] = (
                    intent.candidate_tuple
                )
        for archived in self._archived_outcomes:
            outcome = archived.outcome
            if outcome.binding not in selected:
                raise ValueError(f"archived candidate outcome names unknown binding {outcome.binding!r}")
            if outcome.candidate == selected[outcome.binding]:
                raise ValueError("archived candidate outcome must not name selected head")
            selected_outcomes.append(outcome)
            candidate_commits[archived.candidate_commit.candidate_commit_digest()] = archived.candidate_commit
            prepared_candidate_tuples[archived.candidate_tuple.candidate_commit.candidate_commit_digest()] = (
                archived.candidate_tuple
            )
        _reject_duplicate_candidate_outcomes(selected_outcomes, final_operation_id=self._operation_id)
        for candidate in candidate_refs:
            matching = [
                outcome
                for outcome in selected_outcomes
                if outcome.binding == candidate.binding
                and outcome.candidate == candidate.head
                and outcome.candidate_id == candidate.candidate_id
            ]
            if not matching:
                raise ValueError(f"candidate ref for binding {candidate.binding!r} has no candidate outcome")
        return PreparedWorldOperation(
            operation_id=self._operation_id,
            operation_kind=operation_kind,
            target_ref=target_ref,
            input_world_oid=input_world_oid,
            snapshot=snapshot,
            transition=dict(transition),
            candidate_tuples=tuple(prepared_candidate_tuples.values()),
            candidate_refs=candidate_refs,
            candidate_commits=tuple(candidate_commits.values()),
            candidate_outcomes=tuple(selected_outcomes),
            head_selections=tuple(selections),
            selection_evidence=tuple(evidence),
            selected=selected,
            parents=parents,
        )

    def _set_intent(self, binding: str, intent: _SelectionIntent) -> OperationFinalBuilder:
        _require_non_empty(binding, "binding")
        if binding in self._intents:
            raise ValueError(f"duplicate selection intent for binding {binding!r}")
        self._intents[binding] = intent
        return self


def _require_non_empty(value: str, field: str) -> None:
    if not value:
        raise ValueError(f"{field} is required")


def _string_map(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError(f"{label} must be a string map")
    return dict(value)


def _object_list(value: object, label: str) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be a list of objects")
    return tuple(dict(item) for item in value)


def _object_map(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys must be strings")
    return dict(value)


def _reject_unexpected_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    extra_keys = set(value) - expected
    if extra_keys:
        raise ValueError(f"unexpected {label} fields: {sorted(extra_keys)!r}")


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{label} must be a list of non-empty strings")
    return tuple(value)


def _required_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is required")
    return value


def _optional_str_value(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be null or a non-empty string")
    return value


def _required_digest(value: object, field: str) -> str:
    digest = _required_str(value, field)
    prefix = "sha256:"
    hex_digest = digest.removeprefix(prefix)
    if (
        not digest.startswith(prefix)
        or len(hex_digest) != 64
        or any(char not in "0123456789abcdefABCDEF" for char in hex_digest)
    ):
        raise ValueError(f"{field} must be a sha256 digest")
    return digest


def _canonical_candidate_commits(commits: tuple[CandidateCommitRecord, ...]) -> list[dict[str, object]]:
    return sorted((commit.to_json() for commit in commits), key=canonical_digest)


def _canonical_candidate_revisions(candidates: tuple[CandidateRevision, ...]) -> list[dict[str, object]]:
    return sorted((_candidate_revision_to_json(candidate) for candidate in candidates), key=canonical_digest)


def _canonical_json_records(records: Any) -> list[dict[str, object]]:
    return sorted((dict(record) for record in records), key=canonical_digest)


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


def _candidate_revision_from_json(value: Mapping[str, object]) -> CandidateRevision:
    return CandidateRevision(
        operation_id=_required_str(value.get("operation_id"), "candidate operation_id"),
        binding=_required_str(value.get("binding"), "candidate binding"),
        candidate_id=_required_str(value.get("candidate_id"), "candidate candidate_id"),
        store_id=_required_str(value.get("store_id"), "candidate store_id"),
        resource_id=_required_str(value.get("resource_id"), "candidate resource_id"),
        head=_required_str(value.get("head"), "candidate head"),
        ref=_required_str(value.get("ref"), "candidate ref"),
    )


def _canonical_outcome_payloads(outcomes: object) -> list[dict[str, object]]:
    return sorted((dict(item) for item in _object_list(outcomes, "candidate_outcomes")), key=canonical_digest)


def _reject_duplicate_candidate_outcomes(
    outcomes: list[CandidateOutcomeRecord] | tuple[CandidateOutcomeRecord, ...],
    *,
    final_operation_id: str,
) -> None:
    keys: set[tuple[str, str, str, str]] = set()
    for outcome in outcomes:
        producer_operation_id = outcome.producer_operation_id or final_operation_id
        key = (producer_operation_id, outcome.binding, outcome.candidate_id, outcome.candidate)
        if key in keys:
            raise ValueError("duplicate candidate outcome")
        keys.add(key)


def _validate_finalized_input_world(
    *,
    transition: Mapping[str, Any],
    input_world_oid: str | None,
    parents: tuple[str, ...],
) -> None:
    parent_worlds = transition.get("parent_worlds")
    if not isinstance(parent_worlds, list) or not all(isinstance(parent, str) for parent in parent_worlds):
        raise ValueError("finalized transition parent_worlds must be a string list")
    if tuple(parent_worlds) != parents:
        raise ValueError("finalized transition parent_worlds disagree with parents")
    if input_world_oid is None:
        if parents:
            raise ValueError("root finalized operation must not have world parents")
        if transition.get("input_world") is not None:
            raise ValueError("root finalized operation must not carry input_world")
        return
    if transition.get("input_world") != input_world_oid:
        raise ValueError("finalized input_world_oid disagrees with transition input_world")
    if input_world_oid not in parents:
        raise ValueError("finalized input_world_oid must be one of the world parents")


def _validate_prepared_selection_records(
    *,
    operation_id: str,
    input_world_oid: str | None,
    selected: Mapping[str, str],
    head_selections: tuple[dict[str, object], ...],
    selection_evidence: tuple[dict[str, object], ...],
) -> None:
    selections_by_binding: dict[str, HeadSelectionRecord] = {}
    for item in head_selections:
        selection = HeadSelectionRecord.from_json(dict(item))
        if selection.binding in selections_by_binding:
            raise ValueError("prepared operation contains duplicate head selection")
        selections_by_binding[selection.binding] = selection
    evidence_by_binding: dict[str, HeadSelectionEvidence] = {}
    for item in selection_evidence:
        evidence = HeadSelectionEvidence.from_json(dict(item))
        if evidence.operation_id != operation_id:
            raise ValueError("prepared operation selection evidence operation_id disagrees with operation")
        if evidence.binding in evidence_by_binding:
            raise ValueError("prepared operation contains duplicate selection evidence")
        evidence_by_binding[evidence.binding] = evidence
    if set(selections_by_binding) != set(selected):
        raise ValueError("prepared operation head selections must explain every selected binding")
    if set(evidence_by_binding) != set(selected):
        raise ValueError("prepared operation selection evidence must explain every selected binding")
    for binding, head in selected.items():
        selection = selections_by_binding[binding]
        evidence = evidence_by_binding[binding]
        if selection.selected_head != head:
            raise ValueError("prepared operation head selection disagrees with selected head")
        if input_world_oid is None and selection.selection_kind == "unchanged":
            raise ValueError("root prepared operation requires explicit bootstrap/import/checkpoint/revert selections")
        _validate_prepared_selection_boundary(selection, evidence)
        validate_head_selection(selection, evidence)


def _validate_prepared_selection_boundary(selection: HeadSelectionRecord, evidence: HeadSelectionEvidence) -> None:
    _validate_selection_retention_requirements(
        selection.retention_policy_requirements,
        selected_head=selection.selected_head,
    )
    if selection.selection_kind == "unchanged":
        if evidence.evidence_refs:
            raise ValueError("unchanged selection must not carry evidence refs")
        return
    if selection.selection_kind in {"bootstrap", "checkpoint", "import", "revert"} and not evidence.evidence_refs:
        raise ValueError("existing-head selection requires coordinator evidence refs")


def _validate_selection_retention_requirements(
    requirements: tuple[RetentionPolicyRequirement, ...],
    *,
    selected_head: str,
) -> None:
    if not requirements:
        raise ValueError("selection requires retention policy requirements")
    selected_head_pins = tuple(requirement for requirement in requirements if requirement.kind == SELECTED_HEAD_PIN)
    if len(selected_head_pins) != 1:
        raise ValueError("selection requires exactly one selected-head-pin retention policy")
    selected_head_pin = selected_head_pins[0]
    if selected_head_pin.target != selected_head:
        raise ValueError("selected-head-pin retention target must match selected head")
    if selected_head_pin.digest is not None:
        raise ValueError("selected-head-pin retention policy must not carry a digest")


def _validate_candidate_outcome_record_operation_ids(
    outcomes: tuple[dict[str, object], ...],
    *,
    operation_id: str,
) -> None:
    for outcome in outcomes:
        record_operation_id = _required_str(outcome.get("operation_id"), "candidate outcome operation_id")
        if record_operation_id != operation_id:
            raise ValueError("prepared operation candidate outcome operation_id disagrees with operation")


def _validate_candidate_refs_have_outcomes(
    *,
    candidate_refs: tuple[CandidateRevision, ...],
    candidate_outcomes: object,
) -> None:
    outcome_keys = {
        (outcome.get("binding"), outcome.get("candidate"), outcome.get("candidate_id", "primary"))
        for outcome in _object_list(candidate_outcomes, "candidate_outcomes")
    }
    for candidate in candidate_refs:
        if (candidate.binding, candidate.head, candidate.candidate_id) not in outcome_keys:
            raise ValueError(f"candidate ref for binding {candidate.binding!r} has no candidate outcome")


def _validate_candidate_backed_selections(
    *,
    operation_id: str,
    selected: Mapping[str, str],
    candidate_commits: tuple[CandidateCommitRecord, ...],
    candidate_outcomes: tuple[CandidateOutcomeRecord, ...],
    head_selections: object,
    selection_evidence: object,
) -> None:
    commits: dict[tuple[str, str, str, str], CandidateCommitRecord] = {}
    for commit_record in candidate_commits:
        commit_key = (
            commit_record.operation_id,
            commit_record.binding,
            commit_record.candidate_id,
            commit_record.candidate_head,
        )
        if commit_key in commits:
            raise ValueError("duplicate candidate commit record")
        commits[commit_key] = commit_record
    selections = {
        selection.binding: selection
        for selection in (
            HeadSelectionRecord.from_json(dict(item)) for item in _object_list(head_selections, "head_selections")
        )
    }
    evidences = {
        evidence.binding: evidence
        for evidence in (
            HeadSelectionEvidence.from_json(dict(item))
            for item in _object_list(selection_evidence, "selection_evidence")
        )
    }
    selected_candidate_keys: set[tuple[str, str, str]] = set()
    for outcome in candidate_outcomes:
        if outcome.binding not in selected:
            raise ValueError(f"candidate outcome names unknown binding {outcome.binding!r}")
        producer_operation_id = outcome.producer_operation_id or operation_id
        matching_commit = commits.get((producer_operation_id, outcome.binding, outcome.candidate_id, outcome.candidate))
        if matching_commit is None:
            raise ValueError("candidate outcome lacks matching candidate commit record")
        if outcome.outcome == "archived":
            if outcome.candidate == selected[outcome.binding]:
                raise ValueError("archived candidate outcome must not name selected head")
            continue
        if outcome.candidate != selected[outcome.binding]:
            raise ValueError("selected candidate outcome disagrees with selected head")
        selected_candidate_keys.add((outcome.binding, outcome.candidate_id, outcome.candidate))
        selection = selections.get(outcome.binding)
        evidence = evidences.get(outcome.binding)
        if selection is None or evidence is None:
            raise ValueError("selected candidate outcome requires head selection evidence")
        if selection.selection_kind == "new-candidate":
            if producer_operation_id != operation_id:
                raise ValueError("new-candidate selection requires current operation producer")
            if outcome.producer_world_oid is not None:
                raise ValueError("new-candidate selection must not carry producer_world_oid")
            if evidence.producer_operation_id not in {None, operation_id}:
                raise ValueError("new-candidate evidence producer_operation_id disagrees with operation")
        elif selection.selection_kind == "child-produced":
            if outcome.producer_world_oid is None:
                raise ValueError("child-produced selection requires producer_world_oid")
            if evidence.producer_operation_id != producer_operation_id:
                raise ValueError("child-produced evidence producer_operation_id disagrees with outcome")
        else:
            raise ValueError("selected candidate outcome requires candidate-backed head selection")
        if evidence.revision_preparation_digest != matching_commit.revision_preparation_digest:
            raise ValueError("selection evidence revision_preparation_digest disagrees with commit")
        if evidence.candidate_commit_digest != matching_commit.candidate_commit_digest():
            raise ValueError("selection evidence candidate_commit_digest disagrees with commit")
        if evidence.candidate_ref != matching_commit.candidate_ref:
            raise ValueError("selection evidence candidate_ref disagrees with commit")
    for selection in selections.values():
        if selection.selection_kind not in {"new-candidate", "child-produced"}:
            continue
        selected_key = (selection.binding, "primary", selection.selected_head)
        if selected_key not in selected_candidate_keys and not any(
            binding == selection.binding and head == selection.selected_head
            for binding, _candidate_id, head in selected_candidate_keys
        ):
            raise ValueError("candidate-backed head selection lacks selected candidate outcome")
