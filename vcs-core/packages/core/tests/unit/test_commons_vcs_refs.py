from __future__ import annotations

import pytest
from vcs_core.profiles.commons_refs import (
    carrier_commit_ref,
    decode_ref_segment,
    encode_ref_segment,
    pending_projection_prefix,
    pending_projection_ref,
    scope_head_ref,
    workspace_tree_pin_name,
)


def test_ref_segment_encoding_is_reversible_and_ref_safe() -> None:
    value = "sha256:" + "a" * 64
    encoded = encode_ref_segment(value)

    assert encoded.startswith("utf8hex-")
    assert ":" not in encoded
    assert "/" not in encoded
    assert decode_ref_segment(encoded) == value


def test_ref_segment_encoding_rejects_empty_values() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        encode_ref_segment("")
    with pytest.raises(ValueError, match="non-empty"):
        decode_ref_segment("utf8hex-")


def test_ref_segment_decoding_rejects_unknown_encoding() -> None:
    with pytest.raises(ValueError, match="unsupported encoding"):
        decode_ref_segment("sha256/abc")


def test_vcs_core_commons_refs_encode_dynamic_segments() -> None:
    scope_id = "sha256:" + "a" * 64
    carrier_oid = "b" * 40
    commit_id = "sha256:" + "c" * 64

    refs = [
        scope_head_ref(scope_id),
        pending_projection_prefix(scope_id),
        pending_projection_ref(scope_id, carrier_oid),
        carrier_commit_ref(carrier_oid),
        workspace_tree_pin_name(commit_id),
    ]

    assert all(":" not in ref for ref in refs)
    assert scope_id not in "\n".join(refs)
    assert carrier_oid not in carrier_commit_ref(carrier_oid)
    assert commit_id not in workspace_tree_pin_name(commit_id)
