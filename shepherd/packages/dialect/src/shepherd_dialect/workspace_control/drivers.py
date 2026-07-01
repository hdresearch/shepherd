"""Selectable workspace-control ledger drivers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, assert_never

from vcs_core.spi import (
    BaseSubstrateDriver,
    CapabilitySet,
    CaptureRequest,
    CommandRequest,
    CommandSpec,
    DriverAuthorityRequiredError,
    DriverContext,
    DriverIngressResult,
    DriverSchema,
    IngressRequest,
    KeyedJsonTreeDraft,
    MergeRequest,
    ObservationDraft,
    ParamSpec,
    PayloadDescriptorClaim,
    ReduceRequest,
    RevisionStorageProfile,
    ScanRequest,
    TransitionDraft,
    UnsupportedRequestError,
)

from shepherd_dialect.workspace_control.ledger_contracts import (
    RUN_LEDGER_BINDING,
    RUN_LEDGER_SCHEMA,
    TASK_ARTIFACT_BINDING,
    TASK_ARTIFACT_SCHEMA,
    TASK_LEDGER_BINDING,
    TASK_LEDGER_SCHEMA,
)

TASK_LEDGER_STORE_ID = "store_shepherd_tasks"
TASK_ARTIFACT_STORE_ID = "store_shepherd_task_artifacts"
RUN_LEDGER_STORE_ID = "store_shepherd_runs"
TASK_ARTIFACT_RESOURCE_ID = "shepherd-task-artifacts:main"
TASK_LEDGER_ROLE = "shepherd.TaskLibrary"
TASK_ARTIFACT_ROLE = "shepherd.TaskArtifacts"
RUN_LEDGER_ROLE = "shepherd.RunLedger"
TASK_LEDGER_DRIVER_ID = "shepherd.task_ledger"
TASK_ARTIFACT_DRIVER_ID = "shepherd.task_artifacts"
RUN_LEDGER_DRIVER_ID = "shepherd.run_ledger"

_LEDGER_AUTHORITY_TOKEN = object()


@dataclass(frozen=True)
class LedgerWriteAuthority:
    """In-process capability for internal workspace-control ledger writes."""

    _token: object


def mint_ledger_write_authority() -> LedgerWriteAuthority:
    """Return the in-process authority required by ledger publish commands."""
    return LedgerWriteAuthority(_LEDGER_AUTHORITY_TOKEN)


def require_ledger_write_authority(value: object) -> LedgerWriteAuthority:
    """Validate the internal ledger-write authority parameter."""
    if not isinstance(value, LedgerWriteAuthority) or value._token is not _LEDGER_AUTHORITY_TOKEN:
        raise DriverAuthorityRequiredError(
            "workspace-control ledger writes require ShepherdWorkspace orchestration authority"
        )
    return value


@dataclass(frozen=True)
class ShepherdTaskLedgerDriver(BaseSubstrateDriver):
    """Selectable JSON ledger for registered Shepherd task definitions."""

    store_id: str = TASK_LEDGER_STORE_ID
    binding: str = TASK_LEDGER_BINDING
    role: str = TASK_LEDGER_ROLE
    driver_id: str = TASK_LEDGER_DRIVER_ID
    driver_version: str = "v1"
    materialization_class: str = "external"
    lifecycle_class: str = "evidence"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(accepts=frozenset({CommandRequest}), selectable=True)

    def describe(self) -> DriverSchema:
        return _ledger_driver_schema(
            driver_id=self.driver_id,
            driver_version=self.driver_version,
            capabilities=self.capabilities,
            description="Publish a complete task-library ledger revision.",
        )

    def prepare(self, context: DriverContext, request: IngressRequest) -> DriverIngressResult:
        match request:
            case CommandRequest(command="publish", params=params):
                return _prepare_ledger_publish(
                    context=context,
                    driver_id=self.driver_id,
                    semantic_op="task-ledger-publish",
                    materialization_class=self.materialization_class,
                    expected_schema=TASK_LEDGER_SCHEMA,
                    params=params,
                    ingress_kind=request.ingress_kind,
                )
            case CommandRequest(command=other_cmd):
                raise ValueError(f"unsupported task-ledger command: {other_cmd!r}")
            case ScanRequest() | CaptureRequest() | ReduceRequest() | MergeRequest():
                raise UnsupportedRequestError(driver_id=self.driver_id, request_type=type(request))
            case _:
                assert_never(request)


@dataclass(frozen=True)
class ShepherdTaskArtifactDriver(BaseSubstrateDriver):
    """Selectable JSON store for immutable Shepherd task artifacts."""

    store_id: str = TASK_ARTIFACT_STORE_ID
    binding: str = TASK_ARTIFACT_BINDING
    role: str = TASK_ARTIFACT_ROLE
    driver_id: str = TASK_ARTIFACT_DRIVER_ID
    driver_version: str = "v1"
    materialization_class: str = "external"
    lifecycle_class: str = "evidence"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(accepts=frozenset({CommandRequest}), selectable=True)

    def describe(self) -> DriverSchema:
        return _artifact_driver_schema(
            driver_id=self.driver_id,
            driver_version=self.driver_version,
            capabilities=self.capabilities,
        )

    def prepare(self, context: DriverContext, request: IngressRequest) -> DriverIngressResult:
        match request:
            case CommandRequest(command="put", params=params):
                return _prepare_ledger_publish(
                    context=context,
                    driver_id=self.driver_id,
                    semantic_op="task-artifact-put",
                    materialization_class=self.materialization_class,
                    expected_schema=TASK_ARTIFACT_SCHEMA,
                    params=params,
                    ingress_kind=request.ingress_kind,
                )
            case CommandRequest(command=other_cmd):
                raise ValueError(f"unsupported task-artifact command: {other_cmd!r}")
            case ScanRequest() | CaptureRequest() | ReduceRequest() | MergeRequest():
                raise UnsupportedRequestError(driver_id=self.driver_id, request_type=type(request))
            case _:
                assert_never(request)


@dataclass(frozen=True)
class ShepherdRunLedgerDriver(BaseSubstrateDriver):
    """Selectable JSON ledger for Shepherd run index/control records."""

    store_id: str = RUN_LEDGER_STORE_ID
    binding: str = RUN_LEDGER_BINDING
    role: str = RUN_LEDGER_ROLE
    driver_id: str = RUN_LEDGER_DRIVER_ID
    driver_version: str = "v2"
    materialization_class: str = "external"
    lifecycle_class: str = "evidence"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(accepts=frozenset({CommandRequest}), selectable=True)

    def describe(self) -> DriverSchema:
        return _ledger_driver_schema(
            driver_id=self.driver_id,
            driver_version=self.driver_version,
            capabilities=self.capabilities,
            description="Publish an addressable run-ledger revision.",
            storage_profile=RevisionStorageProfile(
                shape="keyed-json-tree",
                authority_role="authority",
                growth_bound="unbounded",
            ),
        )

    def prepare(self, context: DriverContext, request: IngressRequest) -> DriverIngressResult:
        match request:
            case CommandRequest(command="publish", params=params):
                return _prepare_ledger_publish(
                    context=context,
                    driver_id=self.driver_id,
                    semantic_op="run-ledger-publish",
                    materialization_class=self.materialization_class,
                    expected_schema=RUN_LEDGER_SCHEMA,
                    params=params,
                    ingress_kind=request.ingress_kind,
                    content_required=True,
                )
            case CommandRequest(command=other_cmd):
                raise ValueError(f"unsupported run-ledger command: {other_cmd!r}")
            case ScanRequest() | CaptureRequest() | ReduceRequest() | MergeRequest():
                raise UnsupportedRequestError(driver_id=self.driver_id, request_type=type(request))
            case _:
                assert_never(request)


def _ledger_driver_schema(
    *,
    driver_id: str,
    driver_version: str,
    capabilities: CapabilitySet,
    description: str,
    storage_profile: RevisionStorageProfile | None = None,
) -> DriverSchema:
    effective_storage_profile = storage_profile or RevisionStorageProfile()
    params: dict[str, ParamSpec] = {
        "payload": ParamSpec(type="object", description="run-ledger manifest payload"),
        "expected_head": ParamSpec(
            type="str?",
            description="selected head this whole-ledger publish was based on",
            has_default=True,
            default=None,
        ),
        "authority": ParamSpec(
            type="object",
            description="in-process ShepherdWorkspace ledger-write authority",
        ),
    }
    if effective_storage_profile.shape == "keyed-json-tree":
        params["content"] = ParamSpec(
            type="object",
            description="internal keyed-tree content draft",
            projectable=False,
        )
    return DriverSchema(
        driver_id=driver_id,
        driver_version=driver_version,
        capabilities=capabilities,
        storage_profile=effective_storage_profile,
        commands={
            "publish": CommandSpec(
                description=description,
                projectable=False,
                params=params,
            )
        },
    )


def _artifact_driver_schema(
    *,
    driver_id: str,
    driver_version: str,
    capabilities: CapabilitySet,
) -> DriverSchema:
    return DriverSchema(
        driver_id=driver_id,
        driver_version=driver_version,
        capabilities=capabilities,
        commands={
            "put": CommandSpec(
                description="Publish one immutable task artifact payload.",
                projectable=False,
                params={
                    "payload": ParamSpec(type="object", description="canonical task artifact payload"),
                    "expected_head": ParamSpec(
                        type="str?",
                        description="selected artifact-log head this put was based on",
                        has_default=True,
                        default=None,
                    ),
                    "authority": ParamSpec(
                        type="object",
                        description="in-process ShepherdWorkspace ledger-write authority",
                    ),
                },
            )
        },
    )


def _prepare_ledger_publish(
    *,
    context: DriverContext,
    driver_id: str,
    semantic_op: str,
    materialization_class: str,
    expected_schema: str,
    params: Mapping[str, Any],
    ingress_kind: str,
    content_required: bool = False,
) -> DriverIngressResult:
    require_ledger_write_authority(params.get("authority"))
    _validate_expected_head(context.base_heads, params.get("expected_head"))
    payload = _payload_param(params)
    if payload.get("schema") != expected_schema:
        raise ValueError(f"ledger publish expected schema {expected_schema!r}, got {payload.get('schema')!r}")
    content = params.get("content")
    if content_required and not isinstance(content, KeyedJsonTreeDraft):
        raise TypeError("run-ledger publish requires KeyedJsonTreeDraft parameter 'content'")
    if content is not None and not isinstance(content, KeyedJsonTreeDraft):
        raise TypeError("ledger publish content must be a KeyedJsonTreeDraft when present")
    return _json_state_ingress_result(
        context=context,
        driver_id=driver_id,
        semantic_op=semantic_op,
        payload=payload,
        content=content,
        materialization_class=materialization_class,
        ingress_kind=ingress_kind,
    )


def _validate_expected_head(base_heads: tuple[str, ...], expected_head: object) -> None:
    if expected_head is None:
        if base_heads:
            raise RuntimeError("selected head moved: expected no current head")
        return
    if not isinstance(expected_head, str) or not expected_head:
        raise TypeError("expected_head must be null or a non-empty string")
    if base_heads != (expected_head,):
        actual = base_heads[0] if len(base_heads) == 1 else None
        raise RuntimeError(f"selected head moved: expected {expected_head!r}, got {actual!r}")


def _payload_param(params: Mapping[str, Any]) -> dict[str, Any]:
    payload = params.get("payload")
    if not isinstance(payload, Mapping):
        raise TypeError("ledger publish requires object parameter 'payload'")
    return dict(payload)


def _json_state_ingress_result(
    *,
    context: DriverContext,
    driver_id: str,
    semantic_op: str,
    payload: dict[str, Any],
    content: KeyedJsonTreeDraft | None = None,
    materialization_class: str,
    ingress_kind: str,
) -> DriverIngressResult:
    payload_claim = PayloadDescriptorClaim.for_json_payload(payload)
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
            "payload_digest": payload_claim.payload_digest,
        },
        mechanism=driver_id,
    )
    transition = TransitionDraft(
        transition_id="primary",
        semantic_op=semantic_op,
        payload=payload,
        observation_ids=(observation.observation_id,),
        base_heads=context.base_heads,
        payload_descriptor_claim=payload_claim,
        materialization_class=materialization_class,
        content=content,
    )
    return DriverIngressResult(observations=(observation,), transitions=(transition,))
