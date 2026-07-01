"""Narrow substrate helpers and drivers for the v2 world storage manager."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, assert_never

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._overlay_capture_adapter import (
    OVERLAY_ADAPTER_ID,
    OVERLAY_ADAPTER_VERSION,
    OVERLAY_EVIDENCE_KINDS,
    OVERLAY_MECHANISM,
    OverlayCaptureAdapter,
)
from vcs_core._substrate_driver import (
    ActiveSurface,
    BaseSubstrateDriver,
    CapabilitySet,
    CaptureAdapter,
    CaptureAdapterSchema,
    CaptureRequest,
    ChildWorldSnapshot,
    CommandRequest,
    CommandSpec,
    DriverContext,
    DriverIngressResult,
    DriverSchema,
    IngressRequest,
    MergeRequest,
    MergeSpec,
    ObservationDraft,
    ParamSpec,
    ReduceRequest,
    ScanRequest,
    ScanSpec,
    TransitionDraft,
    UnsupportedRequestError,
)
from vcs_core._transition_kernel_records import PayloadDescriptorClaim
from vcs_core._world_types import WORLD_REF_SUBSTRATE_KIND, WorldRefPayload, canonical_digest

if TYPE_CHECKING:
    from collections.abc import Callable

    import pygit2

    from vcs_core._substrate_driver import ReductionBatch
    from vcs_core._transition_kernel_records import RelationshipRequirement, RetentionPolicyRequirement
    from vcs_core._world_operation_builder import CandidateSelectionPlan
    from vcs_core._world_storage_manager import PreparedCandidateBundle, WorldStorageManager
    from vcs_core._world_transition_coordinator import CoordinatorEvidenceOnlyIngress
    from vcs_core._world_types import SubstrateHead, WorldCommit

WORKSPACE_REVISION_SCHEMA = "vcscore/workspace-revision/v1"
WORKSPACE_STATE_MANIFEST_SCHEMA = "vcscore/workspace-state-manifest/v1"
SESSION_STATE_REVISION_SCHEMA = "vcscore/session-state-revision/v1"
TRACE_REVISION_SCHEMA = "vcscore/trace-revision/v1"
TRACE_SUBSTRATE_KIND = "shepherd.trace"
TRACE_ROLE = "shepherd.TraceState"
WORLD_REF_ROLE = "vcscore.WorldRef"
WORKSPACE_MANIFEST_BYTE_AUTHORITY_MODES = frozenset({"digest-only", "tree-backed"})
WORKSPACE_MANIFEST_FILE_MODES = frozenset({0o100644, 0o100755})


@dataclass(frozen=True)
class WorkspaceSubstrateDriver:
    """JSON workspace-ref state driver without filesystem capture.

    Deliberately standalone: this is the original T1 driver and predates
    the ``BaseSubstrateDriver`` mixin (introduced in Phase A.1). The three
    drivers migrated in T3 — session, trace, world-ref — inherit the mixin;
    this one is intentionally *not* retrofitted and keeps its explicit
    ``validate_result`` / ``capture_adapters`` implementations. It is
    retained as the no-mixin reference driver: one driver that spells out
    the full ``SubstrateDriver`` Protocol surface with no inherited
    defaults. The accepted cost is that a future ``BaseSubstrateDriver``
    default hook will not reach this driver automatically — it must opt in
    deliberately. (SPI v0.1 deliberate note; see EXECPLAN Decision Log
    2026-05-25.)
    """

    store_id: str = "store_workspace"
    binding: str = "workspace"
    role: str = "shepherd.WorkspaceRef"
    driver_id: str = "shepherd.workspace_ref"
    driver_version: str = "v1"
    materialization_class: str = "external"

    @property
    def capabilities(self) -> CapabilitySet:
        # T2c re-added ``ReduceRequest`` to ``accepts`` in the same commit
        # that wired the typed reduce handler (per SPI v0.1 §Result Shape
        # "Capabilities are a runtime contract"). The handler delegates to
        # the same ``_workspace_capture_reduction_from_evidence_ingress_result``
        # logic the legacy ``capture-reduction-from-evidence`` command uses,
        # accepting the caller-computed reduction payload/proof via
        # ``ReduceRequest.reduction_payload`` / ``reduction_proof`` fields.
        return CapabilitySet(
            accepts=frozenset(
                {
                    CommandRequest,
                    ScanRequest,
                    CaptureRequest,
                    ReduceRequest,
                    MergeRequest,
                }
            ),
            selectable=True,
            materializable=True,
        )

    def describe(self) -> DriverSchema:
        return DriverSchema(
            driver_id=self.driver_id,
            driver_version=self.driver_version,
            capabilities=self.capabilities,
            commands={
                "bootstrap": CommandSpec(
                    description="Create the initial bootstrap workspace revision.",
                    params={"payload": ParamSpec(type="object", description="canonical revision payload")},
                ),
                "import": CommandSpec(
                    description="Import an external workspace revision.",
                    params={"payload": ParamSpec(type="object", description="canonical revision payload")},
                ),
                "create-candidate": CommandSpec(
                    description="Create a JSON-backed workspace candidate revision.",
                    params={
                        "payload": ParamSpec(type="object", description="canonical revision payload"),
                        "git_tree_oid": ParamSpec(
                            type="str",
                            required=False,
                            description="optional Git tree oid for tree-backed mode",
                        ),
                    },
                ),
            },
            scans={
                "workspace-scan": ScanSpec(
                    description="Scan-classified workspace transition (drift detector).",
                    params={"payload": ParamSpec(type="object")},
                ),
                "workspace-adoption": ScanSpec(
                    description="Adopt baseline workspace state.",
                    params={"payload": ParamSpec(type="object")},
                ),
            },
            merges={
                "workspace-overlay-merge": MergeSpec(
                    description="Coordinated overlay merge into the workspace.",
                    params={
                        "payload": ParamSpec(type="object"),
                        "other_head": ParamSpec(type="str"),
                    },
                ),
            },
            capture_adapters=(
                CaptureAdapterSchema(
                    adapter_id=OVERLAY_ADAPTER_ID,
                    adapter_version=OVERLAY_ADAPTER_VERSION,
                    mechanism=OVERLAY_MECHANISM,
                    evidence_kinds=OVERLAY_EVIDENCE_KINDS,
                ),
            ),
        )

    def prepare(
        self,
        context: DriverContext,
        request: IngressRequest,
    ) -> DriverIngressResult:
        """Typed dispatch over IngressRequest variants (SPI v0.1 §Q1)."""
        match request:
            case CommandRequest(command="bootstrap", params=params):
                return self._prepare_json_state(context, "bootstrap", params, ingress_kind=request.ingress_kind)
            case CommandRequest(command="import", params=params):
                return self._prepare_json_state(context, "import", params, ingress_kind=request.ingress_kind)
            case CommandRequest(command="create-candidate", params=params):
                return self._prepare_json_state(
                    context,
                    "workspace-json-revision",
                    params,
                    ingress_kind=request.ingress_kind,
                )
            case CommandRequest(command=other_cmd):
                raise ValueError(f"unsupported workspace command: {other_cmd!r}")
            case ScanRequest(scan_kind="workspace-scan", external_state=state):
                return self._prepare_json_state(context, "workspace-scan", state, ingress_kind=request.ingress_kind)
            case ScanRequest(scan_kind="workspace-adoption", external_state=state):
                return self._prepare_json_state(
                    context,
                    "workspace-adoption",
                    state,
                    ingress_kind=request.ingress_kind,
                )
            case ScanRequest(scan_kind=other_scan):
                raise ValueError(f"unsupported workspace scan kind: {other_scan!r}")
            case MergeRequest(other_head=other_head, policy=policy):
                merged_params: dict[str, Any] = dict(policy)
                merged_params.setdefault("other_head", other_head)
                return self._prepare_json_state(
                    context,
                    "workspace-overlay-merge",
                    merged_params,
                    ingress_kind=request.ingress_kind,
                )
            case CaptureRequest(adapter_id=adapter_id, observations=observations):
                if adapter_id != OVERLAY_ADAPTER_ID:
                    raise ValueError(
                        f"workspace driver only accepts CaptureRequest from {OVERLAY_ADAPTER_ID!r}, not {adapter_id!r}"
                    )
                # CaptureRequest is the evidence-persistence stage; per Q1 §Result Shape,
                # the driver returns observations only. The coordinator persists them
                # as evidence and a downstream ReduceRequest produces transitions.
                return DriverIngressResult(observations=tuple(observations))
            case ReduceRequest(
                evidence_citations=batch,
                reduction_payload=payload,
                reduction_proof=proof,
            ):
                # T2c: typed reduce handler for the Python-tier capture
                # flow. Mirrors the legacy ``capture-reduction-from-evidence``
                # command's data shape — caller pre-computes the reduction
                # state manifest and supplies it via the typed fields
                # because v0.1 DriverContext doesn't carry a coordinator-
                # supplied evidence resolver. v0.2 may revisit.
                if payload is None or proof is None:
                    raise ValueError(
                        "workspace driver ReduceRequest requires "
                        "reduction_payload and reduction_proof in v0.1; "
                        "context-supplied evidence resolver lands in v0.2"
                    )
                if not batch.citations:
                    raise ValueError(
                        "workspace driver ReduceRequest requires at least one EvidenceCitation in evidence_citations"
                    )
                producer_ids = {c.producer_operation_id for c in batch.citations}
                if len(producer_ids) != 1:
                    raise ValueError(
                        "workspace driver ReduceRequest requires all "
                        "EvidenceCitations to share one producer_operation_id; "
                        f"got {sorted(producer_ids)!r}"
                    )
                command_operation_id = batch.citations[0].producer_operation_id
                evidence_citation_ids = tuple(citation.citation_id for citation in batch.citations)
                git_tree_oid = _optional_str(payload, "git_tree_oid")
                return _workspace_capture_reduction_from_evidence_ingress_result(
                    context=context,
                    driver_id=self.driver_id,
                    payload=dict(payload),
                    materialization_class=self.materialization_class,
                    command_operation_id=command_operation_id,
                    evidence_citation_ids=evidence_citation_ids,
                    reduced_state_proof=proof,
                    git_tree_oid=git_tree_oid,
                )
            case _:
                # SPI v0.1 §Q1 exhaustiveness discipline: every driver's
                # match over ``IngressRequest`` ends with ``assert_never``
                # so a new variant in v0.2 (e.g., ``ReplayRequest``) fires
                # one mypy error per under-implementing driver.
                assert_never(request)

    def capture_adapters(
        self,
        context: DriverContext,
    ) -> tuple[CaptureAdapter, ...]:
        del context
        return (OverlayCaptureAdapter(),)

    def validate_result(
        self,
        request: IngressRequest,
        result: DriverIngressResult,
    ) -> None:
        # No domain-specific invariants beyond validator layers 1-2 in T1c.
        return None

    def _prepare_json_state(
        self,
        context: DriverContext,
        semantic_op: str,
        params: Mapping[str, Any],
        *,
        ingress_kind: str,
    ) -> DriverIngressResult:
        payload = _revision_payload(WORKSPACE_REVISION_SCHEMA, _payload_param(params))
        return _json_state_ingress_result(
            context=context,
            driver_id=self.driver_id,
            semantic_op=semantic_op,
            payload=payload,
            materialization_class=self.materialization_class,
            git_tree_oid=_optional_str(params, "git_tree_oid"),
            ingress_kind=ingress_kind,
        )


@dataclass(frozen=True)
class WorkspaceSubstrateAdapter:
    """Role-aware helper for workspace substrate revisions."""

    manager: WorldStorageManager
    driver: WorkspaceSubstrateDriver = field(default_factory=WorkspaceSubstrateDriver)

    def create_bootstrap_revision(
        self,
        ref: str,
        payload: dict[str, Any],
        *,
        operation_id: str,
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
    ) -> str:
        return self._create_revision_with_command(
            "bootstrap",
            ref,
            payload,
            operation_id=operation_id,
            parents=parents,
            message=message,
        )

    def create_import_revision(
        self,
        ref: str,
        payload: dict[str, Any],
        *,
        operation_id: str,
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
    ) -> str:
        return self._create_revision_with_command(
            "import",
            ref,
            payload,
            operation_id=operation_id,
            parents=parents,
            message=message,
        )

    def _create_revision_with_command(
        self,
        command: str,
        ref: str,
        payload: dict[str, Any],
        *,
        operation_id: str,
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
    ) -> str:
        return self.manager.create_prepared_driver_revision_bundle(
            self.driver.store_id,
            ref,
            operation_id=operation_id,
            binding=self.driver.binding,
            result=self.driver.prepare(
                self._context(operation_id=operation_id, parents=parents),
                CommandRequest(command=command, params={"payload": payload}),
            ),
            driver_id=self.driver.driver_id,
            driver_version=self.driver.driver_version,
            parents=parents,
            message=message,
        ).head

    def create_candidate(
        self,
        *,
        operation_id: str,
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
    ) -> PreparedCandidateBundle:
        return self.manager.create_prepared_driver_candidate_bundle(
            self.driver.store_id,
            operation_id=operation_id,
            binding=self.driver.binding,
            result=self.driver.prepare(
                self._context(operation_id=operation_id, parents=parents),
                CommandRequest(command="create-candidate", params={"payload": payload}),
            ),
            driver_id=self.driver.driver_id,
            driver_version=self.driver.driver_version,
            parents=parents,
            message=message,
        )

    def create_scan_candidate(
        self,
        *,
        operation_id: str,
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
        workspace_tree_oid: str | None = None,
    ) -> PreparedCandidateBundle:
        return self._create_manifest_candidate_command(
            "scan",
            operation_id=operation_id,
            payload=payload,
            parents=parents,
            message=message,
            workspace_tree_oid=workspace_tree_oid,
        )

    def create_adoption_candidate(
        self,
        *,
        operation_id: str,
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
        workspace_tree_oid: str | None = None,
    ) -> PreparedCandidateBundle:
        return self._create_manifest_candidate_command(
            "adopt-baseline",
            operation_id=operation_id,
            payload=payload,
            parents=parents,
            message=message,
            workspace_tree_oid=workspace_tree_oid,
        )

    def create_overlay_merge_candidate(
        self,
        *,
        operation_id: str,
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
        workspace_tree_oid: str | None = None,
    ) -> PreparedCandidateBundle:
        return self._create_manifest_candidate_command(
            "overlay-merge",
            operation_id=operation_id,
            payload=payload,
            parents=parents,
            message=message,
            workspace_tree_oid=workspace_tree_oid,
        )

    def _create_manifest_candidate_command(
        self,
        command: str,
        *,
        operation_id: str,
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...],
        message: str | None,
        workspace_tree_oid: str | None = None,
    ) -> PreparedCandidateBundle:
        command_params: dict[str, Any] = {"payload": payload}
        if workspace_tree_oid is not None:
            command_params["git_tree_oid"] = workspace_tree_oid
        # T3-callers: dispatch the legacy ``command`` string into a typed
        # request. "scan" and "adopt-baseline" are scan ingress (drift
        # detectors); "overlay-merge" is a merge mechanism (workspace-specific
        # for now; ``other_head`` is informational and not populated by current
        # call sites — v0.2 may revisit when overlay merge tracks multi-head
        # provenance).
        request: IngressRequest
        if command == "scan":
            request = ScanRequest(scan_kind="workspace-scan", external_state=command_params)
        elif command == "adopt-baseline":
            request = ScanRequest(scan_kind="workspace-adoption", external_state=command_params)
        elif command == "overlay-merge":
            request = MergeRequest(other_head="", policy=command_params)
        else:
            raise ValueError(f"unsupported manifest candidate command: {command!r}")
        return self.manager.create_prepared_driver_candidate_bundle(
            self.driver.store_id,
            operation_id=operation_id,
            binding=self.driver.binding,
            result=self.driver.prepare(
                self._context(operation_id=operation_id, parents=parents),
                request,
            ),
            driver_id=self.driver.driver_id,
            driver_version=self.driver.driver_version,
            parents=parents,
            ingress_kind=request.ingress_kind,
            message=message,
        )

    def persist_capture_history_evidence(
        self,
        *,
        command_operation_id: str,
        capture_events: tuple[object, ...],
        envelope_id: str = "capture-events",
    ) -> CoordinatorEvidenceOnlyIngress:
        """Persist command-owned raw capture history as evidence-only driver ingress.

        T3-callers: migrated from ``prepare_command("capture-evidence", ...)``
        to a direct call into the underlying ``_workspace_capture_evidence_ingress_result``
        helper. The capture-evidence path is workspace-driver-specific
        (the observations carry workspace-binding fields the generic
        ``OverlayCaptureAdapter`` doesn't add), so it stays bound to the
        helper rather than routing through the typed ``CaptureRequest``
        variant. The legacy ``prepare_command`` Protocol method removal
        in T3-final eliminates the indirection that previously routed
        this through the driver's string dispatch.
        """
        if not capture_events:
            raise ValueError("capture_events must be a non-empty sequence")
        ctx = self._context(operation_id=command_operation_id, parents=())
        return self.manager.persist_driver_evidence_only(
            self.driver.store_id,
            operation_id=command_operation_id,
            binding=self.driver.binding,
            result=_workspace_capture_evidence_ingress_result(
                context=ctx,
                driver_id=self.driver.driver_id,
                command_operation_id=command_operation_id,
                capture_events=capture_events,
            ),
            ingress_kind="capture",
            driver_id=self.driver.driver_id,
            driver_version=self.driver.driver_version,
            envelope_id=envelope_id,
        )

    def create_capture_reduction_candidate_from_evidence(
        self,
        *,
        operation_id: str,
        command_operation_id: str,
        payload: dict[str, Any],
        reduction_batch: ReductionBatch,
        reduced_state_proof: Mapping[str, object],
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
        workspace_tree_oid: str | None = None,
    ) -> PreparedCandidateBundle:
        # T3-callers: migrated to typed ReduceRequest. Mirrors the runtime
        # rewire pattern from _python_runtime_capture_candidate (T2c-iii):
        # caller-computed reduction_payload + reduction_proof, citations
        # from the ReductionBatch.
        reduction_payload = dict(payload)
        if workspace_tree_oid is not None:
            reduction_payload["git_tree_oid"] = workspace_tree_oid
        request = ReduceRequest(
            evidence_citations=reduction_batch,
            reduction_payload=reduction_payload,
            reduction_proof=reduced_state_proof,
        )
        return self.manager.create_prepared_driver_candidate_bundle(
            self.driver.store_id,
            operation_id=operation_id,
            binding=self.driver.binding,
            result=self.driver.prepare(
                self._context(operation_id=operation_id, parents=parents),
                request,
            ),
            driver_id=self.driver.driver_id,
            driver_version=self.driver.driver_version,
            parents=parents,
            ingress_kind="reduce",
            reduction_batch=reduction_batch,
            message=message,
        )

    def plan_candidate_selection(
        self,
        bundle: PreparedCandidateBundle,
        *,
        operation_id: str | None = None,
        selection_kind: Literal["new-candidate", "child-produced"] | None = None,
        producer_operation_id: str | None = None,
        producer_world_oid: str | None = None,
        relationship_requirements: tuple[RelationshipRequirement, ...] = (),
        retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = (),
        selection_policy_digest: str | None = None,
    ) -> CandidateSelectionPlan:
        return _plan_candidate_selection(
            self.manager,
            self.driver.role,
            bundle,
            operation_id=operation_id,
            selection_kind=selection_kind,
            producer_operation_id=producer_operation_id,
            producer_world_oid=producer_world_oid,
            relationship_requirements=relationship_requirements,
            retention_policy_requirements=retention_policy_requirements,
            selection_policy_digest=selection_policy_digest,
        )

    def head(self, oid: str) -> SubstrateHead:
        return self.manager.substrate_head(
            self.driver.store_id,
            binding=self.driver.binding,
            head=oid,
            role=self.driver.role,
        )

    def _context(
        self,
        *,
        operation_id: str,
        parents: tuple[str | pygit2.Oid, ...],
        active_surface: ActiveSurface | None = None,
    ) -> DriverContext:
        return DriverContext(
            operation_id=operation_id,
            binding=self.driver.binding,
            role=self.driver.role,
            store_identity=self.manager.store(self.driver.store_id).identity,
            base_heads=tuple(str(parent) for parent in parents),
            active_surface=active_surface,
        )


@dataclass(frozen=True)
class SessionStateSubstrateDriver(BaseSubstrateDriver):
    """Provider-neutral session-state JSON substrate driver."""

    store_id: str = "store_session"
    binding: str = "session"
    role: str = "shepherd.SessionState"
    driver_id: str = "shepherd.session_state"
    driver_version: str = "v1"
    materialization_class: str = "external"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            accepts=frozenset({CommandRequest}),
            selectable=True,
        )

    def describe(self) -> DriverSchema:
        return DriverSchema(
            driver_id=self.driver_id,
            driver_version=self.driver_version,
            capabilities=self.capabilities,
            commands={
                "checkpoint": CommandSpec(
                    description="Create a session-state checkpoint revision.",
                    params={"payload": ParamSpec(type="object", description="canonical revision payload")},
                ),
                "create-candidate": CommandSpec(
                    description="Create a JSON-backed session-state candidate revision.",
                    params={"payload": ParamSpec(type="object", description="canonical revision payload")},
                ),
            },
        )

    def prepare(
        self,
        context: DriverContext,
        request: IngressRequest,
    ) -> DriverIngressResult:
        """Typed dispatch over IngressRequest variants (SPI v0.1 §Q1)."""
        match request:
            case CommandRequest(command="checkpoint", params=params):
                return self._prepare_state(context, "checkpoint", params, ingress_kind=request.ingress_kind)
            case CommandRequest(command="create-candidate", params=params):
                return self._prepare_state(
                    context,
                    "session-state-json-revision",
                    params,
                    ingress_kind=request.ingress_kind,
                )
            case CommandRequest(command=other):
                raise ValueError(f"unsupported session-state command: {other!r}")
            case ScanRequest() | CaptureRequest() | ReduceRequest() | MergeRequest():
                # Capabilities declare ``accepts=frozenset({CommandRequest})``;
                # the coordinator pre-flight rejects non-accepted request
                # types via ``UnsupportedRequestError`` before reaching here.
                # This arm narrows the union so the wildcard ``case _``
                # below is reserved for v0.2 variants (e.g., ``ReplayRequest``).
                raise UnsupportedRequestError(driver_id=self.driver_id, request_type=type(request))
            case _:
                # SPI v0.1 §Q1 exhaustiveness discipline: every driver's
                # match over ``IngressRequest`` ends with ``assert_never``
                # so a new variant in v0.2 (e.g., ``ReplayRequest``) fires
                # one mypy error per under-implementing driver.
                assert_never(request)

    def _prepare_state(
        self,
        context: DriverContext,
        semantic_op: str,
        params: Mapping[str, Any],
        *,
        ingress_kind: str,
    ) -> DriverIngressResult:
        payload = _revision_payload(SESSION_STATE_REVISION_SCHEMA, _payload_param(params))
        return _json_state_ingress_result(
            context=context,
            driver_id=self.driver_id,
            semantic_op=semantic_op,
            payload=payload,
            materialization_class=self.materialization_class,
            ingress_kind=ingress_kind,
        )


@dataclass(frozen=True)
class RoleSubstrateAdapter:
    """Manager-bound publishing helper shared by command-only state substrates.

    Generic over the driver plus the two command names a state substrate uses
    (the *revision* command and the *candidate* command). Role-specific adapters
    subclass this, pin the driver via ``field(default_factory=...)``, set the
    command names, and add ergonomic per-role sugar that delegates to
    ``build_revision`` / ``build_candidate``. ``_context`` and ``head`` are
    overridable for substrates that diverge (e.g. the world-ref adapter injects
    a child-world resolver and asserts a store kind).

    This is the in-tree adapter pattern the substrate guide's Step 4 teaches; it
    stays internal because it wraps the private ``WorldStorageManager`` (an
    out-of-tree state substrate cannot reach the manager through public surface).
    """

    manager: WorldStorageManager
    driver: BaseSubstrateDriver
    revision_command: str = "checkpoint"
    candidate_command: str = "create-candidate"

    def build_revision(
        self,
        ref: str,
        *,
        operation_id: str,
        params: Mapping[str, Any],
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
    ) -> str:
        """Prepare + persist a revision under ``revision_command``; return its head."""
        return self.manager.create_prepared_driver_revision_bundle(
            self.driver.store_id,
            ref,
            operation_id=operation_id,
            binding=self.driver.binding,
            result=self.driver.prepare(
                self._context(operation_id=operation_id, parents=parents),
                CommandRequest(command=self.revision_command, params=dict(params)),
            ),
            driver_id=self.driver.driver_id,
            driver_version=self.driver.driver_version,
            parents=parents,
            message=message,
        ).head

    def build_candidate(
        self,
        *,
        operation_id: str,
        params: Mapping[str, Any],
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
    ) -> PreparedCandidateBundle:
        """Prepare a candidate revision under ``candidate_command``."""
        return self.manager.create_prepared_driver_candidate_bundle(
            self.driver.store_id,
            operation_id=operation_id,
            binding=self.driver.binding,
            result=self.driver.prepare(
                self._context(operation_id=operation_id, parents=parents),
                CommandRequest(command=self.candidate_command, params=dict(params)),
            ),
            driver_id=self.driver.driver_id,
            driver_version=self.driver.driver_version,
            parents=parents,
            message=message,
        )

    def plan_candidate_selection(
        self,
        bundle: PreparedCandidateBundle,
        *,
        operation_id: str | None = None,
        selection_kind: Literal["new-candidate", "child-produced"] | None = None,
        producer_operation_id: str | None = None,
        producer_world_oid: str | None = None,
        relationship_requirements: tuple[RelationshipRequirement, ...] = (),
        retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = (),
        selection_policy_digest: str | None = None,
    ) -> CandidateSelectionPlan:
        return _plan_candidate_selection(
            self.manager,
            self.driver.role,
            bundle,
            operation_id=operation_id,
            selection_kind=selection_kind,
            producer_operation_id=producer_operation_id,
            producer_world_oid=producer_world_oid,
            relationship_requirements=relationship_requirements,
            retention_policy_requirements=retention_policy_requirements,
            selection_policy_digest=selection_policy_digest,
        )

    def head(self, oid: str) -> SubstrateHead:
        return self.manager.substrate_head(
            self.driver.store_id,
            binding=self.driver.binding,
            head=oid,
            role=self.driver.role,
        )

    def _context(self, *, operation_id: str, parents: tuple[str | pygit2.Oid, ...]) -> DriverContext:
        return DriverContext(
            operation_id=operation_id,
            binding=self.driver.binding,
            role=self.driver.role,
            store_identity=self.manager.store(self.driver.store_id).identity,
            base_heads=tuple(str(parent) for parent in parents),
        )


@dataclass(frozen=True)
class _PayloadStateAdapter(RoleSubstrateAdapter):
    """Shared sugar for JSON-payload state substrates (session, trace).

    Both expose a ``checkpoint`` revision and a candidate, each keyed on a
    ``payload`` dict; the leaves differ only in the driver and (for trace) the
    candidate command name.
    """

    def create_checkpoint(
        self,
        ref: str,
        payload: dict[str, Any],
        *,
        operation_id: str,
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
    ) -> str:
        return self.build_revision(
            ref,
            operation_id=operation_id,
            params={"payload": payload},
            parents=parents,
            message=message,
        )

    def create_candidate(
        self,
        *,
        operation_id: str,
        payload: dict[str, Any],
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
    ) -> PreparedCandidateBundle:
        return self.build_candidate(
            operation_id=operation_id,
            params={"payload": payload},
            parents=parents,
            message=message,
        )


@dataclass(frozen=True)
class SessionStateSubstrateAdapter(_PayloadStateAdapter):
    """Role-aware helper for session-state substrate revisions."""

    driver: BaseSubstrateDriver = field(default_factory=SessionStateSubstrateDriver)


@dataclass(frozen=True)
class TaskTraceSubstrateDriver(BaseSubstrateDriver):
    """Provider-neutral Shepherd task trace JSON substrate driver."""

    store_id: str = "store_trace"
    binding: str = "trace"
    role: str = TRACE_ROLE
    driver_id: str = "shepherd.task_trace"
    driver_version: str = "v1"
    materialization_class: str = "external"
    # B4b slice 2: a trace is durable observation history — discard-means-archive
    # falls out of evidence-class, not state-class (trace-substrate-hybrid.md rec 3).
    lifecycle_class: str = "evidence"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            accepts=frozenset({CommandRequest}),
            selectable=True,
        )

    def describe(self) -> DriverSchema:
        return DriverSchema(
            driver_id=self.driver_id,
            driver_version=self.driver_version,
            capabilities=self.capabilities,
            commands={
                "checkpoint": CommandSpec(
                    description="Create a task-trace checkpoint revision.",
                    params={"payload": ParamSpec(type="object", description="canonical trace revision payload")},
                ),
                "append": CommandSpec(
                    description="Append a task-trace revision.",
                    params={"payload": ParamSpec(type="object", description="canonical trace revision payload")},
                ),
            },
        )

    def prepare(
        self,
        context: DriverContext,
        request: IngressRequest,
    ) -> DriverIngressResult:
        """Typed dispatch over IngressRequest variants (SPI v0.1 §Q1)."""
        match request:
            case CommandRequest(command="checkpoint", params=params):
                return self._prepare_state(context, "checkpoint", params, ingress_kind=request.ingress_kind)
            case CommandRequest(command="append", params=params):
                return self._prepare_state(context, "task-trace-append", params, ingress_kind=request.ingress_kind)
            case CommandRequest(command=other_cmd):
                raise ValueError(f"unsupported task-trace command: {other_cmd!r}")
            case ScanRequest() | CaptureRequest() | ReduceRequest() | MergeRequest():
                # Capabilities declare ``accepts=frozenset({CommandRequest})``;
                # the coordinator pre-flight rejects non-accepted request
                # types via ``UnsupportedRequestError`` before reaching here.
                # This arm narrows the union so the wildcard ``case _``
                # below is reserved for v0.2 variants (e.g., ``ReplayRequest``).
                raise UnsupportedRequestError(driver_id=self.driver_id, request_type=type(request))
            case _:
                # SPI v0.1 §Q1 exhaustiveness discipline: every driver's
                # match over ``IngressRequest`` ends with ``assert_never``
                # so a new variant in v0.2 (e.g., ``ReplayRequest``) fires
                # one mypy error per under-implementing driver.
                assert_never(request)

    def _prepare_state(
        self,
        context: DriverContext,
        semantic_op: str,
        params: Mapping[str, Any],
        *,
        ingress_kind: str,
    ) -> DriverIngressResult:
        payload = _trace_revision_payload(_payload_param(params))
        return _json_state_ingress_result(
            context=context,
            driver_id=self.driver_id,
            semantic_op=semantic_op,
            payload=payload,
            materialization_class=self.materialization_class,
            ingress_kind=ingress_kind,
        )


@dataclass(frozen=True)
class TaskTraceSubstrateAdapter(_PayloadStateAdapter):
    """Role-aware helper for provider-neutral task trace substrate revisions.

    Same payload-shaped sugar as the session adapter; a trace candidate is an
    ``append`` rather than ``create-candidate`` (a trace is durable observation
    history — ``trace-substrate-hybrid.md``).
    """

    driver: BaseSubstrateDriver = field(default_factory=TaskTraceSubstrateDriver)
    candidate_command: str = "append"


@dataclass(frozen=True)
class WorldRefSubstrateDriver(BaseSubstrateDriver):
    """Native command driver for recursive world-ref substrate revisions."""

    store_id: str = "store_child_world_ref"
    binding: str = "child"
    role: str = WORLD_REF_ROLE
    driver_id: str = "vcscore.world_ref"
    driver_version: str = "v1"
    materialization_class: str = "internal"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            accepts=frozenset({CommandRequest}),
            selectable=True,
            recursive_reference_capable=True,
        )

    def describe(self) -> DriverSchema:
        return DriverSchema(
            driver_id=self.driver_id,
            driver_version=self.driver_version,
            capabilities=self.capabilities,
            commands={
                "import": CommandSpec(
                    description="Import an external world-ref revision referencing an existing child world.",
                    params={
                        "world_oid": ParamSpec(type="str", description="child world oid"),
                        "world_store_id": ParamSpec(
                            type="str",
                            required=False,
                            description="expected child-world store id",
                        ),
                        "expected_snapshot_digest": ParamSpec(
                            type="str",
                            required=False,
                            description="expected child-world snapshot digest",
                        ),
                    },
                ),
                "create-candidate": CommandSpec(
                    description="Create a JSON-backed world-ref candidate revision.",
                    params={
                        "world_oid": ParamSpec(type="str", description="child world oid"),
                        "world_store_id": ParamSpec(
                            type="str",
                            required=False,
                            description="expected child-world store id",
                        ),
                        "expected_snapshot_digest": ParamSpec(
                            type="str",
                            required=False,
                            description="expected child-world snapshot digest",
                        ),
                    },
                ),
            },
        )

    def prepare(
        self,
        context: DriverContext,
        request: IngressRequest,
    ) -> DriverIngressResult:
        """Typed dispatch over IngressRequest variants (SPI v0.1 §Q1)."""
        match request:
            case CommandRequest(command="import", params=params):
                return self._prepare_state(context, "import", params, ingress_kind=request.ingress_kind)
            case CommandRequest(command="create-candidate", params=params):
                return self._prepare_state(
                    context,
                    "world-ref-json-revision",
                    params,
                    ingress_kind=request.ingress_kind,
                )
            case CommandRequest(command=other_cmd):
                raise ValueError(f"unsupported world-ref command: {other_cmd!r}")
            case ScanRequest() | CaptureRequest() | ReduceRequest() | MergeRequest():
                # Capabilities declare ``accepts=frozenset({CommandRequest})``;
                # the coordinator pre-flight rejects non-accepted request
                # types via ``UnsupportedRequestError`` before reaching here.
                # This arm narrows the union so the wildcard ``case _``
                # below is reserved for v0.2 variants (e.g., ``ReplayRequest``).
                raise UnsupportedRequestError(driver_id=self.driver_id, request_type=type(request))
            case _:
                # SPI v0.1 §Q1 exhaustiveness discipline: every driver's
                # match over ``IngressRequest`` ends with ``assert_never``
                # so a new variant in v0.2 (e.g., ``ReplayRequest``) fires
                # one mypy error per under-implementing driver.
                assert_never(request)

    def _prepare_state(
        self,
        context: DriverContext,
        semantic_op: str,
        params: Mapping[str, Any],
        *,
        ingress_kind: str,
    ) -> DriverIngressResult:
        if context.child_worlds is None:
            raise InvalidRepositoryStateError("world-ref driver requires child-world resolver")
        world_oid = _required_str(params, "world_oid")
        snapshot = context.child_worlds.resolve_child_world(
            world_oid,
            expected_world_store_id=_optional_str(params, "world_store_id"),
            expected_snapshot_digest=_optional_str(params, "expected_snapshot_digest"),
        )
        payload = WorldRefPayload(
            world_store_id=snapshot.world_store_id,
            world_oid=snapshot.world_oid,
            snapshot_digest=snapshot.snapshot_digest,
        ).to_json()
        observation = ObservationDraft(
            observation_id="child-world",
            evidence_kind=f"{ingress_kind}:{semantic_op}",
            stable_observation={
                "binding": context.binding,
                "store_id": context.store_identity.store_id,
                "resource_id": context.store_identity.resource_id,
                "substrate_kind": context.store_identity.kind,
                "semantic_op": semantic_op,
                "parent_heads": list(context.base_heads),
                "world_store_id": snapshot.world_store_id,
                "world_oid": snapshot.world_oid,
                "snapshot_digest": snapshot.snapshot_digest,
            },
            mechanism=self.driver_id,
        )
        transition = TransitionDraft(
            transition_id="primary",
            semantic_op=semantic_op,
            payload=payload,
            observation_ids=(observation.observation_id,),
            base_heads=context.base_heads,
            payload_descriptor_claim=PayloadDescriptorClaim.for_json_payload(payload),
            materialization_class=self.materialization_class,
        )
        return DriverIngressResult(observations=(observation,), transitions=(transition,))


@dataclass(frozen=True)
class WorldRefSubstrateAdapter(RoleSubstrateAdapter):
    """Manager-bound publishing helper for the world-ref driver.

    Diverges from the payload-state adapters: its commands take a child-world
    reference (``world_oid`` + optional store-id / snapshot-digest) rather than
    a payload dict, its ``_context`` injects a read-only child-world resolver,
    and ``head`` asserts the world-ref store kind. The generic ``build_revision``
    / ``build_candidate`` carry the child-world params straight through.
    """

    driver: BaseSubstrateDriver = field(default_factory=WorldRefSubstrateDriver)
    revision_command: str = "import"

    @staticmethod
    def _child_world_params(
        world_oid: str,
        expected_snapshot_digest: str | None,
        world_store_id: str | None,
    ) -> dict[str, Any]:
        return {
            "world_oid": world_oid,
            "expected_snapshot_digest": expected_snapshot_digest,
            "world_store_id": world_store_id,
        }

    def create_import_revision(
        self,
        ref: str,
        *,
        operation_id: str,
        world_oid: str,
        expected_snapshot_digest: str | None = None,
        world_store_id: str | None = None,
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
    ) -> str:
        return self.build_revision(
            ref,
            operation_id=operation_id,
            params=self._child_world_params(world_oid, expected_snapshot_digest, world_store_id),
            parents=parents,
            message=message,
        )

    def create_candidate(
        self,
        *,
        operation_id: str,
        world_oid: str,
        expected_snapshot_digest: str | None = None,
        world_store_id: str | None = None,
        parents: tuple[str | pygit2.Oid, ...] = (),
        message: str | None = None,
    ) -> PreparedCandidateBundle:
        return self.build_candidate(
            operation_id=operation_id,
            params=self._child_world_params(world_oid, expected_snapshot_digest, world_store_id),
            parents=parents,
            message=message,
        )

    def head(self, oid: str) -> SubstrateHead:
        head = super().head(oid)
        if head.kind != WORLD_REF_SUBSTRATE_KIND:
            raise ValueError(f"world-ref store must have kind {WORLD_REF_SUBSTRATE_KIND!r}")
        return head

    def _context(self, *, operation_id: str, parents: tuple[str | pygit2.Oid, ...]) -> DriverContext:
        return DriverContext(
            operation_id=operation_id,
            binding=self.driver.binding,
            role=self.driver.role,
            store_identity=self.manager.store(self.driver.store_id).identity,
            base_heads=tuple(str(parent) for parent in parents),
            child_worlds=ReadOnlyChildWorldResolver(
                world_store_id=self.manager.world_store.world_store_id,
                read_world_fn=self.manager.read_world,
            ),
        )


@dataclass(frozen=True)
class ReadOnlyChildWorldResolver:
    """Narrow child-world lookup service supplied to world-ref drivers."""

    world_store_id: str
    read_world_fn: Callable[[str], WorldCommit]

    def resolve_child_world(
        self,
        world_oid: str,
        *,
        expected_world_store_id: str | None = None,
        expected_snapshot_digest: str | None = None,
    ) -> ChildWorldSnapshot:
        resolved_world_store_id = expected_world_store_id or self.world_store_id
        if resolved_world_store_id != self.world_store_id:
            raise InvalidRepositoryStateError("world-ref payload world_store_id disagrees with coordinator")
        world = self.read_world_fn(world_oid)
        snapshot_digest = world.snapshot.digest()
        if expected_snapshot_digest is not None and expected_snapshot_digest != snapshot_digest:
            raise InvalidRepositoryStateError(
                "world-ref payload expected_snapshot_digest disagrees with referenced world"
            )
        return ChildWorldSnapshot(
            world_store_id=resolved_world_store_id,
            world_oid=world.oid,
            snapshot_digest=snapshot_digest,
        )


def _json_state_ingress_result(
    *,
    context: DriverContext,
    driver_id: str,
    semantic_op: str,
    payload: dict[str, Any],
    materialization_class: str,
    ingress_kind: str,
    git_tree_oid: str | None = None,
) -> DriverIngressResult:
    payload_digest = PayloadDescriptorClaim.for_json_payload(payload).payload_digest
    observation = ObservationDraft(
        observation_id="payload",
        evidence_kind=f"{ingress_kind}:{semantic_op}",
        stable_observation={
            "binding": context.binding,
            "store_id": context.store_identity.store_id,
            "resource_id": context.store_identity.resource_id,
            "substrate_kind": context.store_identity.kind,
            "semantic_op": semantic_op,
            "parent_heads": list(context.base_heads),
            "payload_digest": payload_digest,
        },
        mechanism=driver_id,
    )
    transition = TransitionDraft(
        transition_id="primary",
        semantic_op=semantic_op,
        payload=payload,
        observation_ids=(observation.observation_id,),
        base_heads=context.base_heads,
        payload_descriptor_claim=PayloadDescriptorClaim.for_json_payload(payload),
        materialization_class=materialization_class,
        git_tree_oid=git_tree_oid,
    )
    return DriverIngressResult(observations=(observation,), transitions=(transition,))


def workspace_state_manifest_payload(
    entries: tuple[Mapping[str, object], ...],
    *,
    byte_authority: str = "digest-only",
) -> dict[str, object]:
    """Build a canonical digest-only workspace-state manifest payload."""
    if byte_authority not in WORKSPACE_MANIFEST_BYTE_AUTHORITY_MODES:
        raise ValueError(f"unsupported workspace manifest byte_authority: {byte_authority!r}")
    normalized = _normalize_workspace_manifest_entries(entries)
    return validate_workspace_state_manifest_payload(
        {
            "schema": WORKSPACE_STATE_MANIFEST_SCHEMA,
            "byte_authority": byte_authority,
            "entries": list(normalized),
        }
    )


def validate_workspace_state_manifest_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Validate and return a canonical workspace-state manifest payload."""
    if payload.get("schema") != WORKSPACE_STATE_MANIFEST_SCHEMA:
        raise ValueError(f"workspace manifest schema must be {WORKSPACE_STATE_MANIFEST_SCHEMA!r}")
    byte_authority = payload.get("byte_authority")
    if byte_authority not in WORKSPACE_MANIFEST_BYTE_AUTHORITY_MODES:
        raise ValueError(f"unsupported workspace manifest byte_authority: {byte_authority!r}")
    entries = payload.get("entries")
    normalized = _normalize_workspace_manifest_entries(entries)
    if not isinstance(entries, list) or entries != list(normalized):
        raise ValueError("workspace manifest entries must be canonical")
    return {
        "schema": WORKSPACE_STATE_MANIFEST_SCHEMA,
        "byte_authority": byte_authority,
        "entries": list(normalized),
    }


def _normalize_workspace_manifest_entries(entries: object) -> tuple[dict[str, object], ...]:
    if not isinstance(entries, (tuple, list)):
        raise TypeError("workspace manifest entries must be a sequence")
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TypeError("workspace manifest entries must be objects")
        path = _normalize_manifest_path(entry.get("path"))
        if path in seen:
            raise ValueError(f"duplicate workspace manifest path: {path!r}")
        seen.add(path)
        state = entry.get("state", "present")
        if state == "deleted":
            if set(entry) != {"path", "state"}:
                raise ValueError("workspace manifest deleted entries only allow path and state")
            normalized.append({"path": path, "state": "deleted"})
            continue
        if state != "present":
            raise ValueError("workspace manifest entry state must be 'present' or 'deleted'")
        if set(entry) != {"path", "state", "mode", "content_digest"}:
            raise ValueError("workspace manifest present entries require path, state, mode, and content_digest")
        mode = entry.get("mode")
        if not isinstance(mode, int) or isinstance(mode, bool) or mode not in WORKSPACE_MANIFEST_FILE_MODES:
            raise ValueError("workspace manifest present entries require mode 100644 or 100755")
        content_digest = entry.get("content_digest")
        if not _is_sha256_digest(content_digest):
            raise ValueError("workspace manifest present entries require content_digest")
        normalized.append(
            {
                "path": path,
                "state": "present",
                "mode": mode,
                "content_digest": content_digest,
            }
        )
    return tuple(sorted(normalized, key=lambda item: str(item["path"])))


def workspace_state_revision_payload(
    entries: tuple[Mapping[str, object], ...],
    *,
    byte_authority: str = "digest-only",
) -> dict[str, object]:
    """Wrap a workspace-state manifest in the existing JSON workspace revision schema."""
    return {
        "schema": WORKSPACE_REVISION_SCHEMA,
        "state_manifest": workspace_state_manifest_payload(entries, byte_authority=byte_authority),
    }


def _workspace_capture_evidence_ingress_result(
    *,
    context: DriverContext,
    driver_id: str,
    command_operation_id: str,
    capture_events: tuple[object, ...],
) -> DriverIngressResult:
    observations = tuple(
        _capture_observation(
            context=context,
            driver_id=driver_id,
            event=event,
            event_index=index,
            command_operation_id=command_operation_id,
        )
        for index, event in enumerate(capture_events)
    )
    return DriverIngressResult(observations=observations)


def _workspace_capture_reduction_from_evidence_ingress_result(
    *,
    context: DriverContext,
    driver_id: str,
    payload: dict[str, object],
    materialization_class: str,
    command_operation_id: str,
    evidence_citation_ids: tuple[str, ...],
    reduced_state_proof: Mapping[str, object],
    git_tree_oid: str | None = None,
) -> DriverIngressResult:
    if not evidence_citation_ids:
        raise ValueError("capture reduction requires at least one evidence citation")
    _validate_workspace_capture_reduction_payload_and_proof(
        payload=payload,
        command_operation_id=command_operation_id,
        reduced_state_proof=reduced_state_proof,
    )
    payload_digest = PayloadDescriptorClaim.for_json_payload(payload).payload_digest
    proof_observation: dict[str, object] = {
        "command_operation_id": command_operation_id,
        "binding": context.binding,
        "store_id": context.store_identity.store_id,
        "resource_id": context.store_identity.resource_id,
        "substrate_kind": context.store_identity.kind,
        "semantic_op": "workspace-capture-reduction",
        "payload_digest": payload_digest,
        "proof": dict(reduced_state_proof),
    }
    observation = ObservationDraft(
        observation_id="reduced-state-proof",
        evidence_kind="reduce:reduced-state-proof",
        stable_observation=proof_observation,
        mechanism=driver_id,
        correlation_id=command_operation_id,
        evidence_payload_descriptor_claim=PayloadDescriptorClaim.for_json_payload(proof_observation),
    )
    transition = TransitionDraft(
        transition_id="primary",
        semantic_op="workspace-capture-reduction",
        payload=payload,
        observation_ids=(observation.observation_id,),
        evidence_citation_ids=evidence_citation_ids,
        base_heads=context.base_heads,
        payload_descriptor_claim=PayloadDescriptorClaim.for_json_payload(payload),
        materialization_class=materialization_class,
        git_tree_oid=git_tree_oid,
    )
    return DriverIngressResult(observations=(observation,), transitions=(transition,))


def _validate_workspace_capture_reduction_payload_and_proof(
    *,
    payload: Mapping[str, object],
    command_operation_id: str,
    reduced_state_proof: Mapping[str, object],
) -> None:
    if payload.get("schema") != WORKSPACE_REVISION_SCHEMA:
        raise ValueError(f"capture reduction payload schema must be {WORKSPACE_REVISION_SCHEMA!r}")
    manifest = payload.get("state_manifest")
    if not isinstance(manifest, Mapping):
        raise TypeError("capture reduction payload requires state_manifest")
    validated_manifest = validate_workspace_state_manifest_payload(manifest)
    manifest_digest = canonical_digest(validated_manifest)
    proof_manifest_digest = reduced_state_proof.get("manifest_digest")
    if not _is_sha256_digest(proof_manifest_digest):
        raise ValueError("capture reduction proof requires manifest_digest")
    if proof_manifest_digest != manifest_digest:
        raise ValueError("capture reduction proof manifest_digest disagrees with payload")
    proof_byte_authority = reduced_state_proof.get("byte_authority")
    if proof_byte_authority != validated_manifest["byte_authority"]:
        raise ValueError("capture reduction proof byte_authority disagrees with payload")
    proof_command_operation_id = reduced_state_proof.get("command_operation_id")
    if proof_command_operation_id is not None and proof_command_operation_id != command_operation_id:
        raise ValueError("capture reduction proof command_operation_id disagrees with command")


def _capture_observation(
    *,
    context: DriverContext,
    driver_id: str,
    event: object,
    event_index: int,
    command_operation_id: str,
) -> ObservationDraft:
    event_command_id = _capture_event_str(event, "command_operation_id")
    if event_command_id != command_operation_id:
        raise ValueError("capture event command_operation_id disagrees with command")
    binding = _capture_event_str(event, "binding_name")
    if binding != context.binding and not (context.binding == "workspace" and binding == "filesystem"):
        raise ValueError("capture event binding_name disagrees with workspace binding")
    stable_observation: dict[str, object] = {
        "command_operation_id": command_operation_id,
        "binding": context.binding,
        "source_binding": binding,
        "store_id": context.store_identity.store_id,
        "resource_id": context.store_identity.resource_id,
        "substrate_kind": context.store_identity.kind,
        "semantic_op": "workspace-capture-reduction",
        "op": _capture_event_str(event, "op"),
        "path": _capture_event_str(event, "path"),
        "scope": _capture_event_str(event, "scope"),
        "scope_instance_id": _capture_event_str(event, "scope_instance_id"),
        "pid": _capture_event_int(event, "pid"),
        "proc_seq": _capture_event_int(event, "proc_seq"),
        "global_seq": _capture_event_int(event, "global_seq"),
        "event_seq": _capture_event_int(event, "event_seq"),
        "capture_mechanism": _capture_event_str(event, "capture_mechanism"),
    }
    for key in ("capture_epoch", "ppid", "exe", "cwd"):
        value = _capture_event_optional(event, key)
        if value is not None:
            stable_observation[key] = value
    return ObservationDraft(
        observation_id=f"capture-{event_index}-{stable_observation['global_seq']}",
        evidence_kind="capture:filesystem-event",
        stable_observation=stable_observation,
        mechanism=driver_id,
        correlation_id=command_operation_id,
        evidence_payload_descriptor_claim=PayloadDescriptorClaim.for_json_payload(stable_observation),
    )


def _payload_param(params: Mapping[str, Any]) -> dict[str, Any]:
    value = params.get("payload")
    if not isinstance(value, dict):
        raise TypeError("payload is required")
    return dict(value)


def _capture_events_param(params: Mapping[str, Any]) -> tuple[object, ...]:
    value = params.get("capture_events")
    if not isinstance(value, (tuple, list)) or not value:
        raise ValueError("capture_events must be a non-empty sequence")
    return tuple(value)


def _mapping_param(params: Mapping[str, Any], key: str) -> Mapping[str, object]:
    value = params.get(key)
    if not isinstance(value, Mapping):
        raise TypeError(f"{key} is required")
    return value


def _str_tuple_param(params: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = params.get(key)
    if not isinstance(value, (tuple, list)) or not value:
        raise ValueError(f"{key} must be a non-empty sequence")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{key} must contain non-empty strings")
    return tuple(value)


def _capture_event_str(event: object, key: str) -> str:
    value = _capture_event_value(event, key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"capture event {key} is required")
    return value


def _capture_event_int(event: object, key: str) -> int:
    value = _capture_event_value(event, key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"capture event {key} must be an integer")
    return value


def _capture_event_optional(event: object, key: str) -> object | None:
    return _capture_event_value(event, key)


def _capture_event_value(event: object, key: str) -> object:
    if isinstance(event, Mapping):
        return event.get(key)
    return getattr(event, key, None)


def _normalize_manifest_path(value: object) -> str:
    if not isinstance(value, str) or value in {"", "."} or "\0" in value:
        raise ValueError("workspace manifest path must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("workspace manifest path must be relative")
    parts = path.parts
    if not parts or ".." in parts or parts[0] == ".vcscore":
        raise ValueError("workspace manifest path escapes workspace authority")
    return path.as_posix()


def _is_sha256_digest(value: object) -> bool:
    if not isinstance(value, str):
        return False
    prefix = "sha256:"
    hex_digest = value.removeprefix(prefix)
    return (
        value.startswith(prefix)
        and len(hex_digest) == 64
        and all(char in "0123456789abcdefABCDEF" for char in hex_digest)
    )


def _required_str(params: Mapping[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _optional_str(params: Mapping[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string when provided")
    return value


def _revision_payload(schema: str, payload: dict[str, Any]) -> dict[str, Any]:
    if "schema" in payload and payload["schema"] != schema:
        raise ValueError(f"payload schema must be {schema!r}")
    return {"schema": schema, **payload}


def _trace_revision_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "kind" in payload and payload["kind"] != TRACE_SUBSTRATE_KIND:
        raise ValueError(f"trace payload kind must be {TRACE_SUBSTRATE_KIND!r}")
    revision = {"kind": TRACE_SUBSTRATE_KIND, **_revision_payload(TRACE_REVISION_SCHEMA, payload)}
    _required_str(revision, "trace_runtime")
    _required_str(revision, "trace_owner_id")
    _required_str(revision, "frontier_id")
    _validate_hybrid_trace_events(revision)
    return revision


# B4b slice 2 (W1): the hybrid event-kind discipline, enforced driver-side.
# The 5-field floor stays a floor — a payload without `events` is untouched.
# The taxonomy is open by design: unknown kinds are admitted iff namespaced
# ('family.name') AND obeying the pointer/record discipline below — that is
# the extension point, not silent acceptance of arbitrary shapes.
_TRACE_POINTER_KINDS = frozenset({"substrate.transition"})


def _validate_hybrid_trace_events(revision: dict[str, Any]) -> None:
    events = revision.get("events")
    if events is None:
        return
    _required_str(revision, "identity_domain")  # the hoisted header default
    ids: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise TypeError("trace events must be mappings")
        event_id, kind = event.get("id"), event.get("kind")
        if not (isinstance(event_id, str) and event_id):
            raise ValueError("every trace event needs a non-empty string 'id'")
        is_effect_kind = isinstance(kind, str) and kind.isidentifier() and kind[:1].isupper()
        if not (isinstance(kind, str) and ("." in kind or is_effect_kind)):
            raise ValueError(
                f"trace event kind must be a namespaced string ('family.name') or an "
                f"effect-type record kind (CamelCase identifier, e.g. 'FileCreate'); got {kind!r}. "
                "Known taxonomy kinds: task.invocation, substrate.transition, run.lifecycle, "
                "supervisor.decision; new kinds must be namespaced and obey the "
                "pointer/record discipline."
            )
        ids.append(event_id)
        if kind in _TRACE_POINTER_KINDS:
            if "record_digest" in event:
                raise ValueError(
                    f"pointer entry {event_id!r} ({kind}) cites by content address and carries no record_digest"
                )
        elif "record_digest" in event and not event.get("identity_domain"):
            raise ValueError(
                f"digested record {event_id!r} ({kind}) must carry identity_domain "
                "explicitly — the header default never applies to digested records "
                "(the fourth-row override; trace-identity-dual-domain.md)"
            )
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate trace event ids: {ids!r}")
    known = set(ids)
    for edge in revision.get("causal_edges") or ():
        if len(tuple(edge)) != 2 or not set(edge) <= known:
            raise ValueError(f"causal edge {edge!r} must link two known event ids")
    for owner, path in (revision.get("owner_paths") or {}).items():
        if not set(path) <= known:
            raise ValueError(f"owner path {owner!r} references unknown event ids")


def _plan_candidate_selection(
    manager: WorldStorageManager,
    role: str,
    bundle: PreparedCandidateBundle,
    *,
    operation_id: str | None = None,
    selection_kind: Literal["new-candidate", "child-produced"] | None = None,
    producer_operation_id: str | None = None,
    producer_world_oid: str | None = None,
    relationship_requirements: tuple[RelationshipRequirement, ...] = (),
    retention_policy_requirements: tuple[RetentionPolicyRequirement, ...] = (),
    selection_policy_digest: str | None = None,
) -> CandidateSelectionPlan:
    from vcs_core._world_operation_builder import CandidateSelection

    return manager.plan_candidate_selection(
        operation_id=operation_id or bundle.candidate_commit.operation_id,
        selection=CandidateSelection.from_bundle(bundle),
        selection_kind=selection_kind,
        producer_operation_id=producer_operation_id,
        producer_world_oid=producer_world_oid,
        role=role,
        relationship_requirements=relationship_requirements,
        retention_policy_requirements=retention_policy_requirements,
        selection_policy_digest=selection_policy_digest,
    )
