"""Coordinator-owned payload descriptor authority helpers."""

from __future__ import annotations

from dataclasses import dataclass

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._transition_kernel_records import (
    JSON_PAYLOAD_AUTHORITY_MODE,
    JSON_PAYLOAD_CANONICAL_MANIFEST,
    JSON_PAYLOAD_CODEC_ID,
    JSON_PAYLOAD_CODEC_VERSION,
    PayloadAuthorityMode,
    PayloadDescriptorClaim,
    ValidatedPayloadDescriptor,
)
from vcs_core._world_types import canonical_digest


@dataclass(frozen=True)
class PayloadCodecRegistration:
    """Coordinator-owned payload codec authority accepted for revision planning."""

    codec_id: str
    codec_version: str
    authority_mode: PayloadAuthorityMode
    canonical_manifest: dict[str, object]
    allows_payload_ref: bool = False


JSON_PAYLOAD_CODEC_REGISTRATION = PayloadCodecRegistration(
    codec_id=JSON_PAYLOAD_CODEC_ID,
    codec_version=JSON_PAYLOAD_CODEC_VERSION,
    authority_mode=JSON_PAYLOAD_AUTHORITY_MODE,
    canonical_manifest=dict(JSON_PAYLOAD_CANONICAL_MANIFEST),
)

_PAYLOAD_CODEC_REGISTRY: tuple[PayloadCodecRegistration, ...] = (JSON_PAYLOAD_CODEC_REGISTRATION,)


def validate_payload_descriptor_claim(
    claim: PayloadDescriptorClaim,
    *,
    payload: dict[str, object],
) -> ValidatedPayloadDescriptor:
    """Accept a registered payload descriptor claim at the coordinator boundary."""
    payload_digest = canonical_digest(payload)
    registration = _payload_codec_registration_for(claim)
    if claim.canonical_manifest != registration.canonical_manifest:
        raise InvalidRepositoryStateError("prepared candidate draft payload descriptor manifest is invalid")
    if claim.payload_ref is not None and not registration.allows_payload_ref:
        raise InvalidRepositoryStateError("prepared candidate draft payload descriptor must not carry payload_ref")
    try:
        return claim.validate(expected_payload_digest=payload_digest)
    except ValueError as exc:
        raise InvalidRepositoryStateError("prepared candidate draft payload descriptor disagrees with payload") from exc


def validate_json_payload_descriptor_claim(
    claim: PayloadDescriptorClaim,
    *,
    payload: dict[str, object],
) -> ValidatedPayloadDescriptor:
    """Accept a JSON payload descriptor claim at the coordinator boundary."""
    return validate_payload_descriptor_claim(claim, payload=payload)


def _payload_codec_registration_for(claim: PayloadDescriptorClaim) -> PayloadCodecRegistration:
    codec_matches = tuple(
        registration for registration in _PAYLOAD_CODEC_REGISTRY if registration.codec_id == claim.codec_id
    )
    if not codec_matches:
        raise InvalidRepositoryStateError("prepared candidate draft payload descriptor codec is not registered")
    version_matches = tuple(
        registration for registration in codec_matches if registration.codec_version == claim.codec_version
    )
    if not version_matches:
        raise InvalidRepositoryStateError("prepared candidate draft payload descriptor codec version is not registered")
    authority_matches = tuple(
        registration for registration in version_matches if registration.authority_mode == claim.authority_mode
    )
    if not authority_matches:
        raise InvalidRepositoryStateError("prepared candidate draft payload descriptor authority mode is invalid")
    return authority_matches[0]
