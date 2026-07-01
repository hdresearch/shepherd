"""Private alpha operation protocol rules for v2 world workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._transition_kernel_records import CandidateCommitRecord, CandidateOutcomeRecord
from vcs_core._world_operation_builder import FinalizedWorldOperation, PreparedWorldOperation
from vcs_core._world_publication_plan import PublicationPlan
from vcs_core._world_types import CandidateRevision, OperationFinalRecord, WorldSnapshot

if TYPE_CHECKING:
    from collections.abc import Mapping

OPERATION_STATUSES = frozenset(
    {
        "opened",
        "prepared",
        "finalized",
        "world_committed",
        "publishing",
        "published",
        "closed",
        "failed",
        "archived",
    }
)
TERMINAL_OPERATION_STATUSES = frozenset({"closed", "archived"})
OPERATION_TRANSITIONS = {
    "opened": frozenset({"prepared", "failed"}),
    "prepared": frozenset({"finalized", "failed"}),
    "finalized": frozenset({"world_committed", "failed"}),
    "world_committed": frozenset({"publishing", "failed"}),
    "publishing": frozenset({"published", "failed"}),
    "published": frozenset({"closed"}),
    "failed": frozenset({"archived"}),
}

_FINALIZED_REPLAY_FIELDS = (
    "candidate_refs",
    "candidate_commits",
    "candidate_outcomes",
    "selected",
    "snapshot",
    "snapshot_digest",
    "transition",
    "parents",
    "operation_final",
    "operation_final_digest",
)
_PUBLICATION_PLAN_FIELDS = ("publication_plan", "publication_plan_digest")
_PREPARED_OPERATION_FIELDS = ("prepared_world_operation", "prepared_world_operation_digest")


def validate_operation_status(status: str) -> None:
    """Validate one alpha operation lifecycle status."""
    if status not in OPERATION_STATUSES:
        raise InvalidRepositoryStateError(f"unsupported operation journal status: {status!r}")


def validate_operation_transition(prior: str, next_status: str) -> None:
    """Validate one alpha operation lifecycle transition."""
    if next_status not in OPERATION_TRANSITIONS.get(prior, frozenset()):
        raise InvalidRepositoryStateError(f"invalid operation journal transition: {prior} -> {next_status}")


def validate_operation_transition_payload(
    prior: Mapping[str, object],
    next_payload: Mapping[str, object],
) -> None:
    """Validate alpha lifecycle transition fields that must remain monotonic."""
    prior_status = prior.get("status")
    next_status = next_payload.get("status")
    if not isinstance(prior_status, str) or not isinstance(next_status, str):
        raise InvalidRepositoryStateError("operation journal status is required")
    validate_operation_transition(prior_status, next_status)
    for field in ("world_oid", "operation_final_digest"):
        prior_value = prior.get(field)
        next_value = next_payload.get(field)
        if prior_value is not None and next_value != prior_value:
            raise InvalidRepositoryStateError(f"operation journal {field} cannot change after it is recorded")
    if _has_finalized_replay_payload(prior):
        for field in _FINALIZED_REPLAY_FIELDS:
            if prior.get(field) is not None and next_payload.get(field) != prior.get(field):
                raise InvalidRepositoryStateError(f"operation journal {field} cannot change after finalization")
    if _has_prepared_operation_payload(prior):
        for field in _PREPARED_OPERATION_FIELDS:
            if prior.get(field) is not None and next_payload.get(field) != prior.get(field):
                raise InvalidRepositoryStateError(f"operation journal {field} cannot change after preparation")
    if _has_publication_plan(prior):
        for field in _PUBLICATION_PLAN_FIELDS:
            if prior.get(field) is not None and next_payload.get(field) != prior.get(field):
                raise InvalidRepositoryStateError(f"operation journal {field} cannot change after publication intent")


def validate_operation_status_fields(payload: Mapping[str, object]) -> None:
    """Validate fields required by the payload's alpha lifecycle status."""
    status = payload.get("status")
    if not isinstance(status, str):
        raise InvalidRepositoryStateError("operation journal status is required")
    validate_operation_status(status)
    if status in {"finalized", "world_committed", "publishing", "published", "closed"}:
        _required_sha256_digest(payload, "operation_final_digest")
        _required_string_map(payload, "selected")
        _required_object_list(payload, "candidate_outcomes")
    if status in {"world_committed", "publishing", "published", "closed"}:
        _required_str(payload, "world_oid")
    if status in {"publishing", "published", "closed"} or _has_publication_plan(payload):
        _validate_publication_plan_payload(payload)
    if _has_prepared_operation_payload(payload):
        _validate_prepared_operation_payload(payload)
    if status in {"finalized", "world_committed", "publishing", "published", "closed"} or _has_finalized_replay_payload(
        payload
    ):
        _validate_finalized_replay_payload(payload)
    if status in {"failed", "archived"}:
        _required_str(payload, "error")


