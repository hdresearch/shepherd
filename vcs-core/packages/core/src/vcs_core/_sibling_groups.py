"""Internal sibling-group records used as recovery blockers.

This module preserves enough of the deferred sibling-group control-ref shape to
detect and fail closed on exploratory or partially recovered refs. It is not an
active product admission surface, and it is not the public cohort/best-of-N API.
Retained-output candidate evidence is recorded through the retained-output
selection coordinator instead.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Literal, cast

import pygit2

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._pygit2_helpers import lookup_path
from vcs_core.git_store import (
    build_tree,
    create_commit_with_recovery,
    create_or_update_reference,
    create_signature,
    set_reference_target,
)

SIBLING_GROUP_REF_PREFIX = "refs/vcscore/sibling-groups"
SIBLING_GROUP_PAYLOAD_PATH = "meta/sibling-group.json"
SIBLING_GROUP_SCHEMA_VERSION = 1

SiblingGroupStatus = Literal[
    "admitting",
    "admitted",
    "running",
    "selecting",
    "merged",
    "discarded",
    "archived",
    "failed",
]
# The in-flight subset of SiblingGroupStatus that blocks lifecycle/recovery mutations
# (the settled merged/discarded/archived states do not). Single home for this vocabulary;
# the lifecycle and recovery-inventory paths import it rather than re-declaring it.
BLOCKING_SIBLING_GROUP_STATUSES: frozenset[SiblingGroupStatus] = frozenset(
    {"admitting", "admitted", "running", "selecting", "failed"}
)
SiblingState = Literal["admitted", "running", "sealed", "cancelled", "archived", "selected", "merged"]
CarrierLeaseMode = Literal["writable_carrier", "exclusive_resource", "read_only_shared"]
CarrierLeaseState = Literal["planned", "granted", "released", "archived", "rejected"]

_SAFE_REF_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class SiblingHandleRecord:
    """Per-world machine handle decoded from a deferred sibling-group record."""

    world_id: str
    machine_scope_name: str
    display_label: str
    scope_ref: str
    parent_ref: str
    creation_oid: str
    state: SiblingState
    archive_ref: str | None = None
    operation_ids: tuple[str, ...] = ()
    carrier_refs: tuple[str, ...] = ()
    instance_id: str | None = None
    branch_scope_ref: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("world_id", self.world_id)
        validate_ref_segment(self.machine_scope_name, field_name="machine_scope_name")
        _require_non_empty("display_label", self.display_label)
        _require_non_empty("scope_ref", self.scope_ref)
        _require_non_empty("parent_ref", self.parent_ref)
        _require_non_empty("creation_oid", self.creation_oid)
        if self.scope_ref != f"refs/vcscore/scopes/{self.machine_scope_name}":
            raise ValueError("scope_ref must match machine_scope_name under refs/vcscore/scopes/.")
        if self.state not in _sibling_states():
            raise ValueError(f"Unknown sibling state: {self.state!r}.")
        _require_optional_non_empty("archive_ref", self.archive_ref)
        _require_unique_non_empty_strings("operation_ids", self.operation_ids)
        _require_unique_non_empty_strings("carrier_refs", self.carrier_refs)
        _require_optional_non_empty("instance_id", self.instance_id)
        _require_optional_non_empty("branch_scope_ref", self.branch_scope_ref)


@dataclass(frozen=True)
class CarrierLeaseRecord:
    """Carrier/resource decision decoded from a deferred sibling-group record."""

    lease_id: str
    world_id: str
    substrate: str
    target_id: str
    mode: CarrierLeaseMode
    resource_key: str
    state: CarrierLeaseState
    carrier_ref: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("lease_id", self.lease_id)
        _require_non_empty("world_id", self.world_id)
        _require_non_empty("substrate", self.substrate)
        _require_non_empty("target_id", self.target_id)
        _require_non_empty("resource_key", self.resource_key)
        if self.mode not in _carrier_lease_modes():
            raise ValueError(f"Unknown carrier lease mode: {self.mode!r}.")
        if self.state not in _carrier_lease_states():
            raise ValueError(f"Unknown carrier lease state: {self.state!r}.")
        _require_optional_non_empty("carrier_ref", self.carrier_ref)
        _require_optional_non_empty("reason", self.reason)


@dataclass(frozen=True)
class SiblingGroupRecord:
    """Deferred cohort record retained for recovery/blocker characterization."""

    group_id: str
    parent_ref: str
    parent_world_id: str
    admitted_parent_oid: str
    status: SiblingGroupStatus
    siblings: tuple[SiblingHandleRecord, ...]
    leases: tuple[CarrierLeaseRecord, ...]
    created_at: float
    updated_at: float

    def __post_init__(self) -> None:
        validate_group_id(self.group_id)
        _require_non_empty("parent_ref", self.parent_ref)
        _require_non_empty("parent_world_id", self.parent_world_id)
        _require_non_empty("admitted_parent_oid", self.admitted_parent_oid)
        _require_finite_number("created_at", self.created_at)
        _require_finite_number("updated_at", self.updated_at)
        if self.status not in _sibling_group_statuses():
            raise ValueError(f"Unknown sibling group status: {self.status!r}.")
        if len(self.siblings) < 2:
            raise ValueError("sibling groups require at least two siblings.")
        _require_unique_non_empty_strings("sibling world_ids", tuple(sibling.world_id for sibling in self.siblings))
        _require_unique_non_empty_strings(
            "sibling machine_scope_names",
            tuple(sibling.machine_scope_name for sibling in self.siblings),
        )
        _require_unique_non_empty_strings(
            "sibling display_labels",
            tuple(sibling.display_label for sibling in self.siblings),
        )
        for sibling in self.siblings:
            if sibling.parent_ref != self.parent_ref:
                raise ValueError("All sibling parent_ref values must match group parent_ref.")
            if sibling.creation_oid != self.admitted_parent_oid:
                raise ValueError("All sibling creation_oid values must match group admitted_parent_oid.")
        sibling_world_ids = {sibling.world_id for sibling in self.siblings}
        _require_unique_non_empty_strings("lease_ids", tuple(lease.lease_id for lease in self.leases))
        for lease in self.leases:
            if lease.world_id not in sibling_world_ids:
                raise ValueError(f"Lease {lease.lease_id!r} refers to unknown sibling world_id {lease.world_id!r}.")


@dataclass(frozen=True)
class SiblingGroupSnapshot:
    """One loaded sibling-group record plus its control-ref head."""

    head_oid: str
    record: SiblingGroupRecord


@dataclass(frozen=True)
class UnreadableSiblingGroup:
    """A sibling-group ref that exists but cannot be decoded safely."""

    group_id: str
    ref: str
    reason: str


@dataclass(frozen=True)
class SiblingGroupListing:
    """Current readable sibling groups plus unreadable fail-closed entries."""

    groups: tuple[SiblingGroupSnapshot, ...]
    unreadable: tuple[UnreadableSiblingGroup, ...]


def validate_group_id(group_id: str) -> None:
    validate_ref_segment(group_id, field_name="group_id")


def validate_ref_segment(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _SAFE_REF_SEGMENT.fullmatch(value):
        raise ValueError(f"{field_name} must be a non-empty lowercase ref segment without '/'.")


def sibling_group_ref(group_id: str) -> str:
    validate_group_id(group_id)
    return f"{SIBLING_GROUP_REF_PREFIX}/{group_id}"


def sibling_machine_scope_name(group_id: str, ordinal: int) -> str:
    validate_group_id(group_id)
    if isinstance(ordinal, bool) or ordinal < 0:
        raise ValueError("ordinal must be a non-negative integer.")
    suffix = group_id.removeprefix("sg-")
    return f"sib-{suffix}-{ordinal}"


def canonical_sibling_group_json(record: SiblingGroupRecord) -> bytes:
    return json.dumps(_record_to_json(record), allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_sibling_group_snapshot(repo: pygit2.Repository, group_id: str) -> SiblingGroupSnapshot | None:
    ref = sibling_group_ref(group_id)
    if ref not in repo.references:
        return None
    commit = _load_sibling_group_commit(repo, ref=ref, group_id=group_id)
    return SiblingGroupSnapshot(
        head_oid=str(commit.id),
        record=_record_from_commit(repo, commit, group_id=group_id, ref=ref),
    )


def list_sibling_groups(repo: pygit2.Repository) -> SiblingGroupListing:
    groups: list[SiblingGroupSnapshot] = []
    unreadable: list[UnreadableSiblingGroup] = []
    prefix = f"{SIBLING_GROUP_REF_PREFIX}/"
    for ref in sorted(repo.references):
        if not ref.startswith(prefix):
            continue
        group_id = ref[len(prefix) :]
        try:
            snapshot = load_sibling_group_snapshot(repo, group_id)
        except (InvalidRepositoryStateError, ValueError) as exc:
            unreadable.append(UnreadableSiblingGroup(group_id=group_id, ref=ref, reason=str(exc)))
            continue
        if snapshot is not None:
            groups.append(snapshot)
    return SiblingGroupListing(groups=tuple(groups), unreadable=tuple(unreadable))


def publish_sibling_group_snapshot(
    repo: pygit2.Repository,
    record: SiblingGroupRecord,
    *,
    expected_head_oid: str | None,
) -> bool:
    """Publish a deferred sibling-group record for recovery tests only."""
    ref = sibling_group_ref(record.group_id)
    observed_head_oid = _sibling_group_current_head(repo, record.group_id)
    if observed_head_oid != expected_head_oid:
        return observed_head_oid is not None and _current_payload_matches(repo, record)
    if observed_head_oid is not None and _current_payload_matches(repo, record):
        return True

    tree_oid = build_tree(repo, None, [(SIBLING_GROUP_PAYLOAD_PATH, canonical_sibling_group_json(record))])
    parents: list[pygit2.Oid] = []
    if observed_head_oid is not None:
        parents.append(pygit2.Oid(hex=observed_head_oid))
    sig = create_signature("sibling-group")
    commit_oid = create_commit_with_recovery(
        repo,
        None,
        sig,
        sig,
        f"sibling-group:{record.group_id}",
        tree_oid,
        parents,
    )

    current_head_oid = _sibling_group_current_head(repo, record.group_id)
    if current_head_oid != observed_head_oid:
        return current_head_oid is not None and _current_payload_matches(repo, record)
    if ref in repo.references:
        set_reference_target(repo, ref, commit_oid)
    else:
        create_or_update_reference(repo, ref, commit_oid)
    return True


def _current_payload_matches(repo: pygit2.Repository, record: SiblingGroupRecord) -> bool:
    snapshot = load_sibling_group_snapshot(repo, record.group_id)
    return snapshot is not None and canonical_sibling_group_json(snapshot.record) == canonical_sibling_group_json(
        record
    )


def _sibling_group_current_head(repo: pygit2.Repository, group_id: str) -> str | None:
    ref = sibling_group_ref(group_id)
    if ref not in repo.references:
        return None
    return str(_load_sibling_group_commit(repo, ref=ref, group_id=group_id).id)


def _load_sibling_group_commit(repo: pygit2.Repository, *, ref: str, group_id: str) -> pygit2.Commit:
    try:
        commit = repo.references[ref].peel(pygit2.Commit)
    except (KeyError, TypeError, ValueError, pygit2.GitError) as exc:
        raise InvalidRepositoryStateError(
            f"Sibling group {group_id!r} at {ref} does not point to a readable commit."
        ) from exc
    if not isinstance(commit, pygit2.Commit):
        raise InvalidRepositoryStateError(f"Sibling group {group_id!r} at {ref} does not point to a readable commit.")
    return commit


def _record_from_commit(
    repo: pygit2.Repository,
    commit: pygit2.Commit,
    *,
    group_id: str,
    ref: str,
) -> SiblingGroupRecord:
    payload = _read_json_blob(repo, commit.tree, SIBLING_GROUP_PAYLOAD_PATH)
    if not isinstance(payload, dict):
        raise InvalidRepositoryStateError(f"Sibling group {group_id!r} at {ref} is missing a readable payload.")
    try:
        record = _record_from_json(payload)
    except (TypeError, ValueError) as exc:
        raise InvalidRepositoryStateError(f"Sibling group {group_id!r} at {ref} is invalid: {exc}") from exc
    if record.group_id != group_id:
        raise InvalidRepositoryStateError(f"Sibling group {group_id!r} at {ref} reports group_id {record.group_id!r}.")
    return record


def _record_to_json(record: SiblingGroupRecord) -> dict[str, object]:
    return {
        "version": SIBLING_GROUP_SCHEMA_VERSION,
        "group_id": record.group_id,
        "parent_ref": record.parent_ref,
        "parent_world_id": record.parent_world_id,
        "admitted_parent_oid": record.admitted_parent_oid,
        "status": record.status,
        "siblings": [_sibling_to_json(sibling) for sibling in record.siblings],
        "leases": [_lease_to_json(lease) for lease in record.leases],
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _record_from_json(raw: dict[str, object]) -> SiblingGroupRecord:
    if raw.get("version") != SIBLING_GROUP_SCHEMA_VERSION:
        raise ValueError("unsupported sibling-group schema version")
    siblings_raw = raw.get("siblings")
    leases_raw = raw.get("leases")
    if not isinstance(siblings_raw, list):
        raise TypeError("siblings must be a list")
    if not isinstance(leases_raw, list):
        raise TypeError("leases must be a list")
    return SiblingGroupRecord(
        group_id=_string_field(raw, "group_id"),
        parent_ref=_string_field(raw, "parent_ref"),
        parent_world_id=_string_field(raw, "parent_world_id"),
        admitted_parent_oid=_string_field(raw, "admitted_parent_oid"),
        status=cast("SiblingGroupStatus", _string_field(raw, "status")),
        siblings=tuple(_sibling_from_json(item) for item in siblings_raw),
        leases=tuple(_lease_from_json(item) for item in leases_raw),
        created_at=_float_field(raw, "created_at"),
        updated_at=_float_field(raw, "updated_at"),
    )


def _sibling_to_json(record: SiblingHandleRecord) -> dict[str, object]:
    payload: dict[str, object] = {
        "world_id": record.world_id,
        "machine_scope_name": record.machine_scope_name,
        "display_label": record.display_label,
        "scope_ref": record.scope_ref,
        "parent_ref": record.parent_ref,
        "creation_oid": record.creation_oid,
        "state": record.state,
        "operation_ids": list(record.operation_ids),
        "carrier_refs": list(record.carrier_refs),
    }
    if record.archive_ref is not None:
        payload["archive_ref"] = record.archive_ref
    if record.instance_id is not None:
        payload["instance_id"] = record.instance_id
    if record.branch_scope_ref is not None:
        payload["branch_scope_ref"] = record.branch_scope_ref
    return payload


def _sibling_from_json(raw: object) -> SiblingHandleRecord:
    if not isinstance(raw, dict):
        raise TypeError("sibling entries must be objects")
    return SiblingHandleRecord(
        world_id=_string_field(raw, "world_id"),
        machine_scope_name=_string_field(raw, "machine_scope_name"),
        display_label=_string_field(raw, "display_label"),
        scope_ref=_string_field(raw, "scope_ref"),
        parent_ref=_string_field(raw, "parent_ref"),
        creation_oid=_string_field(raw, "creation_oid"),
        state=cast("SiblingState", _string_field(raw, "state")),
        archive_ref=_optional_string_field(raw, "archive_ref"),
        operation_ids=_string_tuple_field(raw, "operation_ids"),
        carrier_refs=_string_tuple_field(raw, "carrier_refs"),
        instance_id=_optional_string_field(raw, "instance_id"),
        branch_scope_ref=_optional_string_field(raw, "branch_scope_ref"),
    )


def _lease_to_json(record: CarrierLeaseRecord) -> dict[str, object]:
    payload: dict[str, object] = {
        "lease_id": record.lease_id,
        "world_id": record.world_id,
        "substrate": record.substrate,
        "target_id": record.target_id,
        "mode": record.mode,
        "resource_key": record.resource_key,
        "state": record.state,
    }
    if record.carrier_ref is not None:
        payload["carrier_ref"] = record.carrier_ref
    if record.reason is not None:
        payload["reason"] = record.reason
    return payload


def _lease_from_json(raw: object) -> CarrierLeaseRecord:
    if not isinstance(raw, dict):
        raise TypeError("lease entries must be objects")
    return CarrierLeaseRecord(
        lease_id=_string_field(raw, "lease_id"),
        world_id=_string_field(raw, "world_id"),
        substrate=_string_field(raw, "substrate"),
        target_id=_string_field(raw, "target_id"),
        mode=cast("CarrierLeaseMode", _string_field(raw, "mode")),
        resource_key=_string_field(raw, "resource_key"),
        state=cast("CarrierLeaseState", _string_field(raw, "state")),
        carrier_ref=_optional_string_field(raw, "carrier_ref"),
        reason=_optional_string_field(raw, "reason"),
    )


def _read_json_blob(repo: pygit2.Repository, root_tree: pygit2.Tree, path: str) -> object | None:
    obj = lookup_path(repo, root_tree, path)
    if not isinstance(obj, pygit2.Blob):
        return None
    try:
        return cast("object", json.loads(obj.data.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _string_field(raw: dict[str, object], field_name: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_string_field(raw: dict[str, object], field_name: str) -> str | None:
    value = raw.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string when present")
    return value


def _string_tuple_field(raw: dict[str, object], field_name: str) -> tuple[str, ...]:
    value = raw.get(field_name)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError(f"{field_name} must contain only non-empty strings")
        result.append(item)
    return tuple(result)


def _float_field(raw: dict[str, object], field_name: str) -> float:
    value = raw.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be numeric")
    return float(value)


def _require_non_empty(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string.")


def _require_optional_non_empty(field_name: str, value: str | None) -> None:
    if value is not None:
        _require_non_empty(field_name, value)


def _require_unique_non_empty_strings(field_name: str, values: tuple[str, ...]) -> None:
    for value in values:
        _require_non_empty(field_name, value)
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be unique.")


def _require_finite_number(field_name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number.")


def _sibling_group_statuses() -> set[str]:
    return {"admitting", "admitted", "running", "selecting", "merged", "discarded", "archived", "failed"}


def _sibling_states() -> set[str]:
    return {"admitted", "running", "sealed", "cancelled", "archived", "selected", "merged"}


def _carrier_lease_modes() -> set[str]:
    return {"writable_carrier", "exclusive_resource", "read_only_shared"}


def _carrier_lease_states() -> set[str]:
    return {"planned", "granted", "released", "archived", "rejected"}
