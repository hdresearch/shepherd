"""Private canonical records for the v2 transition-kernel prototype."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from vcs_core._world_types import canonical_bytes, canonical_digest, load_canonical_json

EVIDENCE_RECORD_SCHEMA = "vcscore/evidence-record/v1"
EVIDENCE_STABLE_SCHEMA = "vcscore/evidence-stable/v1"
EVIDENCE_ONLY_ENVELOPE_SCHEMA = "vcscore/evidence-only-envelope/v1"
VALIDATED_PAYLOAD_DESCRIPTOR_SCHEMA = "vcscore/validated-payload-descriptor/v1"
LOGICAL_TRANSITION_SCHEMA = "vcscore/logical-transition/v1"
PREPARED_REVISION_PLAN_SCHEMA = "vcscore/prepared-revision-plan/v1"
REVISION_PREPARATION_SCHEMA = "vcscore/revision-preparation/v2"
CANDIDATE_COMMIT_SCHEMA = "vcscore/candidate-commit/v1"
CANDIDATE_OUTCOME_SCHEMA = "vcscore/candidate-outcome/v1"
HEAD_SELECTION_SCHEMA = "vcscore/head-selection/v1"
HEAD_SELECTION_EVIDENCE_SCHEMA = "vcscore/head-selection-evidence/v1"
SELECTION_RETENTION_RECEIPT_SCHEMA = "vcscore/selection-retention-receipt/v1"

EntryOrdering = Literal["set", "sequence"]
SelectionKind = Literal["unchanged", "new-candidate", "child-produced", "bootstrap", "checkpoint", "revert", "import"]
# Single source of truth for the candidate-backed selection kinds: the frozenset is
# DERIVED from the Literal, so the two cannot drift. _world_selection_policy imports
# this Literal rather than re-declaring it.
CandidateBackedSelectionKind = Literal["new-candidate", "child-produced"]
CandidateOutcomeStatus = Literal["selected", "archived"]
PayloadAuthorityMode = Literal["coordinator-native", "registered-driver-codec"]

_CANDIDATE_BACKED_SELECTION_KINDS = frozenset(get_args(CandidateBackedSelectionKind))
JSON_PAYLOAD_CODEC_ID = "vcscore.json"
JSON_PAYLOAD_CODEC_VERSION = "v1"
JSON_PAYLOAD_AUTHORITY_MODE: PayloadAuthorityMode = "coordinator-native"
JSON_PAYLOAD_CANONICAL_MANIFEST: dict[str, object] = {"payload_format": "canonical-json-v1"}


@dataclass(frozen=True)
class EvidenceRecord:
    operation_id: str
    evidence_kind: str
    payload_digest: str
    stable_observation: dict[str, object]
    binding: str | None = None
    store_id: str | None = None
    substrate_kind: str | None = None
    ingress_kind: str | None = None
    observed_head: str | None = None
    observed_at_unix_ns: int | None = None
    mechanism: str | None = None
    correlation_id: str | None = None

    def evidence_digest(self) -> str:
        return canonical_digest(
            {
                "schema": EVIDENCE_STABLE_SCHEMA,
                "binding": self.binding,
                "store_id": self.store_id,
                "substrate_kind": self.substrate_kind,
                "ingress_kind": self.ingress_kind,
                "observed_head": self.observed_head,
                "evidence_kind": self.evidence_kind,
                "payload_digest": self.payload_digest,
                "stable_observation": self.stable_observation,
                "mechanism": self.mechanism,
                "correlation_id": self.correlation_id,
            }
        )

    def record_digest(self) -> str:
        return canonical_digest(self._record_payload())

    def _record_payload(self) -> dict[str, object]:
        return {
            "schema": EVIDENCE_RECORD_SCHEMA,
            "operation_id": self.operation_id,
            "binding": self.binding,
            "store_id": self.store_id,
            "substrate_kind": self.substrate_kind,
            "ingress_kind": self.ingress_kind,
            "observed_head": self.observed_head,
            "evidence_kind": self.evidence_kind,
            "payload_digest": self.payload_digest,
            "stable_observation": self.stable_observation,
            "observed_at_unix_ns": self.observed_at_unix_ns,
            "mechanism": self.mechanism,
            "correlation_id": self.correlation_id,
            "evidence_digest": self.evidence_digest(),
        }

    def to_json(self) -> dict[str, object]:
        return {**self._record_payload(), "record_digest": self.record_digest()}

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: dict[str, object]) -> EvidenceRecord:
        _reject_unexpected_keys(
            value,
            {
                "schema",
                "operation_id",
                "binding",
                "store_id",
                "substrate_kind",
                "ingress_kind",
                "observed_head",
                "evidence_kind",
                "payload_digest",
                "stable_observation",
                "observed_at_unix_ns",
                "mechanism",
                "correlation_id",
                "evidence_digest",
                "record_digest",
            },
            "evidence record",
        )
        _require_schema(value, EVIDENCE_RECORD_SCHEMA, "evidence record")
        stable_observation = value.get("stable_observation")
        if not isinstance(stable_observation, dict):
            raise TypeError("stable_observation must be an object")
        observed_at = value.get("observed_at_unix_ns")
        if observed_at is not None and not isinstance(observed_at, int):
            raise ValueError("observed_at_unix_ns must be an integer when present")
        record = cls(
            operation_id=_required_str(value, "operation_id"),
            binding=_optional_str(value, "binding"),
            store_id=_optional_str(value, "store_id"),
            substrate_kind=_optional_str(value, "substrate_kind"),
            ingress_kind=_optional_str(value, "ingress_kind"),
            observed_head=_optional_str(value, "observed_head"),
            evidence_kind=_required_str(value, "evidence_kind"),
            payload_digest=_required_digest(value, "payload_digest"),
            stable_observation=dict(stable_observation),
            observed_at_unix_ns=observed_at,
            mechanism=_optional_str(value, "mechanism"),
            correlation_id=_optional_str(value, "correlation_id"),
        )
        _require_digest_match(value, "evidence_digest", record.evidence_digest())
        _require_digest_match(value, "record_digest", record.record_digest())
        return record

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> EvidenceRecord:
        return cls.from_json(load_canonical_json(data))


@dataclass(frozen=True)
class EvidenceRef:
    ref: str
    evidence_digest: str
    record_digest: str
    payload_digest: str

    def to_json(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "evidence_digest": self.evidence_digest,
            "record_digest": self.record_digest,
            "payload_digest": self.payload_digest,
        }

    @classmethod
    def from_json(cls, value: object) -> EvidenceRef:
        if not isinstance(value, dict):
            raise TypeError("evidence ref must be an object")
        _reject_unexpected_keys(value, {"ref", "evidence_digest", "record_digest", "payload_digest"}, "evidence ref")
        return cls(
            ref=_required_str(value, "ref"),
            evidence_digest=_required_digest(value, "evidence_digest"),
            record_digest=_required_digest(value, "record_digest"),
            payload_digest=_required_digest(value, "payload_digest"),
        )


@dataclass(frozen=True)
class EvidenceOnlyEnvelopeRecord:
    """Coordinator-owned anchor for intentional evidence-only writes."""

    producer_operation_id: str
    envelope_id: str
    binding: str
    store_id: str
    resource_id: str
    substrate_kind: str
    ingress_kind: str
    evidence_refs: tuple[EvidenceRef, ...]
    evidence_kinds: tuple[str, ...]

    def envelope_digest(self) -> str:
        return canonical_digest(self._record_payload())

    def _record_payload(self) -> dict[str, object]:
        return {
            "schema": EVIDENCE_ONLY_ENVELOPE_SCHEMA,
            "producer_operation_id": self.producer_operation_id,
            "envelope_id": self.envelope_id,
            "binding": self.binding,
            "store_id": self.store_id,
            "resource_id": self.resource_id,
            "substrate_kind": self.substrate_kind,
            "ingress_kind": self.ingress_kind,
            "evidence_refs": [ref.to_json() for ref in self.evidence_refs],
            "evidence_digests": [ref.evidence_digest for ref in self.evidence_refs],
            "evidence_kinds": list(self.evidence_kinds),
        }

    def to_json(self) -> dict[str, object]:
        return {**self._record_payload(), "envelope_digest": self.envelope_digest()}

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: dict[str, object]) -> EvidenceOnlyEnvelopeRecord:
        _reject_unexpected_keys(
            value,
            {
                "schema",
                "producer_operation_id",
                "envelope_id",
                "binding",
                "store_id",
                "resource_id",
                "substrate_kind",
                "ingress_kind",
                "evidence_refs",
                "evidence_digests",
                "evidence_kinds",
                "envelope_digest",
            },
            "evidence-only envelope",
        )
        _require_schema(value, EVIDENCE_ONLY_ENVELOPE_SCHEMA, "evidence-only envelope")
        evidence_refs = tuple(EvidenceRef.from_json(item) for item in _required_list(value, "evidence_refs"))
        if not evidence_refs:
            raise ValueError("evidence-only envelope requires evidence_refs")
        evidence_digests = _required_digest_tuple(value, "evidence_digests")
        if evidence_digests != tuple(ref.evidence_digest for ref in evidence_refs):
            raise ValueError("evidence-only envelope evidence_digests disagree with evidence_refs")
        envelope = cls(
            producer_operation_id=_required_str(value, "producer_operation_id"),
            envelope_id=_required_str(value, "envelope_id"),
            binding=_required_str(value, "binding"),
            store_id=_required_str(value, "store_id"),
            resource_id=_required_str(value, "resource_id"),
            substrate_kind=_required_str(value, "substrate_kind"),
            ingress_kind=_required_str(value, "ingress_kind"),
            evidence_refs=evidence_refs,
            evidence_kinds=_required_str_tuple(value, "evidence_kinds"),
        )
        if len(envelope.evidence_kinds) != len(envelope.evidence_refs):
            raise ValueError("evidence-only envelope evidence_kinds must match evidence_refs")
        _require_digest_match(value, "envelope_digest", envelope.envelope_digest())
        return envelope

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> EvidenceOnlyEnvelopeRecord:
        return cls.from_json(load_canonical_json(data))


@dataclass(frozen=True)
class PayloadDescriptorClaim:
    """Driver-facing payload digest claim awaiting coordinator validation."""

    codec_id: str
    codec_version: str
    authority_mode: PayloadAuthorityMode
    payload_digest: str
    canonical_manifest: dict[str, object]
    payload_ref: str | None = None

    @classmethod
    def for_json_payload(cls, payload: dict[str, object]) -> PayloadDescriptorClaim:
        return cls(
            codec_id=JSON_PAYLOAD_CODEC_ID,
            codec_version=JSON_PAYLOAD_CODEC_VERSION,
            authority_mode=JSON_PAYLOAD_AUTHORITY_MODE,
            payload_digest=canonical_digest(payload),
            canonical_manifest=dict(JSON_PAYLOAD_CANONICAL_MANIFEST),
        )

    def validate(self, *, expected_payload_digest: str) -> ValidatedPayloadDescriptor:
        if self.payload_digest != expected_payload_digest:
            raise ValueError("payload descriptor claim digest disagrees with canonical payload")
        return ValidatedPayloadDescriptor(
            codec_id=self.codec_id,
            codec_version=self.codec_version,
            authority_mode=self.authority_mode,
            payload_digest=self.payload_digest,
            canonical_manifest=dict(self.canonical_manifest),
            payload_ref=self.payload_ref,
        )


@dataclass(frozen=True)
class ValidatedPayloadDescriptor:
    """Coordinator-accepted proof of payload digest codec authority."""

    codec_id: str
    codec_version: str
    authority_mode: PayloadAuthorityMode
    payload_digest: str
    canonical_manifest: dict[str, object]
    payload_ref: str | None = None

    @classmethod
    def for_json_payload(cls, payload: dict[str, object]) -> ValidatedPayloadDescriptor:
        return PayloadDescriptorClaim.for_json_payload(payload).validate(
            expected_payload_digest=canonical_digest(payload),
        )

    def descriptor_digest(self) -> str:
        return canonical_digest(self._record_payload())

    def _record_payload(self) -> dict[str, object]:
        return {
            "schema": VALIDATED_PAYLOAD_DESCRIPTOR_SCHEMA,
            "codec_id": self.codec_id,
            "codec_version": self.codec_version,
            "authority_mode": self.authority_mode,
            "payload_digest": self.payload_digest,
            "canonical_manifest": self.canonical_manifest,
            "payload_ref": self.payload_ref,
        }

    def to_json(self) -> dict[str, object]:
        return {**self._record_payload(), "descriptor_digest": self.descriptor_digest()}

    @classmethod
    def from_json(cls, value: dict[str, object]) -> ValidatedPayloadDescriptor:
        _reject_unexpected_keys(
            value,
            {
                "schema",
                "codec_id",
                "codec_version",
                "authority_mode",
                "payload_digest",
                "canonical_manifest",
                "payload_ref",
                "descriptor_digest",
            },
            "validated payload descriptor",
        )
        _require_schema(value, VALIDATED_PAYLOAD_DESCRIPTOR_SCHEMA, "validated payload descriptor")
        authority_mode = _required_str(value, "authority_mode")
        if authority_mode not in {"coordinator-native", "registered-driver-codec"}:
            raise ValueError(f"unsupported payload descriptor authority_mode: {authority_mode!r}")
        canonical_manifest = value.get("canonical_manifest")
        if not isinstance(canonical_manifest, dict):
            raise TypeError("payload descriptor canonical_manifest must be an object")
        descriptor = cls(
            codec_id=_required_str(value, "codec_id"),
            codec_version=_required_str(value, "codec_version"),
            authority_mode=cast("PayloadAuthorityMode", authority_mode),
            payload_digest=_required_digest(value, "payload_digest"),
            canonical_manifest=dict(canonical_manifest),
            payload_ref=_optional_str(value, "payload_ref"),
        )
        _require_digest_match(value, "descriptor_digest", descriptor.descriptor_digest())
        return descriptor


@dataclass(frozen=True)
class RelationshipRequirement:
    binding: str
    relation: str
    target_binding: str
    target_head: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("binding", self.binding),
            ("relation", self.relation),
            ("target_binding", self.target_binding),
            ("target_head", self.target_head),
        ):
            if not value:
                raise ValueError(f"{field_name} is required")
        if self.relation not in {"exact", "descends-from"}:
            raise ValueError(f"unsupported relationship relation: {self.relation!r}")

    def to_json(self) -> dict[str, object]:
        return {
            "binding": self.binding,
            "relation": self.relation,
            "target_binding": self.target_binding,
            "target_head": self.target_head,
        }

    @classmethod
    def from_json(cls, value: object) -> RelationshipRequirement:
        if not isinstance(value, dict):
            raise TypeError("relationship requirement must be an object")
        _reject_unexpected_keys(value, {"binding", "relation", "target_binding", "target_head"}, "relationship")
        return cls(
            binding=_required_str(value, "binding"),
            relation=_required_str(value, "relation"),
            target_binding=_required_str(value, "target_binding"),
            target_head=_required_str(value, "target_head"),
        )


@dataclass(frozen=True)
class RetentionPolicyRequirement:
    kind: str
    target: str
    digest: str | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"kind": self.kind, "target": self.target}
        if self.digest is not None:
            value["digest"] = self.digest
        return value

    @classmethod
    def from_json(cls, value: object) -> RetentionPolicyRequirement:
        if not isinstance(value, dict):
            raise TypeError("retention policy requirement must be an object")
        _reject_unexpected_keys(value, {"kind", "target", "digest"}, "retention policy requirement")
        return cls(
            kind=_required_str(value, "kind"),
            target=_required_str(value, "target"),
            digest=_optional_digest(value, "digest"),
        )


@dataclass(frozen=True)
class RetainedRef:
    kind: str
    ref: str
    digest: str | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"kind": self.kind, "ref": self.ref}
        if self.digest is not None:
            value["digest"] = self.digest
        return value

    @classmethod
    def from_json(cls, value: object) -> RetainedRef:
        if not isinstance(value, dict):
            raise TypeError("retained ref must be an object")
        _reject_unexpected_keys(value, {"kind", "ref", "digest"}, "retained ref")
        return cls(
            kind=_required_str(value, "kind"),
            ref=_required_str(value, "ref"),
            digest=_optional_digest(value, "digest"),
        )


@dataclass(frozen=True)
class LogicalTransition:
    binding: str
    store_id: str
    resource_id: str
    substrate_kind: str
    driver: str
    driver_version: str
    base_heads: tuple[str, ...]
    ingress_kind: str
    semantic_op: str
    payload_digest: str
    evidence_digests: tuple[str, ...] = ()
    requirements: tuple[RelationshipRequirement, ...] = ()
    idempotency_key: str | None = None

    def transition_digest(self) -> str:
        return canonical_digest(self._digest_payload())

    def _digest_payload(self) -> dict[str, object]:
        return {
            "schema": LOGICAL_TRANSITION_SCHEMA,
            "binding": self.binding,
            "store_id": self.store_id,
            "resource_id": self.resource_id,
            "substrate_kind": self.substrate_kind,
            "driver": self.driver,
            "driver_version": self.driver_version,
            "base_heads": list(self.base_heads),
            "ingress_kind": self.ingress_kind,
            "semantic_op": self.semantic_op,
            "payload_digest": self.payload_digest,
            "evidence_digests": _sorted_values(self.evidence_digests),
            "requirements": _sorted_json(requirement.to_json() for requirement in self.requirements),
            "idempotency_key": self.idempotency_key,
        }

    def to_json(self) -> dict[str, object]:
        return {**self._digest_payload(), "transition_digest": self.transition_digest()}

    @classmethod
    def from_json(cls, value: dict[str, object]) -> LogicalTransition:
        _reject_unexpected_keys(
            value,
            {
                "schema",
                "binding",
                "store_id",
                "resource_id",
                "substrate_kind",
                "driver",
                "driver_version",
                "base_heads",
                "ingress_kind",
                "semantic_op",
                "payload_digest",
                "evidence_digests",
                "requirements",
                "idempotency_key",
                "transition_digest",
            },
            "logical transition",
        )
        _require_schema(value, LOGICAL_TRANSITION_SCHEMA, "logical transition")
        transition = cls(
            binding=_required_str(value, "binding"),
            store_id=_required_str(value, "store_id"),
            resource_id=_required_str(value, "resource_id"),
            substrate_kind=_required_str(value, "substrate_kind"),
            driver=_required_str(value, "driver"),
            driver_version=_required_str(value, "driver_version"),
            base_heads=_required_str_tuple(value, "base_heads"),
            ingress_kind=_required_str(value, "ingress_kind"),
            semantic_op=_required_str(value, "semantic_op"),
            payload_digest=_required_digest(value, "payload_digest"),
            evidence_digests=_required_digest_tuple(value, "evidence_digests"),
            requirements=tuple(
                RelationshipRequirement.from_json(item) for item in _required_list(value, "requirements")
            ),
            idempotency_key=_optional_str(value, "idempotency_key"),
        )
        _require_digest_match(value, "transition_digest", transition.transition_digest())
        return transition


@dataclass(frozen=True)
class PreparedTransitionBundle:
    transition: LogicalTransition
    payload: object | None = None
    payload_ref: str | None = None

    def require_resolvable_payload(self) -> None:
        if self.payload is None and self.payload_ref is None:
            raise ValueError("prepared transition bundle must carry payload or payload_ref")


@dataclass(frozen=True)
class PreparedRevisionPlan:
    binding: str
    store_id: str
    transition_digest: str
    base_heads: tuple[str, ...]
    expected_parent_heads: tuple[str, ...]
    content_digest: str
    materialization_class: str
    entries: tuple[dict[str, object], ...]
    entry_ordering: EntryOrdering = "set"
    git_tree_oid: str | None = None

    def revision_plan_digest(self) -> str:
        return canonical_digest(self._digest_payload())

    def _digest_payload(self) -> dict[str, object]:
        if self.entry_ordering == "set":
            entries: list[object] = _sorted_json(self.entries)
        elif self.entry_ordering == "sequence":
            entries = list(self.entries)
        else:
            raise ValueError(f"unsupported revision plan entry ordering: {self.entry_ordering!r}")
        return {
            "schema": PREPARED_REVISION_PLAN_SCHEMA,
            "binding": self.binding,
            "store_id": self.store_id,
            "transition_digest": self.transition_digest,
            "base_heads": list(self.base_heads),
            "expected_parent_heads": list(self.expected_parent_heads),
            "content_digest": self.content_digest,
            "materialization_class": self.materialization_class,
            "entry_ordering": self.entry_ordering,
            "entries": entries,
        }

    def to_json(self) -> dict[str, object]:
        return {
            **self._digest_payload(),
            "git_tree_oid": self.git_tree_oid,
            "revision_plan_digest": self.revision_plan_digest(),
        }

    @classmethod
    def from_json(cls, value: dict[str, object]) -> PreparedRevisionPlan:
        _reject_unexpected_keys(
            value,
            {
                "schema",
                "binding",
                "store_id",
                "transition_digest",
                "base_heads",
                "expected_parent_heads",
                "content_digest",
                "materialization_class",
                "entry_ordering",
                "entries",
                "git_tree_oid",
                "revision_plan_digest",
            },
            "prepared revision plan",
        )
        _require_schema(value, PREPARED_REVISION_PLAN_SCHEMA, "prepared revision plan")
        entry_ordering = _required_str(value, "entry_ordering")
        if entry_ordering not in {"set", "sequence"}:
            raise ValueError(f"unsupported revision plan entry ordering: {entry_ordering!r}")
        entries = _required_list(value, "entries")
        if not all(isinstance(item, dict) for item in entries):
            raise ValueError("prepared revision plan entries must be objects")
        plan = cls(
            binding=_required_str(value, "binding"),
            store_id=_required_str(value, "store_id"),
            transition_digest=_required_digest(value, "transition_digest"),
            base_heads=_required_str_tuple(value, "base_heads"),
            expected_parent_heads=_required_str_tuple(value, "expected_parent_heads"),
            content_digest=_required_digest(value, "content_digest"),
            materialization_class=_required_str(value, "materialization_class"),
            entry_ordering=entry_ordering,  # type: ignore[arg-type]
            entries=tuple(dict(cast("dict[str, object]", item)) for item in entries),
            git_tree_oid=_optional_str(value, "git_tree_oid"),
        )
        _require_digest_match(value, "revision_plan_digest", plan.revision_plan_digest())
        return plan


@dataclass(frozen=True)
class RevisionPreparationRecord:
    operation_id: str
    binding: str
    store_id: str
    resource_id: str
    transition_digest: str
    revision_plan_digest: str
    content_digest: str
    evidence_digests: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    cited_evidence_refs: tuple[EvidenceRef, ...] = ()
    relationship_requirements: tuple[RelationshipRequirement, ...] = ()

    def revision_preparation_digest(self) -> str:
        return canonical_digest(self._digest_payload())

    def _digest_payload(self) -> dict[str, object]:
        return {
            "schema": REVISION_PREPARATION_SCHEMA,
            "operation_id": self.operation_id,
            "binding": self.binding,
            "store_id": self.store_id,
            "resource_id": self.resource_id,
            "transition_digest": self.transition_digest,
            "revision_plan_digest": self.revision_plan_digest,
            "content_digest": self.content_digest,
            "evidence_digests": _sorted_values(self.evidence_digests),
            "evidence_refs": [ref.to_json() for ref in self.evidence_refs],
            "cited_evidence_refs": [ref.to_json() for ref in self.cited_evidence_refs],
            "relationship_requirements": _sorted_json(
                requirement.to_json() for requirement in self.relationship_requirements
            ),
        }

    def to_json(self) -> dict[str, object]:
        return {**self._digest_payload(), "revision_preparation_digest": self.revision_preparation_digest()}

    @classmethod
    def from_json(cls, value: dict[str, object]) -> RevisionPreparationRecord:
        _reject_unexpected_keys(
            value,
            {
                "schema",
                "operation_id",
                "binding",
                "store_id",
                "resource_id",
                "transition_digest",
                "revision_plan_digest",
                "content_digest",
                "evidence_digests",
                "evidence_refs",
                "cited_evidence_refs",
                "relationship_requirements",
                "revision_preparation_digest",
            },
            "revision preparation",
        )
        _require_schema(value, REVISION_PREPARATION_SCHEMA, "revision preparation")
        record = cls(
            operation_id=_required_str(value, "operation_id"),
            binding=_required_str(value, "binding"),
            store_id=_required_str(value, "store_id"),
            resource_id=_required_str(value, "resource_id"),
            transition_digest=_required_digest(value, "transition_digest"),
            revision_plan_digest=_required_digest(value, "revision_plan_digest"),
            content_digest=_required_digest(value, "content_digest"),
            evidence_digests=_required_digest_tuple(value, "evidence_digests"),
            evidence_refs=tuple(EvidenceRef.from_json(item) for item in _required_list(value, "evidence_refs")),
            cited_evidence_refs=tuple(
                EvidenceRef.from_json(item) for item in _required_list(value, "cited_evidence_refs")
            ),
            relationship_requirements=tuple(
                RelationshipRequirement.from_json(item) for item in _required_list(value, "relationship_requirements")
            ),
        )
        _require_digest_match(value, "revision_preparation_digest", record.revision_preparation_digest())
        return record


@dataclass(frozen=True)
class CandidateCommitRecord:
    operation_id: str
    binding: str
    store_id: str
    resource_id: str
    candidate_head: str
    candidate_ref: str
    revision_preparation_digest: str
    candidate_id: str = "primary"

    def candidate_commit_digest(self) -> str:
        return canonical_digest(self._digest_payload())

    def _digest_payload(self) -> dict[str, object]:
        return {
            "schema": CANDIDATE_COMMIT_SCHEMA,
            "operation_id": self.operation_id,
            "binding": self.binding,
            "candidate_id": self.candidate_id,
            "store_id": self.store_id,
            "resource_id": self.resource_id,
            "candidate_head": self.candidate_head,
            "candidate_ref": self.candidate_ref,
            "revision_preparation_digest": self.revision_preparation_digest,
        }

    def to_json(self) -> dict[str, object]:
        return {**self._digest_payload(), "candidate_commit_digest": self.candidate_commit_digest()}

    @classmethod
    def from_json(cls, value: dict[str, object]) -> CandidateCommitRecord:
        _reject_unexpected_keys(
            value,
            {
                "schema",
                "operation_id",
                "binding",
                "candidate_id",
                "store_id",
                "resource_id",
                "candidate_head",
                "candidate_ref",
                "revision_preparation_digest",
                "candidate_commit_digest",
            },
            "candidate commit",
        )
        _require_schema(value, CANDIDATE_COMMIT_SCHEMA, "candidate commit")
        record = cls(
            operation_id=_required_str(value, "operation_id"),
            binding=_required_str(value, "binding"),
            store_id=_required_str(value, "store_id"),
            resource_id=_required_str(value, "resource_id"),
            candidate_head=_required_str(value, "candidate_head"),
            candidate_ref=_required_str(value, "candidate_ref"),
            revision_preparation_digest=_required_digest(value, "revision_preparation_digest"),
            candidate_id=_optional_str(value, "candidate_id") or "primary",
        )
        _require_digest_match(value, "candidate_commit_digest", record.candidate_commit_digest())
        return record


@dataclass(frozen=True)
class CandidateOutcomeRecord:
    """Typed candidate outcome entry embedded in operation-final records.

    ``to_json`` preserves the current compact operation-final payload shape.
    ``to_record_json`` is the canonical kernel record shape with schema and
    digest fields for prepared-operation/journal use.
    """

    binding: str
    candidate: str
    outcome: CandidateOutcomeStatus
    candidate_id: str = "primary"
    store_id: str | None = None
    resource_id: str | None = None
    transition_digest: str | None = None
    revision_plan_digest: str | None = None
    content_digest: str | None = None
    revision_preparation_digest: str | None = None
    candidate_commit_digest: str | None = None
    evidence_digests: tuple[str, ...] = ()
    producer_operation_id: str | None = None
    producer_world_oid: str | None = None
    evidence_refs: tuple[EvidenceRef, ...] = ()

    def outcome_digest(self, *, final_operation_id: str) -> str:
        return canonical_digest(self._digest_payload(final_operation_id=final_operation_id))

    def record_digest(self, *, final_operation_id: str) -> str:
        return canonical_digest(self._record_payload(final_operation_id=final_operation_id))

    def _digest_payload(self, *, final_operation_id: str) -> dict[str, object]:
        return {
            "schema": CANDIDATE_OUTCOME_SCHEMA,
            "operation_id": final_operation_id,
            "binding": self.binding,
            "candidate": self.candidate,
            "outcome": self.outcome,
            "candidate_id": self.candidate_id,
            "store_id": self.store_id,
            "resource_id": self.resource_id,
            "transition_digest": self.transition_digest,
            "revision_plan_digest": self.revision_plan_digest,
            "content_digest": self.content_digest,
            "revision_preparation_digest": self.revision_preparation_digest,
            "candidate_commit_digest": self.candidate_commit_digest,
            "evidence_digests": sorted(self.evidence_digests),
            "producer_operation_id": self.producer_operation_id or final_operation_id,
            "evidence_refs": _sorted_json(ref.to_json() for ref in self.evidence_refs),
        }

    def _record_payload(self, *, final_operation_id: str) -> dict[str, object]:
        return {
            **self._digest_payload(final_operation_id=final_operation_id),
            "producer_world_oid": self.producer_world_oid,
            "outcome_digest": self.outcome_digest(final_operation_id=final_operation_id),
        }

    def to_record_json(self, *, final_operation_id: str) -> dict[str, object]:
        return {
            **self._record_payload(final_operation_id=final_operation_id),
            "record_digest": self.record_digest(final_operation_id=final_operation_id),
        }

    def to_json(self, *, final_operation_id: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "binding": self.binding,
            "candidate": self.candidate,
            "outcome": self.outcome,
        }
        if self.candidate_id != "primary":
            payload["candidate_id"] = self.candidate_id
        payload.update(
            {
                key: value
                for key, value in (
                    ("store_id", self.store_id),
                    ("resource_id", self.resource_id),
                    ("transition_digest", self.transition_digest),
                    ("revision_plan_digest", self.revision_plan_digest),
                    ("content_digest", self.content_digest),
                    ("revision_preparation_digest", self.revision_preparation_digest),
                    ("candidate_commit_digest", self.candidate_commit_digest),
                )
                if value is not None
            }
        )
        if self.evidence_digests:
            payload["evidence_digests"] = sorted(self.evidence_digests)
        if self.producer_operation_id is not None and self.producer_operation_id != final_operation_id:
            payload["producer_operation_id"] = self.producer_operation_id
        if self.producer_world_oid is not None:
            payload["producer_world_oid"] = self.producer_world_oid
        if self.evidence_refs:
            payload["evidence_refs"] = _sorted_json(ref.to_json() for ref in self.evidence_refs)
        return payload

    @classmethod
    def from_record_json(cls, value: dict[str, object]) -> CandidateOutcomeRecord:
        _reject_unexpected_keys(
            value,
            {
                "schema",
                "operation_id",
                "binding",
                "candidate",
                "outcome",
                "candidate_id",
                "store_id",
                "resource_id",
                "transition_digest",
                "revision_plan_digest",
                "content_digest",
                "revision_preparation_digest",
                "candidate_commit_digest",
                "evidence_digests",
                "producer_operation_id",
                "producer_world_oid",
                "evidence_refs",
                "outcome_digest",
                "record_digest",
            },
            "candidate outcome",
        )
        _require_schema(value, CANDIDATE_OUTCOME_SCHEMA, "candidate outcome")
        operation_id = _required_str(value, "operation_id")
        producer_operation_id = _optional_str(value, "producer_operation_id")
        record = cls._from_payload_json(value, compact=False)
        if producer_operation_id == operation_id:
            record = cls(
                binding=record.binding,
                candidate=record.candidate,
                outcome=record.outcome,
                candidate_id=record.candidate_id,
                store_id=record.store_id,
                resource_id=record.resource_id,
                transition_digest=record.transition_digest,
                revision_plan_digest=record.revision_plan_digest,
                content_digest=record.content_digest,
                revision_preparation_digest=record.revision_preparation_digest,
                candidate_commit_digest=record.candidate_commit_digest,
                evidence_digests=record.evidence_digests,
                producer_operation_id=None,
                producer_world_oid=record.producer_world_oid,
                evidence_refs=record.evidence_refs,
            )
        _require_digest_match(value, "outcome_digest", record.outcome_digest(final_operation_id=operation_id))
        _require_digest_match(value, "record_digest", record.record_digest(final_operation_id=operation_id))
        return record

    @classmethod
    def from_operation_final_json(cls, value: dict[str, object]) -> CandidateOutcomeRecord:
        return cls._from_payload_json(value, compact=True)

    @classmethod
    def from_json(cls, value: dict[str, object]) -> CandidateOutcomeRecord:
        return cls.from_record_json(value)

    @classmethod
    def _from_payload_json(cls, value: dict[str, object], *, compact: bool) -> CandidateOutcomeRecord:
        _reject_unexpected_keys(
            value,
            {
                "schema",
                "operation_id",
                "binding",
                "candidate",
                "outcome",
                "candidate_id",
                "store_id",
                "resource_id",
                "transition_digest",
                "revision_plan_digest",
                "content_digest",
                "revision_preparation_digest",
                "candidate_commit_digest",
                "evidence_digests",
                "producer_operation_id",
                "producer_world_oid",
                "evidence_refs",
                "outcome_digest",
                "record_digest",
            }
            if not compact
            else {
                "binding",
                "candidate",
                "outcome",
                "candidate_id",
                "store_id",
                "resource_id",
                "transition_digest",
                "revision_plan_digest",
                "content_digest",
                "revision_preparation_digest",
                "candidate_commit_digest",
                "evidence_digests",
                "producer_operation_id",
                "producer_world_oid",
                "evidence_refs",
            },
            "candidate outcome",
        )
        outcome = _required_str(value, "outcome")
        if outcome not in {"selected", "archived"}:
            raise ValueError(f"unknown candidate outcome status: {outcome!r}")
        raw_evidence_refs = value.get("evidence_refs", [])
        if not isinstance(raw_evidence_refs, list):
            raise TypeError("evidence_refs must be a list")
        evidence_digests = _required_digest_tuple(
            {**value, "evidence_digests": value.get("evidence_digests", [])}, "evidence_digests"
        )
        return cls(
            binding=_required_str(value, "binding"),
            candidate=_required_str(value, "candidate"),
            outcome=outcome,  # type: ignore[arg-type]
            candidate_id=_optional_str(value, "candidate_id") or "primary",
            store_id=_optional_str(value, "store_id"),
            resource_id=_optional_str(value, "resource_id"),
            transition_digest=_optional_digest(value, "transition_digest"),
            revision_plan_digest=_optional_digest(value, "revision_plan_digest"),
            content_digest=_optional_digest(value, "content_digest"),
            revision_preparation_digest=_optional_digest(value, "revision_preparation_digest"),
            candidate_commit_digest=_optional_digest(value, "candidate_commit_digest"),
            evidence_digests=evidence_digests,
            producer_operation_id=_optional_str(value, "producer_operation_id"),
            producer_world_oid=_optional_str(value, "producer_world_oid"),
            evidence_refs=tuple(EvidenceRef.from_json(item) for item in raw_evidence_refs),
        )


@dataclass(frozen=True)
class HeadSelectionRecord:
    binding: str
    store_id: str
    resource_id: str
    selected_head: str
    selection_kind: SelectionKind
    selected_from: str | None = None
    relationship_requirements: tuple[RelationshipRequirement, ...] = ()
    retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = ()
    selection_policy_digest: str | None = None

    def selection_digest(self) -> str:
        return canonical_digest(self._digest_payload())

    def _digest_payload(self) -> dict[str, object]:
        return {
            "schema": HEAD_SELECTION_SCHEMA,
            "binding": self.binding,
            "store_id": self.store_id,
            "resource_id": self.resource_id,
            "selected_head": self.selected_head,
            "selection_kind": self.selection_kind,
            "selected_from": self.selected_from,
            "relationship_requirements": _sorted_json(
                requirement.to_json() for requirement in self.relationship_requirements
            ),
            "retention_policy_requirements": _sorted_json(
                requirement.to_json() for requirement in self.retention_policy_requirements
            ),
            "selection_policy_digest": self.selection_policy_digest,
        }

    def to_json(self) -> dict[str, object]:
        return {
            **self._digest_payload(),
            "selection_digest": self.selection_digest(),
        }

    @classmethod
    def from_json(cls, value: dict[str, object]) -> HeadSelectionRecord:
        _reject_unexpected_keys(
            value,
            {
                "schema",
                "binding",
                "store_id",
                "resource_id",
                "selected_head",
                "selection_kind",
                "selected_from",
                "relationship_requirements",
                "retention_policy_requirements",
                "selection_policy_digest",
                "selection_digest",
            },
            "head selection",
        )
        _require_schema(value, HEAD_SELECTION_SCHEMA, "head selection")
        selection_kind = _required_str(value, "selection_kind")
        if selection_kind not in {
            "unchanged",
            "new-candidate",
            "child-produced",
            "bootstrap",
            "checkpoint",
            "revert",
            "import",
        }:
            raise ValueError(f"unsupported selection kind: {selection_kind!r}")
        selection = cls(
            binding=_required_str(value, "binding"),
            store_id=_required_str(value, "store_id"),
            resource_id=_required_str(value, "resource_id"),
            selected_head=_required_str(value, "selected_head"),
            selection_kind=selection_kind,  # type: ignore[arg-type]
            selected_from=_optional_str(value, "selected_from"),
            relationship_requirements=tuple(
                RelationshipRequirement.from_json(item) for item in _required_list(value, "relationship_requirements")
            ),
            retention_policy_requirements=tuple(
                RetentionPolicyRequirement.from_json(item)
                for item in _required_list(value, "retention_policy_requirements")
            ),
            selection_policy_digest=_optional_digest(value, "selection_policy_digest"),
        )
        _require_digest_match(value, "selection_digest", selection.selection_digest())
        return selection


@dataclass(frozen=True)
class HeadSelectionEvidence:
    operation_id: str
    binding: str
    store_id: str
    resource_id: str
    selected_head: str
    selection_digest: str
    revision_preparation_digest: str | None = None
    candidate_commit_digest: str | None = None
    candidate_ref: str | None = None
    producer_operation_id: str | None = None
    evidence_refs: tuple[EvidenceRef, ...] = ()
    retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = ()

    def selection_evidence_digest(self) -> str:
        return canonical_digest(self._digest_payload())

    def _digest_payload(self) -> dict[str, object]:
        return {
            "schema": HEAD_SELECTION_EVIDENCE_SCHEMA,
            "operation_id": self.operation_id,
            "binding": self.binding,
            "store_id": self.store_id,
            "resource_id": self.resource_id,
            "selected_head": self.selected_head,
            "selection_digest": self.selection_digest,
            "revision_preparation_digest": self.revision_preparation_digest,
            "candidate_commit_digest": self.candidate_commit_digest,
            "candidate_ref": self.candidate_ref,
            "producer_operation_id": self.producer_operation_id,
            "evidence_refs": _sorted_json(ref.to_json() for ref in self.evidence_refs),
            "retention_policy_requirements": _sorted_json(
                requirement.to_json() for requirement in self.retention_policy_requirements
            ),
        }

    def to_json(self) -> dict[str, object]:
        return {**self._digest_payload(), "selection_evidence_digest": self.selection_evidence_digest()}

    @classmethod
    def from_json(cls, value: dict[str, object]) -> HeadSelectionEvidence:
        _reject_unexpected_keys(
            value,
            {
                "schema",
                "operation_id",
                "binding",
                "store_id",
                "resource_id",
                "selected_head",
                "selection_digest",
                "revision_preparation_digest",
                "candidate_commit_digest",
                "candidate_ref",
                "producer_operation_id",
                "evidence_refs",
                "retention_policy_requirements",
                "selection_evidence_digest",
            },
            "head selection evidence",
        )
        _require_schema(value, HEAD_SELECTION_EVIDENCE_SCHEMA, "head selection evidence")
        evidence = cls(
            operation_id=_required_str(value, "operation_id"),
            binding=_required_str(value, "binding"),
            store_id=_required_str(value, "store_id"),
            resource_id=_required_str(value, "resource_id"),
            selected_head=_required_str(value, "selected_head"),
            selection_digest=_required_digest(value, "selection_digest"),
            revision_preparation_digest=_optional_digest(value, "revision_preparation_digest"),
            candidate_commit_digest=_optional_digest(value, "candidate_commit_digest"),
            candidate_ref=_optional_str(value, "candidate_ref"),
            producer_operation_id=_optional_str(value, "producer_operation_id"),
            evidence_refs=tuple(EvidenceRef.from_json(item) for item in _required_list(value, "evidence_refs")),
            retention_policy_requirements=tuple(
                RetentionPolicyRequirement.from_json(item)
                for item in _required_list(value, "retention_policy_requirements")
            ),
        )
        _require_digest_match(value, "selection_evidence_digest", evidence.selection_evidence_digest())
        return evidence


@dataclass(frozen=True)
class SelectionRetentionReceipt:
    """Deferred per-selection receipt vocabulary.

    Runtime world publication currently records world-level retention receipts.
    This DTO remains available for transition-kernel spikes but is not yet an
    authoritative runtime record.
    """

    operation_id: str
    world_oid: str
    binding: str
    store_id: str
    resource_id: str
    selected_head: str
    selection_digest: str
    retained_refs: tuple[RetainedRef, ...]
    authority_ref: str | None = None

    def selection_retention_receipt_digest(self) -> str:
        return canonical_digest(self._digest_payload())

    def _digest_payload(self) -> dict[str, object]:
        return {
            "schema": SELECTION_RETENTION_RECEIPT_SCHEMA,
            "operation_id": self.operation_id,
            "world_oid": self.world_oid,
            "binding": self.binding,
            "store_id": self.store_id,
            "resource_id": self.resource_id,
            "selected_head": self.selected_head,
            "selection_digest": self.selection_digest,
            "retained_refs": _sorted_json(ref.to_json() for ref in self.retained_refs),
            "authority_ref": self.authority_ref,
        }

    def to_json(self) -> dict[str, object]:
        return {
            **self._digest_payload(),
            "selection_retention_receipt_digest": self.selection_retention_receipt_digest(),
        }


def is_candidate_backed_selection_kind(selection_kind: str) -> bool:
    return selection_kind in _CANDIDATE_BACKED_SELECTION_KINDS


def validate_head_selection(selection: HeadSelectionRecord, evidence: HeadSelectionEvidence) -> None:
    if evidence.binding != selection.binding:
        raise ValueError("selection evidence binding disagrees with head selection")
    if evidence.store_id != selection.store_id:
        raise ValueError("selection evidence store_id disagrees with head selection")
    if evidence.resource_id != selection.resource_id:
        raise ValueError("selection evidence resource_id disagrees with head selection")
    if evidence.selected_head != selection.selected_head:
        raise ValueError("selection evidence selected_head disagrees with head selection")
    if evidence.selection_digest != selection.selection_digest():
        raise ValueError("selection evidence digest disagrees with head selection")
    if _sorted_json(req.to_json() for req in evidence.retention_policy_requirements) != _sorted_json(
        req.to_json() for req in selection.retention_policy_requirements
    ):
        raise ValueError("selection evidence retention requirements disagree with head selection")
    candidate_fields = (
        evidence.revision_preparation_digest,
        evidence.candidate_commit_digest,
        evidence.candidate_ref,
    )
    carries_candidate_evidence = any(field is not None for field in candidate_fields)
    if is_candidate_backed_selection_kind(selection.selection_kind) and any(
        field is None for field in candidate_fields
    ):
        raise ValueError("candidate-backed selection requires revision preparation, commit, and ref evidence")
    if not is_candidate_backed_selection_kind(selection.selection_kind) and carries_candidate_evidence:
        raise ValueError("non-candidate selection must not carry candidate evidence")


def _sorted_json(items: Any) -> list[object]:
    return sorted(items, key=canonical_digest)


def _sorted_values(items: tuple[str, ...]) -> list[str]:
    return sorted(items)


def _require_schema(value: dict[str, object], expected: str, label: str) -> None:
    schema = value.get("schema")
    if schema != expected:
        raise ValueError(f"unsupported {label} schema: {schema!r}")


def _required_str(value: dict[str, object], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{key} is required")
    return raw


def _optional_str(value: dict[str, object], key: str) -> str | None:
    raw = value.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{key} must be a non-empty string when present")
    return raw


def _required_digest(value: dict[str, object], key: str) -> str:
    raw = _required_str(value, key)
    _validate_sha256_digest(raw, key)
    return raw


def _optional_digest(value: dict[str, object], key: str) -> str | None:
    raw = _optional_str(value, key)
    if raw is not None:
        _validate_sha256_digest(raw, key)
    return raw


def _required_list(value: dict[str, object], key: str) -> list[object]:
    raw = value.get(key)
    if not isinstance(raw, list):
        raise TypeError(f"{key} must be a list")
    return raw


def _required_str_tuple(value: dict[str, object], key: str) -> tuple[str, ...]:
    raw = _required_list(value, key)
    if not all(isinstance(item, str) and item for item in raw):
        raise ValueError(f"{key} must be a list of non-empty strings")
    return tuple(cast("list[str]", raw))


def _required_digest_tuple(value: dict[str, object], key: str) -> tuple[str, ...]:
    items = _required_str_tuple(value, key)
    for item in items:
        _validate_sha256_digest(item, key)
    return tuple(items)


def _require_digest_match(value: dict[str, object], key: str, expected: str) -> None:
    actual = _required_digest(value, key)
    if actual != expected:
        raise ValueError(f"{key} disagrees with canonical record")


def _reject_unexpected_keys(value: dict[str, object], expected: set[str], label: str) -> None:
    extra_keys = set(value) - expected
    if extra_keys:
        raise ValueError(f"unexpected {label} fields: {sorted(extra_keys)!r}")


def _validate_sha256_digest(value: str, field: str) -> None:
    prefix = "sha256:"
    hex_digest = value.removeprefix(prefix)
    if (
        not value.startswith(prefix)
        or len(hex_digest) != 64
        or any(char not in "0123456789abcdefABCDEF" for char in hex_digest)
    ):
        raise ValueError(f"{field} must be a sha256 digest")
