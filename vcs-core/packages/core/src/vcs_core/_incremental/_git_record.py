"""Durable, self-certifying commit+blob records at a ref, with compare-and-swap.

This generalizes the receipt/lease idiom (``_world_storage_manager`` retention
receipts and publication leases): a record is one parentless git commit whose tree
holds a canonical-JSON payload at ``meta/<name>.json``. The payload carries a
self-digest field computed over the digest-free payload, so a corrupt blob fails
closed on read. The ref is advanced with ``git update-ref <ref> <new> <expected>``
(CAS), mirroring world-authority ref moves in
``_world_store.WorldStore._publish_ref_unchecked``.

Shared substrate for the vcs-core incremental accelerators
(see ``260621-1730-incremental-frontier-primitive.md``).
"""

from __future__ import annotations

import subprocess
from typing import Any

import pygit2

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._pygit2_helpers import require_blob, require_commit
from vcs_core._world_types import canonical_bytes, canonical_digest, load_canonical_json
from vcs_core.git_store import create_commit_with_recovery, insert_tree_entry

_RECORD_SIGNATURE = pygit2.Signature("vcs-core incremental record", "vcs-core@example.invalid")


def with_self_digest(payload: dict[str, Any], *, digest_field: str) -> dict[str, Any]:
    """Return ``payload`` plus ``digest_field`` = canonical_digest of the digest-free payload."""
    base = {key: value for key, value in payload.items() if key != digest_field}
    return {**base, digest_field: canonical_digest(base)}


def write_record(repo: pygit2.Repository, *, meta_name: str, payload: dict[str, Any], message: str) -> str:
    """Write a parentless commit whose tree holds ``payload`` at ``meta/<meta_name>``; return its oid hex.

    The commit is written but NOT attached to any ref — the caller advances the ref
    with :func:`cas_update_ref`, so a crash between write and CAS leaves an inert
    orphan object and the prior generation remains authoritative.
    """
    meta_builder = repo.TreeBuilder()
    insert_tree_entry(
        repo, meta_builder, meta_name, repo.create_blob(canonical_bytes(payload)), pygit2.GIT_FILEMODE_BLOB
    )
    root_builder = repo.TreeBuilder()
    insert_tree_entry(repo, root_builder, "meta", meta_builder.write(), pygit2.GIT_FILEMODE_TREE)
    oid = create_commit_with_recovery(
        repo, None, _RECORD_SIGNATURE, _RECORD_SIGNATURE, message, root_builder.write(), []
    )
    return str(oid)


def read_record(
    repo: pygit2.Repository,
    ref: str,
    *,
    meta_name: str,
    schema: str,
    digest_field: str,
) -> dict[str, Any] | None:
    """Read and validate the record at ``ref``.

    Returns ``None`` when the ref is absent (the accelerator is *missing* → the
    caller falls back to the authoritative full recompute). Raises
    :class:`InvalidRepositoryStateError` when the record is present but *corrupt*
    (schema or self-digest mismatch) → fail closed, never silently fall back.
    """
    target = current_ref_target(repo, ref)
    if target is None:
        return None
    commit = require_commit(repo, pygit2.Oid(hex=target), context=f"incremental record {ref}")
    payload = load_canonical_json(_read_blob_bytes(repo, commit.tree, f"meta/{meta_name}"))
    if payload.get("schema") != schema:
        raise InvalidRepositoryStateError(f"unsupported record schema at {ref}: {payload.get('schema')!r}")
    expected = with_self_digest(payload, digest_field=digest_field)[digest_field]
    if payload.get(digest_field) != expected:
        raise InvalidRepositoryStateError(f"record self-digest disagrees with payload at {ref}")
    return payload


def cas_update_ref(repo: pygit2.Repository, ref: str, new_oid: str, *, expected_oid: str | None) -> bool:
    """``git update-ref ref new [expected]``.

    Returns ``True`` on success, ``False`` on a CAS loss (the ref no longer targets
    ``expected_oid``; ``expected_oid=None`` means "create only if absent"). Raises on
    any other failure. Mirrors ``_world_store._publish_ref_unchecked``.
    """
    if not pygit2.reference_is_valid_name(ref):
        raise InvalidRepositoryStateError(f"invalid record ref name: {ref!r}")
    cmd = ["git", "update-ref", ref, new_oid, expected_oid or ""]
    try:
        result = subprocess.run(cmd, cwd=repo.path, capture_output=True, check=False, text=True)
    except OSError as exc:
        raise InvalidRepositoryStateError(f"failed to update ref {ref!r}: {exc}") from exc
    if result.returncode == 0:
        return True
    current = current_ref_target(repo, ref)
    if expected_oid is None:
        if current is not None:
            return False
    elif current != expected_oid:
        return False
    detail = (result.stderr or result.stdout or "git update-ref failed").strip()
    raise InvalidRepositoryStateError(f"failed to update ref {ref!r}: {detail}")


def current_ref_target(repo: pygit2.Repository, ref: str) -> str | None:
    try:
        return str(repo.references[ref].target)
    except KeyError:
        return None


def _read_blob_bytes(repo: pygit2.Repository, tree: pygit2.Tree, path: str) -> bytes:
    obj: pygit2.Object = tree
    for component in path.split("/"):
        if not isinstance(obj, pygit2.Tree):
            raise InvalidRepositoryStateError(f"{path!r} did not resolve to a blob")
        try:
            obj = repo[obj[component].id]
        except KeyError as exc:
            raise InvalidRepositoryStateError(f"{path!r} missing component {component!r}") from exc
    blob = require_blob(repo, obj.id, context=path)
    return bytes(blob.data)