def _required_str(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise InvalidRepositoryStateError(f"operation journal {field} is required for status {payload.get('status')!r}")
    return value


def _nullable_str(payload: Mapping[str, object], field: str) -> str | None:
    if field not in payload:
        raise InvalidRepositoryStateError(f"operation journal {field} is required for status {payload.get('status')!r}")
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InvalidRepositoryStateError(f"operation journal {field} must be null or a non-empty string")
    return value


def _required_sha256_digest(payload: Mapping[str, object], field: str) -> str:
    value = _required_str(payload, field)
    prefix = "sha256:"
    hex_digest = value.removeprefix(prefix)
    if (
        not value.startswith(prefix)
        or len(hex_digest) != 64
        or any(char not in "0123456789abcdefABCDEF" for char in hex_digest)
    ):
        raise InvalidRepositoryStateError(f"operation journal {field} must be a sha256 digest")
    return value


def _required_string_map(payload: Mapping[str, object], field: str) -> Mapping[str, str]:
    value = payload.get(field)
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise InvalidRepositoryStateError(f"operation journal {field} is required for status {payload.get('status')!r}")
    return value


def _required_object_list(payload: Mapping[str, object], field: str) -> list[dict[str, object]]:
    value = payload.get(field)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise InvalidRepositoryStateError(f"operation journal {field} is required for status {payload.get('status')!r}")
    return [dict(item) for item in value]


def _required_object_map(payload: Mapping[str, object], field: str) -> Mapping[str, object]:
    value = payload.get(field)
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise InvalidRepositoryStateError(f"operation journal {field} is required for status {payload.get('status')!r}")
    return dict(value)


def _required_string_list(payload: Mapping[str, object], field: str) -> tuple[str, ...]:
    value = payload.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise InvalidRepositoryStateError(f"operation journal {field} is required for status {payload.get('status')!r}")
    return tuple(value)


def _has_finalized_replay_payload(payload: Mapping[str, object]) -> bool:
    return payload.get("operation_final") is not None


def _has_prepared_operation_payload(payload: Mapping[str, object]) -> bool:
    return (
        payload.get("prepared_world_operation") is not None
        or payload.get("prepared_world_operation_digest") is not None
    )


def _has_publication_plan(payload: Mapping[str, object]) -> bool:
    return payload.get("publication_plan") is not None or payload.get("publication_plan_digest") is not None


def _validate_prepared_operation_payload(payload: Mapping[str, object]) -> PreparedWorldOperation:
    try:
        prepared = PreparedWorldOperation.from_json(dict(_required_object_map(payload, "prepared_world_operation")))
        prepared_digest = _required_sha256_digest(payload, "prepared_world_operation_digest")
        if prepared.prepared_operation_digest() != prepared_digest:
            raise InvalidRepositoryStateError(
                "operation journal prepared_world_operation_digest disagrees with prepared operation"
            )
        if prepared.operation_id != _required_str(payload, "operation_id"):
            raise InvalidRepositoryStateError("operation journal prepared operation_id disagrees with journal")
        if prepared.operation_kind != _required_str(payload, "operation_kind"):
            raise InvalidRepositoryStateError("operation journal prepared operation_kind disagrees with journal")
        if prepared.target_ref != _required_str(payload, "target_ref"):
            raise InvalidRepositoryStateError("operation journal prepared target_ref disagrees with journal")
        if prepared.input_world_oid != _nullable_str(payload, "input_world_oid"):
            raise InvalidRepositoryStateError("operation journal prepared input_world_oid disagrees with journal")
        return prepared
    except InvalidRepositoryStateError:
        raise
    except (TypeError, ValueError) as exc:
        raise InvalidRepositoryStateError(str(exc)) from exc


def _validate_publication_plan_payload(payload: Mapping[str, object]) -> None:
    raw_plan = _required_object_map(payload, "publication_plan")
    plan_digest = _required_sha256_digest(payload, "publication_plan_digest")
    plan = PublicationPlan.from_json(raw_plan)
    if plan.digest() != plan_digest:
        raise InvalidRepositoryStateError("operation journal publication_plan_digest disagrees with plan")


def _validate_finalized_replay_payload(payload: Mapping[str, object]) -> None:
    try:
        prepared = _validate_prepared_operation_payload(payload) if _has_prepared_operation_payload(payload) else None
        snapshot = WorldSnapshot.from_json(_required_object_map(payload, "snapshot"))
        snapshot_digest = _required_sha256_digest(payload, "snapshot_digest")
        if snapshot.digest() != snapshot_digest:
            raise InvalidRepositoryStateError("operation journal snapshot_digest disagrees with snapshot")
        operation_final = OperationFinalRecord(dict(_required_object_map(payload, "operation_final")))
        operation_final_digest = _required_sha256_digest(payload, "operation_final_digest")
        if operation_final.digest() != operation_final_digest:
            raise InvalidRepositoryStateError("operation journal operation_final_digest disagrees with operation_final")
        selected = dict(_required_string_map(payload, "selected"))
        candidate_commits = tuple(
            CandidateCommitRecord.from_json(dict(item)) for item in _required_object_list(payload, "candidate_commits")
        )
        candidate_outcomes = tuple(
            CandidateOutcomeRecord.from_operation_final_json(dict(item))
            for item in _required_object_list(payload, "candidate_outcomes")
        )
        candidate_refs = tuple(
            _candidate_revision_from_json(item) for item in _required_object_list(payload, "candidate_refs")
        )
        if [commit.to_json() for commit in candidate_commits] != operation_final.payload["candidate_commits"]:
            raise InvalidRepositoryStateError("operation journal candidate_commits disagree with operation_final")
        if [
            outcome.to_json(final_operation_id=_required_str(payload, "operation_id")) for outcome in candidate_outcomes
        ] != operation_final.payload["candidate_outcomes"]:
            raise InvalidRepositoryStateError("operation journal candidate_outcomes disagree with operation_final")
        if selected != operation_final.payload["selected"]:
            raise InvalidRepositoryStateError("operation journal selected heads disagree with operation_final")
        finalized = FinalizedWorldOperation(
            operation_id=_required_str(payload, "operation_id"),
            operation_kind=_required_str(payload, "operation_kind"),
            target_ref=_required_str(payload, "target_ref"),
            input_world_oid=_nullable_str(payload, "input_world_oid"),
            snapshot=snapshot,
            transition=dict(_required_object_map(payload, "transition")),
            operation_final=operation_final,
            candidate_refs=candidate_refs,
            candidate_commits=candidate_commits,
            candidate_outcomes=candidate_outcomes,
            selected=selected,
            parents=_required_string_list(payload, "parents"),
        )
        if prepared is not None:
            prepared_finalized = prepared.finalize()
            if finalized.operation_final.payload != prepared_finalized.operation_final.payload:
                raise InvalidRepositoryStateError("operation journal operation_final disagrees with prepared operation")
            if finalized.snapshot_digest != prepared_finalized.snapshot_digest:
                raise InvalidRepositoryStateError("operation journal snapshot disagrees with prepared operation")
            if dict(finalized.transition) != dict(prepared_finalized.transition):
                raise InvalidRepositoryStateError("operation journal transition disagrees with prepared operation")
            if finalized.parents != prepared_finalized.parents:
                raise InvalidRepositoryStateError("operation journal parents disagree with prepared operation")
    except InvalidRepositoryStateError:
        raise
    except (TypeError, ValueError) as exc:
        raise InvalidRepositoryStateError(str(exc)) from exc


def _candidate_revision_from_json(value: Mapping[str, object]) -> CandidateRevision:
    return CandidateRevision(
        operation_id=_required_str(value, "operation_id"),
        binding=_required_str(value, "binding"),
        candidate_id=_optional_str(value, "candidate_id") or "primary",
        store_id=_required_str(value, "store_id"),
        resource_id=_required_str(value, "resource_id"),
        head=_required_str(value, "head"),
        ref=_required_str(value, "ref"),
    )


def _optional_str(payload: Mapping[str, object], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InvalidRepositoryStateError(f"operation journal {field} must be a non-empty string when present")
    return value
