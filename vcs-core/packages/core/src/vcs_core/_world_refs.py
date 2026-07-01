"""Ref naming helpers for v2 world and substrate storage."""

from __future__ import annotations

import base64
import hashlib
import re

_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def is_ref_safe_component(component: str) -> bool:
    """Return true when ``component`` is safe as one Git ref path component."""
    return (
        bool(component)
        and _SAFE_COMPONENT_RE.fullmatch(component) is not None
        and component not in {".", ".."}
        and not component.startswith(".")
        and ".." not in component
        and "@{" not in component
        and not component.endswith(".lock")
    )


def encode_ref_component(value: str, *, max_component_length: int = 96) -> str:
    """Encode arbitrary user or resource identifiers into one safe ref component."""
    raw = value.encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    candidate = f"b64u_{encoded}"
    if is_ref_safe_component(candidate) and len(candidate) <= max_component_length:
        return candidate
    return f"sha256_{hashlib.sha256(raw).hexdigest()}"


def world_ref(world_instance_id: str) -> str:
    return f"refs/vcscore/worlds/{encode_ref_component(world_instance_id)}"


def scope_ref(scope_id: str) -> str:
    return f"refs/vcscore/scopes/{encode_ref_component(scope_id)}"


def candidate_ref(operation_id: str, binding: str, candidate_id: str = "primary") -> str:
    return (
        "refs/vcscore/candidates/"
        f"{encode_ref_component(operation_id)}/{encode_ref_component(binding)}/{encode_ref_component(candidate_id)}"
    )


def evidence_record_ref(operation_id: str, record_digest: str) -> str:
    prefix = "sha256:"
    if not record_digest.startswith(prefix):
        raise ValueError("record_digest must be a sha256 digest")
    return f"refs/vcscore/evidence/{encode_ref_component(operation_id)}/{record_digest.removeprefix(prefix)}"


def evidence_only_envelope_ref(operation_id: str, envelope_digest: str) -> str:
    prefix = "sha256:"
    if not envelope_digest.startswith(prefix):
        raise ValueError("envelope_digest must be a sha256 digest")
    return f"refs/vcscore/evidence-only/{encode_ref_component(operation_id)}/{envelope_digest.removeprefix(prefix)}"


def world_pin_ref(world_store_id: str, world_oid: str, binding: str) -> str:
    return (
        "refs/vcscore/pins/world/"
        f"{encode_ref_component(world_store_id)}/{encode_ref_component(world_oid)}/{encode_ref_component(binding)}"
    )


def child_world_retention_ref(root_world_oid: str, path: str) -> str:
    return f"refs/vcscore/retention/child-worlds/{encode_ref_component(root_world_oid)}/{encode_ref_component(path)}"


def world_publication_lease_prefix() -> str:
    return "refs/vcscore/publishing/leases"


def world_publication_lease_ref(authority_ref: str, world_oid: str, operation_id: str) -> str:
    return (
        f"{world_publication_lease_prefix()}/"
        f"{encode_ref_component(authority_ref)}/{encode_ref_component(world_oid)}/{encode_ref_component(operation_id)}"
    )


def world_publication_lease_index_ref(world_store_id: str) -> str:
    """Ref for the active-publication-lease accelerator index (one per world store)."""
    return f"refs/vcscore/publishing/lease-index/{encode_ref_component(world_store_id)}"


def world_open_operation_journal_index_ref(world_store_id: str) -> str:
    """Ref for the open-operation-journal accelerator index (one per world store).

    Lives under ``publishing/`` — deliberately NOT under ``ops/open/*`` — so the index's own ref is
    never counted by its ``rebuild_source`` scan of the open-journal family.
    """
    return f"refs/vcscore/publishing/open-journal-index/{encode_ref_component(world_store_id)}"


def world_retention_receipt_ref(authority_ref: str, world_oid: str) -> str:
    return f"refs/vcscore/retention/receipts/{encode_ref_component(authority_ref)}/{encode_ref_component(world_oid)}"


def world_fork_origin_receipt_ref(authority_ref: str) -> str:
    return f"refs/vcscore/retention/forks/{encode_ref_component(authority_ref)}"


def candidate_archive_ref(operation_id: str, binding: str, candidate_id: str = "primary") -> str:
    return (
        "refs/vcscore/archives/operations/"
        f"{encode_ref_component(operation_id)}/{encode_ref_component(binding)}/{encode_ref_component(candidate_id)}"
    )


def operation_journal_family_prefix(family: str) -> str:
    """The ref-namespace prefix for one operation-journal family (``open``/``closed``/``archived``).

    Returns the **encoded** prefix ``refs/vcscore/ops/<encoded family>/`` so callers and tests
    never hand-assemble (and silently mis-encode) a raw family prefix — the recurring source of
    vacuous ``startswith`` guards.
    """
    return f"refs/vcscore/ops/{encode_ref_component(family)}/"


def operation_journal_ref(family: str, operation_id: str) -> str:
    return f"{operation_journal_family_prefix(family)}{encode_ref_component(operation_id)}"


def is_open_operation_journal_ref(ref: str) -> bool:
    """True iff ``ref`` is a v2-shaped open operation-journal ref (``ops/<open>/<op>``, one component).

    Used to fail-closed-validate the open-journal index's entries: a present index is trusted only
    if every key is a real open ref, so a forged/corrupt index can't smuggle an arbitrary string
    onto the admission probe.
    """
    prefix = operation_journal_family_prefix("open")
    if not ref.startswith(prefix):
        return False
    remainder = ref[len(prefix) :]
    return bool(remainder) and "/" not in remainder
