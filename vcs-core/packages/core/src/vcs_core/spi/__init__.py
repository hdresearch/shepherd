"""The substrate SPI — vcs-core's stable *implement-side* surface.

What a substrate author builds against: the driver protocol, the typed
``IngressRequest`` family, the result/draft DTOs, the introspection schema,
the capture family, and the opt-in execution-mechanism capability. This is
the implement-side counterpart to ``vcs_core/runtime_api.py`` (the *use*-side
surface — what a consumer calls to drive vcs-core).

Stability: this is the single source of truth for the SPI vocabulary. The
pre-launch experimental compatibility alias has been retired, so new substrate
authors import from this module directly. The contract stays ``SPI_VERSION = 0``
/ revision ``SUBSTRATE_DRIVER_CONTRACT_REVISION``, growing additively
(``decisions.md`` ``spi-additive-no-bump``).

Name note: ``CommandSpec`` / ``ParamSpec`` are the driver-side schema
dataclasses returned by ``SubstrateDriver.describe()``.

Not here: the built-in drivers (``TaskTraceSubstrateDriver``); the
runtime-substrate *composition seam* (``ExecutionProvider``, ``HandlerStack``,
the effect signatures, Tier-A ``resolve_task_id``) — dialect machinery, not
contract; reducer-support records; coordinator internals.

The conformance kit lives at ``vcs_core.spi.testing``.
"""

from __future__ import annotations

from vcs_core._execution_capability import (
    EXECUTION_CAPABILITY_VERSION,
    ConfinementSpec,
    ExecutionAuthorityRequired,
    ExecutionBoundDriver,
    ExecutionCapability,
    NetMode,
    NetworkPolicy,
    UnsupportedConfinementSpecError,
    verify_execution_negotiation,
)
from vcs_core._substrate_driver import (
    SUBSTRATE_DRIVER_CONTRACT_REVISION,
    ActiveSurface,
    AuthorityRole,
    BaseSubstrateDriver,
    CapabilityContractViolation,
    CapabilitySet,
    CaptureAdapter,
    CaptureAdapterRegistry,
    CaptureAdapterSchema,
    CaptureRequest,
    ChildWorldResolver,
    ChildWorldSnapshot,
    CommandRequest,
    CommandSpec,
    CrashLagOrdering,
    Diagnostic,
    DriverAuthorityRequiredError,
    DriverContext,
    DriverIngressResult,
    DriverSchema,
    DriverSelectionRequirementDraft,
    EvidenceKindReconciliationError,
    FanOutSink,
    GrowthBound,
    IngressRequest,
    KeyedJsonPut,
    KeyedJsonTreeDraft,
    MergeRequest,
    MergeSpec,
    ObservationDraft,
    ObservationSink,
    ParamSpec,
    ParseResult,
    ReadSafety,
    ReduceRequest,
    RetentionHint,
    RevisionContentDraft,
    RevisionStorageProfile,
    RevisionStorageShape,
    ScanRequest,
    ScanSpec,
    SinkFailure,
    SubstrateContractError,
    SubstrateDriver,
    SurfacePolicyError,
    TransitionDraft,
    TupleSink,
    UnsupportedRequestError,
    command,
    validate_driver_ingress,
    validate_driver_ingress_result,
)
from vcs_core._transition_kernel_records import PayloadDescriptorClaim, RelationshipRequirement
from vcs_core._world_types import SubstrateStoreIdentity

#: The ingestion-contract major version. ``0`` while the contract may still
#: break; the sub-version under it is ``SUBSTRATE_DRIVER_CONTRACT_REVISION``.
#: Canonical definition lives here (the SPI's home).
SPI_VERSION = 0

__all__ = [  # noqa: RUF022 — grouped by theme with section comments (a public-surface doc), not sorted
    # Versioning
    "SPI_VERSION",
    "SUBSTRATE_DRIVER_CONTRACT_REVISION",
    # Execution-mechanism capability surface (opt-in; separately versioned)
    "EXECUTION_CAPABILITY_VERSION",
    "ConfinementSpec",
    "ExecutionAuthorityRequired",
    "ExecutionBoundDriver",
    "ExecutionCapability",
    "NetMode",
    "NetworkPolicy",
    "UnsupportedConfinementSpecError",
    "verify_execution_negotiation",
    # The driver protocol + default-bearing mixin
    "SubstrateDriver",
    "BaseSubstrateDriver",
    "command",
    # Typed ingress request family
    "IngressRequest",
    "CommandRequest",
    "ScanRequest",
    "CaptureRequest",
    "ReduceRequest",
    "MergeRequest",
    # Capability + surface
    "CapabilitySet",
    "AuthorityRole",
    "CrashLagOrdering",
    "GrowthBound",
    "ReadSafety",
    "RevisionStorageProfile",
    "RevisionStorageShape",
    "ActiveSurface",
    # Driver context + child-world resolution
    "DriverContext",
    "ChildWorldResolver",
    "ChildWorldSnapshot",
    # Result + draft DTOs
    "DriverIngressResult",
    "TransitionDraft",
    "KeyedJsonPut",
    "KeyedJsonTreeDraft",
    "RevisionContentDraft",
    "ObservationDraft",
    "RetentionHint",
    "DriverSelectionRequirementDraft",
    "Diagnostic",
    # Introspection schema (driver-side; natural names)
    "DriverSchema",
    "CommandSpec",
    "ParamSpec",
    "ScanSpec",
    "MergeSpec",
    "CaptureAdapterSchema",
    # Capture family
    "CaptureAdapter",
    "CaptureAdapterRegistry",
    "ObservationSink",
    "TupleSink",
    "FanOutSink",
    "SinkFailure",
    "ParseResult",
    # Errors
    "SubstrateContractError",
    "UnsupportedRequestError",
    "DriverAuthorityRequiredError",
    "CapabilityContractViolation",
    "EvidenceKindReconciliationError",
    "SurfacePolicyError",
    # Validators
    "validate_driver_ingress",
    "validate_driver_ingress_result",
    # Support types
    "PayloadDescriptorClaim",
    "RelationshipRequirement",
    "SubstrateStoreIdentity",
]
