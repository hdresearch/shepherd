"""Canonical publication plan records for world authority updates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._world_types import canonical_digest

if TYPE_CHECKING:
    from collections.abc import Mapping

WORLD_PUBLICATION_PLAN_SCHEMA = "vcscore/world-publication-plan/v1"


@dataclass(frozen=True)
class PublicationPlan:
    """Deterministic plan for publishing one world to one authority ref."""

    authority_ref: str
    authority_refs: tuple[str, ...]
    world_store_id: str
    world_oid: str
    expected_oid: str | None
    input_world_oid: str | None
    allow_same_resource_alias: bool = False

    def __post_init__(self) -> None:
        for field_name, value in (
            ("authority_ref", self.authority_ref),
            ("world_store_id", self.world_store_id),
            ("world_oid", self.world_oid),
        ):
            if not value:
                raise InvalidRepositoryStateError(f"publication plan {field_name} is required")
        if not self.authority_refs:
            raise InvalidRepositoryStateError("publication plan authority_refs must include authority_ref")
        if self.authority_refs[0] != self.authority_ref:
            raise InvalidRepositoryStateError("publication plan authority_refs must start with authority_ref")
        if self.authority_refs.count(self.authority_ref) != 1:
            raise InvalidRepositoryStateError("publication plan authority_refs must include authority_ref exactly once")
        if len(set(self.authority_refs)) != len(self.authority_refs):
            raise InvalidRepositoryStateError("publication plan authority_refs must be deduplicated")
        if not all(ref for ref in self.authority_refs):
            raise InvalidRepositoryStateError("publication plan authority_refs must be non-empty strings")

    def to_json(self) -> dict[str, object]:
        payload = self._digest_payload()
        return {**payload, "publication_plan_digest": self.digest()}

    def digest(self) -> str:
        return canonical_digest(self._digest_payload())

    def _digest_payload(self) -> dict[str, object]:
        return {
            "schema": WORLD_PUBLICATION_PLAN_SCHEMA,
            "authority_ref": self.authority_ref,
            "authority_refs": list(self.authority_refs),
            "world_store_id": self.world_store_id,
            "world_oid": self.world_oid,
            "expected_oid": self.expected_oid,
            "input_world_oid": self.input_world_oid,
            "allow_same_resource_alias": self.allow_same_resource_alias,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> PublicationPlan:
        expected_keys = {
            "schema",
            "authority_ref",
            "authority_refs",
            "world_store_id",
            "world_oid",
            "expected_oid",
            "input_world_oid",
            "allow_same_resource_alias",
            "publication_plan_digest",
        }
        extra_keys = set(value) - expected_keys
        if extra_keys:
            raise InvalidRepositoryStateError(f"unexpected publication plan fields: {sorted(extra_keys)!r}")
        missing_keys = expected_keys - set(value)
        if missing_keys:
            raise InvalidRepositoryStateError(f"missing publication plan fields: {sorted(missing_keys)!r}")
        if value.get("schema") != WORLD_PUBLICATION_PLAN_SCHEMA:
            raise InvalidRepositoryStateError(f"unsupported publication plan schema: {value.get('schema')!r}")
        authority_refs = value.get("authority_refs")
        if not isinstance(authority_refs, list) or not all(isinstance(item, str) and item for item in authority_refs):
            raise InvalidRepositoryStateError("publication plan authority_refs must be a list of strings")
        allow_same_resource_alias = value.get("allow_same_resource_alias")
        if not isinstance(allow_same_resource_alias, bool):
            raise InvalidRepositoryStateError("publication plan allow_same_resource_alias must be a boolean")
        plan = cls(
            authority_ref=_required_payload_str(value, "publication plan", "authority_ref"),
            authority_refs=tuple(authority_refs),
            world_store_id=_required_payload_str(value, "publication plan", "world_store_id"),
            world_oid=_required_payload_str(value, "publication plan", "world_oid"),
            expected_oid=_optional_payload_str(value, "publication plan", "expected_oid"),
            input_world_oid=_optional_payload_str(value, "publication plan", "input_world_oid"),
            allow_same_resource_alias=allow_same_resource_alias,
        )
        if value.get("publication_plan_digest") != plan.digest():
            raise InvalidRepositoryStateError("publication plan digest disagrees with payload")
        return plan


def _required_payload_str(payload: Mapping[str, object], label: str, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidRepositoryStateError(f"{label} {key} is required")
    return value


def _optional_payload_str(payload: Mapping[str, object], label: str, key: str) -> str | None:
    if key not in payload:
        raise InvalidRepositoryStateError(f"{label} {key} is required")
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InvalidRepositoryStateError(f"{label} {key} must be null or a non-empty string")
    return value
