"""Canonical digest helpers for kernel v2 records and witnesses."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal, TypeAlias

CanonicalJson: TypeAlias = Mapping[str, Any]
RecordMode: TypeAlias = Literal["capture", "declaration"]
Containment: TypeAlias = Literal["full", "contained", "buffered", "uncontained"]

CANONICAL_VERSION = "shepherd.kernel.canonical.v2"
ABI_VERSION = "shepherd.kernel.abi.v0"
CANONICAL_PREFIX = f"{CANONICAL_VERSION}\n".encode("ascii")
ROOT_WITNESS_SCHEMA_REF = "kernel.witness.root.v1"
WITNESS_SCHEMA_REF = "kernel.witness.v1"
ROOT_WITNESS_REF = ""


def canonical_json_bytes(value: CanonicalJson) -> bytes:
    """Return byte-stable canonical JSON for digest input."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_digest(value: CanonicalJson) -> str:
    """Return the kernel digest for a canonical input payload."""
    digest = hashlib.sha256(CANONICAL_PREFIX + canonical_json_bytes(value)).hexdigest()
    return f"sha256:{digest}"


def canonical_record_input(
    *,
    schema_ref: str,
    mode: RecordMode,
    body: Mapping[str, Any],
    caused_by: tuple[str, ...] = (),
    witness: str,
) -> dict[str, Any]:
    """Build the canonical payload for one retained record."""
    _validate_schema_ref(schema_ref)
    _validate_mode(mode)
    _validate_witness_ref(witness)
    _validate_root_witness_record(
        schema_ref=schema_ref,
        mode=mode,
        body=body,
        caused_by=caused_by,
        witness=witness,
    )
    return {
        "body": dict(body),
        "caused_by": list(caused_by),
        "kind": "record",
        "mode": mode,
        "schema_ref": schema_ref,
        "witness": witness,
    }


def record_digest(
    *,
    schema_ref: str,
    mode: RecordMode,
    body: Mapping[str, Any],
    caused_by: tuple[str, ...] = (),
    witness: str,
) -> str:
    """Return the kernel record id for one retained record."""
    return canonical_digest(
        canonical_record_input(
            schema_ref=schema_ref,
            mode=mode,
            body=body,
            caused_by=caused_by,
            witness=witness,
        )
    )


def canonical_witness_input(*, schema_ref: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Build the canonical payload for one witness."""
    validate_witness_body(schema_ref=schema_ref, body=body)
    return {
        "body": dict(body),
        "kind": "witness",
        "schema_ref": schema_ref,
    }


def validate_witness_body(*, schema_ref: str, body: Mapping[str, Any]) -> None:
    """Validate witness-body shape without constructing digest input."""
    _validate_schema_ref(schema_ref)
    _validate_witness_body(schema_ref, body)


def witness_body_digest(*, schema_ref: str, body: Mapping[str, Any]) -> str:
    """Return a digest for canonical witness-body input.

    Kernel records cite retained witness record ids, not this body digest.
    """
    return canonical_digest(canonical_witness_input(schema_ref=schema_ref, body=body))


def root_witness_body() -> dict[str, Any]:
    """Return the fixed root witness body."""
    return {
        "active_binding_refs": [],
        "actor_ref": "kernel:root",
        "authority_refs": [],
        "containment": "full",
        "provenance_policy_refs": [],
        "semantic_environment_refs": [],
        "substrate_ref": "kernel",
        "visibility_policy_refs": [],
    }


def root_witness_body_digest() -> str:
    """Return the deterministic root witness-body digest."""
    return witness_body_digest(schema_ref=ROOT_WITNESS_SCHEMA_REF, body=root_witness_body())


def root_witness_record_id() -> str:
    """Return the deterministic retained root witness record id."""
    return record_digest(
        schema_ref=ROOT_WITNESS_SCHEMA_REF,
        mode="capture",
        body=root_witness_body(),
        witness=ROOT_WITNESS_REF,
    )


def _validate_schema_ref(schema_ref: str) -> None:
    if not schema_ref:
        raise ValueError("schema_ref is required")


def _validate_mode(mode: str) -> None:
    if mode not in {"capture", "declaration"}:
        raise ValueError("mode must be 'capture' or 'declaration'")


def _validate_witness_ref(witness: str) -> None:
    if witness == ROOT_WITNESS_REF:
        return
    if not witness.startswith("sha256:"):
        raise ValueError("witness must be a sha256 digest or the root sentinel")


def _validate_root_witness_record(
    *,
    schema_ref: str,
    mode: str,
    body: Mapping[str, Any],
    caused_by: tuple[str, ...],
    witness: str,
) -> None:
    if witness == ROOT_WITNESS_REF:
        if schema_ref != ROOT_WITNESS_SCHEMA_REF:
            raise ValueError("empty witness sentinel is legal only for the root witness record")
        if mode != "capture":
            raise ValueError("root witness record mode must be 'capture'")
        if caused_by:
            raise ValueError("root witness record cannot have causal parents")
        if dict(body) != root_witness_body():
            raise ValueError("root witness record body must equal root_witness_body()")
        return
    if schema_ref == ROOT_WITNESS_SCHEMA_REF:
        raise ValueError("root witness record must use the empty witness sentinel")


def _validate_witness_body(schema_ref: str, body: Mapping[str, Any]) -> None:
    if schema_ref not in {ROOT_WITNESS_SCHEMA_REF, WITNESS_SCHEMA_REF}:
        return
    missing = {
        "actor_ref",
        "authority_refs",
        "active_binding_refs",
        "semantic_environment_refs",
        "visibility_policy_refs",
        "provenance_policy_refs",
        "substrate_ref",
        "containment",
    } - set(body)
    if missing:
        raise ValueError(f"witness body missing required fields: {', '.join(sorted(missing))}")
    if body["containment"] not in {"full", "contained", "buffered", "uncontained"}:
        raise ValueError("witness containment is invalid")
    if not body["substrate_ref"]:
        raise ValueError("witness substrate_ref is required")
