"""Canonical retained trace ABI types for the trace-first kernel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from .canonical import Containment, RecordMode

SchemaRef = str
RecordId = str
FactId = RecordId
TraceOwnerId = str
OwnerOrdinal = int
CutId = str
FrontierId = CutId
AppendIntentId = str
CommitReceipt = str
ContextId = str
CapabilityWitnessId = str
AppendLocalId = str
VisibilityProfile = Literal["shape_only", "payload", "full_internal"]
Visibility = VisibilityProfile
ModeFilter = Literal["declarations_only", "captures_only", "both"]
OperationKind = Literal["append", "read", "publish_cut", "materialize", "observe"]


@dataclass(frozen=True)
class RecordDraft:
    """Append input. Drafts are not retained records."""

    mode: RecordMode
    schema_ref: SchemaRef
    payload: dict[str, Any] = field(default_factory=dict)
    kind_label: str = ""
    append_local_id: AppendLocalId | None = None
    caused_by_fact_ids: tuple[FactId, ...] = ()
    caused_by_local_refs: tuple[AppendLocalId, ...] = ()

    @property
    def fact_kind(self) -> str:
        """Compatibility label for older schema-layer code."""
        return self.kind_label or self.schema_ref


@dataclass(frozen=True)
class RecordBody:
    """Retained schema-specific body."""

    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordEnvelope:
    """Stable semantic identity and causality for one record."""

    record_id: RecordId
    digest: str
    schema_ref: SchemaRef
    mode: RecordMode
    witness_ref: RecordId
    caused_by_record_ids: tuple[RecordId, ...] = ()

    @property
    def fact_id(self) -> RecordId:
        """Compatibility alias while Fact vocabulary remains."""
        return self.record_id

    @property
    def caused_by_fact_ids(self) -> tuple[RecordId, ...]:
        """Compatibility alias while Fact vocabulary remains."""
        return self.caused_by_record_ids


@dataclass(frozen=True)
class RecordView:
    """Path/read-view metadata outside semantic record identity."""

    trace_owner_id: TraceOwnerId
    owner_ordinal: OwnerOrdinal
    retained_context_ref: ContextId = ""
    kind_label: str = ""

    @property
    def fact_kind(self) -> str:
        """Compatibility alias while Fact vocabulary remains."""
        return self.kind_label


@dataclass(frozen=True)
class Record:
    """One retained record: envelope plus first-order body."""

    envelope: RecordEnvelope
    body: RecordBody
    view: RecordView | None = None

    @property
    def fact_kind(self) -> str:
        """Compatibility label for schema-layer projections."""
        if self.view is not None and self.view.kind_label:
            return self.view.kind_label
        return self.envelope.schema_ref

    @property
    def trace_owner_id(self) -> TraceOwnerId:
        if self.view is None:
            return ""
        return self.view.trace_owner_id

    @property
    def owner_ordinal(self) -> OwnerOrdinal:
        if self.view is None:
            return -1
        return self.view.owner_ordinal

    @property
    def retained_context_ref(self) -> ContextId:
        if self.view is None:
            return ""
        return self.view.retained_context_ref


@dataclass(frozen=True)
class RecordShape:
    """Visibility-filtered record shape without retained payload."""

    envelope: RecordEnvelope
    view: RecordView | None = None
    hidden_reason: str = "payload_hidden"

    @property
    def fact_kind(self) -> str:
        """Compatibility label for schema-layer projections."""
        if self.view is not None and self.view.kind_label:
            return self.view.kind_label
        return self.envelope.schema_ref


VisibleRecord = Record | RecordShape
FactDraft = RecordDraft
FactBody = RecordBody
FactEnvelope = RecordEnvelope
Fact = Record
FactShape = RecordShape
FactView = RecordView
VisibleFact = VisibleRecord


@dataclass(frozen=True)
class RetainedContext:
    """Durable semantic context stamped into retained fact envelopes."""

    context_id: ContextId
    active_binding_refs: tuple[str, ...] = ()
    capability_witness_refs: tuple[CapabilityWitnessId, ...] = ()
    semantic_environment_refs: tuple[str, ...] = ()
    visibility_policy_refs: tuple[str, ...] = ()
    substrate_ref: str = "sqlite.local.v1"
    containment: Containment = "contained"


@dataclass(frozen=True)
class RetainedContextDraft:
    """Append input for a retained context."""

    active_binding_refs: tuple[str, ...] = ()
    capability_witness_refs: tuple[CapabilityWitnessId, ...] = ()
    semantic_environment_refs: tuple[str, ...] = ()
    visibility_policy_refs: tuple[str, ...] = ()
    substrate_ref: str = "sqlite.local.v1"
    containment: Containment = "contained"
    reuse_context_id: ContextId | None = None


@dataclass(frozen=True)
class WitnessBody:
    """Witness body retained as a kernel witness record payload."""

    actor_ref: str
    authority_refs: tuple[str, ...] = ()
    active_binding_refs: tuple[str, ...] = ()
    semantic_environment_refs: tuple[str, ...] = ()
    visibility_policy_refs: tuple[str, ...] = ()
    provenance_policy_refs: tuple[str, ...] = ()
    substrate_ref: str = "sqlite.local.v1"
    containment: Containment = "contained"

    def to_payload(self) -> dict[str, Any]:
        return {
            "active_binding_refs": list(self.active_binding_refs),
            "actor_ref": self.actor_ref,
            "authority_refs": list(self.authority_refs),
            "containment": self.containment,
            "provenance_policy_refs": list(self.provenance_policy_refs),
            "semantic_environment_refs": list(self.semantic_environment_refs),
            "substrate_ref": self.substrate_ref,
            "visibility_policy_refs": list(self.visibility_policy_refs),
        }


@dataclass(frozen=True)
class WitnessDraft:
    """Append-time witness draft before retained record identity is computed."""

    body: WitnessBody


@dataclass(frozen=True)
class AppendGroup:
    """Owner-local group inside one semantic append transition."""

    trace_owner_id: TraceOwnerId
    retained_context: RetainedContextDraft | RetainedContext | ContextId | None = None
    causal_parents: tuple[FactId, ...] = ()
    fact_drafts: tuple[FactDraft, ...] = ()


@dataclass(frozen=True)
class AppendBatch:
    """Atomic semantic append attempt."""

    append_intent_id: AppendIntentId
    groups: tuple[AppendGroup, ...]
    atomicity: Literal["atomic"] = "atomic"


@dataclass(frozen=True)
class AppendReceipt:
    """Storage and trace identity allocated by a successful append."""

    append_intent_id: AppendIntentId
    fact_ids: tuple[FactId, ...]
    commit_receipts: tuple[CommitReceipt, ...]
    owner_ordinal_ranges: dict[TraceOwnerId, tuple[OwnerOrdinal, OwnerOrdinal]]
    causal_edges: tuple[tuple[FactId, FactId], ...]
    context_receipts: tuple[ContextId, ...] = ()


@dataclass(frozen=True)
class OperationContext:
    """Trace-facing operation context presented to kernel operations."""

    actor_ref: str
    operation: OperationKind
    presented_authority_refs: tuple[str, ...] = ()
    schema_environment_ref: str = "shepherd2-slice-a"
    visibility_profile: VisibilityProfile = "payload"
    trust_mode: str | None = None

    @property
    def presented_witness_refs(self) -> tuple[str, ...]:
        """Compatibility name for authority refs while Fact vocabulary remains."""
        return self.presented_authority_refs

    @property
    def schema_version_set(self) -> str:
        """Compatibility name for the active schema environment."""
        return self.schema_environment_ref


@dataclass(frozen=True)
class AppendContext:
    """Compatibility write context presented to append."""

    actor_ref: str
    presented_witness_refs: tuple[str, ...] = ()
    schema_version_set: str = "shepherd2-slice-a"
    trust_mode: str | None = None

    def to_operation_context(self, operation: OperationKind = "append") -> OperationContext:
        return OperationContext(
            actor_ref=self.actor_ref,
            operation=operation,
            presented_authority_refs=self.presented_witness_refs,
            schema_environment_ref=self.schema_version_set,
            trust_mode=self.trust_mode,
        )


@dataclass(frozen=True)
class ReadContext:
    """Compatibility read context presented to read operations."""

    actor_ref: str
    presented_witness_refs: tuple[str, ...] = ()
    visibility_profile: VisibilityProfile = "payload"

    def to_operation_context(self) -> OperationContext:
        return OperationContext(
            actor_ref=self.actor_ref,
            operation="read",
            presented_authority_refs=self.presented_witness_refs,
            visibility_profile=self.visibility_profile,
        )


TRUSTED_APPEND_CONTEXT = AppendContext(
    actor_ref="runtime:internal",
    presented_witness_refs=("trusted:internal",),
    trust_mode="internal",
)
TRUSTED_READ_CONTEXT = ReadContext(
    actor_ref="runtime:internal",
    presented_witness_refs=("trusted:internal",),
    visibility_profile="payload",
)


ClosurePolicy = Literal["visible_only", "include_external_anchors"]


@dataclass(frozen=True)
class PathPrefix:
    """Owner path prefix selected through a retained record position."""

    target_trace_owner_id: TraceOwnerId
    through_record_id: RecordId
    through_owner_ordinal: OwnerOrdinal


@dataclass(frozen=True)
class CausalClosure:
    """Causal closure selected from one or more root records."""

    root_record_ids: tuple[RecordId, ...]
    closure_policy: ClosurePolicy = "include_external_anchors"


@dataclass(frozen=True)
class CutSelector:
    """Immutable read selector captured by a published cut."""

    path_prefix: PathPrefix | None = None
    causal_closure: CausalClosure | None = None


@dataclass(frozen=True)
class Cut:
    """Ring 0 owner-prefix cut."""

    frontier_id: FrontierId
    target_trace_owner_id: TraceOwnerId
    through_fact_id: FactId
    through_owner_ordinal: OwnerOrdinal
    publisher_trace_owner_id: TraceOwnerId | None = None
    created_by_fact_id: FactId | None = None


@dataclass(frozen=True)
class CutSpec:
    """Append input for publishing an owner-prefix cut."""

    frontier_id: FrontierId
    target_trace_owner_id: TraceOwnerId
    through_fact_id: FactId
    publisher_trace_owner_id: TraceOwnerId | None = None
    append_intent_id: AppendIntentId | None = None
    caused_by: tuple[FactId, ...] = ()


OwnerCutoff = Cut
OwnerCutoffSpec = CutSpec


@dataclass(frozen=True)
class ExternalAnchor:
    """Visible reference to a fact outside a slice or hidden by visibility."""

    ref: FactId
    anchor_kind: str = "fact"
    visible_shape: dict[str, Any] = field(default_factory=dict)
    hidden_reason: str = "outside_frontier"


@dataclass(frozen=True)
class ContextAnchor:
    """Visible reference to a retained context hidden by visibility."""

    context_id: ContextId
    visible_shape: dict[str, Any] = field(default_factory=dict)
    hidden_reason: str = "hidden_by_visibility"


@dataclass(frozen=True)
class WitnessAnchor:
    """Visible reference to a witness record hidden by visibility."""

    witness_ref: RecordId
    visible_shape: dict[str, Any] = field(default_factory=dict)
    hidden_reason: str = "hidden_by_visibility"


@dataclass(frozen=True)
class TraceSlice:
    """Graph-shaped read result over retained trace facts."""

    frontier: Cut | None
    visibility_profile: VisibilityProfile
    mode_filter: ModeFilter
    facts_by_id: dict[FactId, VisibleFact]
    contexts_by_id: dict[ContextId, RetainedContext]
    owner_paths: dict[TraceOwnerId, tuple[FactId, ...]]
    causal_edges: tuple[tuple[FactId, FactId], ...]
    external_anchors: tuple[ExternalAnchor, ...] = ()
    context_anchors: tuple[ContextAnchor, ...] = ()
    visible_witnesses_by_id: dict[RecordId, VisibleRecord] = field(default_factory=dict)
    witness_anchors: tuple[WitnessAnchor, ...] = ()

    @property
    def visible_facts_by_id(self) -> dict[FactId, VisibleFact]:
        """Return the visibility-filtered fact map using Slice A vocabulary."""
        return self.facts_by_id

    def fact_ids(self) -> tuple[FactId, ...]:
        return tuple(fact_id for fact_ids in self.owner_paths.values() for fact_id in fact_ids)


class TraceStore(Protocol):
    """Internal TraceStore port for the substrate slice."""

    def append(self, append_context: AppendContext, batch: AppendBatch) -> AppendReceipt: ...

    def preview_record_ids(self, append_context: AppendContext, batch: AppendBatch) -> tuple[FactId, ...]: ...

    def preview_fact_ids(self, append_context: AppendContext, batch: AppendBatch) -> tuple[FactId, ...]: ...

    def read_fact(
        self,
        read_context: ReadContext,
        fact_id: FactId,
    ) -> VisibleFact | ExternalAnchor: ...

    def read_owner_prefix(
        self,
        read_context: ReadContext,
        trace_owner_id: TraceOwnerId,
        through: OwnerOrdinal,
        mode_filter: ModeFilter = "both",
    ) -> TraceSlice: ...

    def read_path_prefix(
        self,
        read_context: ReadContext,
        trace_owner_id: TraceOwnerId,
        through: OwnerOrdinal,
        mode_filter: ModeFilter = "both",
    ) -> TraceSlice: ...

    def publish_cut(self, append_context: AppendContext, spec: CutSpec) -> Cut: ...

    def publish_frontier(self, append_context: AppendContext, spec: OwnerCutoffSpec) -> OwnerCutoff: ...

    def resolve_cut(
        self,
        read_context: ReadContext | OperationContext,
        cut_id: CutId,
        visibility: VisibilityProfile | None = None,
        mode_filter: ModeFilter = "both",
    ) -> TraceSlice: ...

    def resolve_frontier(
        self,
        read_context: ReadContext | OperationContext,
        frontier_id: FrontierId,
        visibility: VisibilityProfile | None = None,
        mode_filter: ModeFilter = "both",
    ) -> TraceSlice: ...

    def read_owner_cutoff(self, frontier_id: FrontierId) -> OwnerCutoff: ...

    def read_causal_closure(
        self,
        read_context: ReadContext,
        roots: tuple[FactId, ...],
        *,
        visibility: VisibilityProfile | None = None,
        mode_filter: ModeFilter = "both",
        closure_policy: ClosurePolicy = "include_external_anchors",
    ) -> TraceSlice: ...

    def close(self) -> None: ...
