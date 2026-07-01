"""Vcs-core commons-vcs ref namespace helpers."""

from __future__ import annotations

_ENCODED_SEGMENT_PREFIX = "utf8hex-"


def encode_ref_segment(value: str) -> str:
    """Encode one dynamic value as a reversible Git-ref-safe segment."""
    if not value:
        raise ValueError("commons ref segment value must be non-empty")
    return _ENCODED_SEGMENT_PREFIX + value.encode("utf-8").hex()


def decode_ref_segment(segment: str) -> str:
    """Decode a segment produced by `encode_ref_segment`."""
    if not segment.startswith(_ENCODED_SEGMENT_PREFIX):
        raise ValueError(f"commons ref segment has unsupported encoding: {segment!r}")
    encoded = segment[len(_ENCODED_SEGMENT_PREFIX) :]
    if not encoded:
        raise ValueError("commons ref segment payload must be non-empty")
    try:
        raw = bytes.fromhex(encoded)
    except ValueError as exc:
        raise ValueError(f"commons ref segment has invalid hex payload: {segment!r}") from exc
    value = raw.decode("utf-8")
    if not value:
        raise ValueError("commons ref segment decoded to an empty value")
    return value


def scope_head_ref(scope_id: str) -> str:
    """Commons backend ref for one committed vcs-core scope head."""
    return f"vcscore/scopes/{encode_ref_segment(scope_id)}/head"


def pending_projection_prefix(scope_id: str) -> str:
    """Commons backend ref prefix for in-flight projections for one scope."""
    return f"vcscore/scopes/{encode_ref_segment(scope_id)}/pending/"


def pending_projection_ref(scope_id: str, carrier_oid: str) -> str:
    """Commons backend ref for one in-flight carrier projection."""
    return f"{pending_projection_prefix(scope_id)}{encode_ref_segment(carrier_oid)}"


def carrier_commit_ref(carrier_oid: str) -> str:
    """Commons backend ref mapping a Store carrier commit to its commons commit."""
    return f"vcscore/carriers/{encode_ref_segment(carrier_oid)}"


def workspace_tree_pin_name(commit_id: str) -> str:
    """Commons backend pin name for the Git workspace tree cited by a commons commit."""
    return f"vcscore/workspace-trees/{encode_ref_segment(commit_id)}"
