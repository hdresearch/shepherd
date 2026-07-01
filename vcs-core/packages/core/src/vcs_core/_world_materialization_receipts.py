"""Private materialization receipt storage for v2 worlds."""

from __future__ import annotations

from dataclasses import dataclass

import pygit2

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._pygit2_helpers import require_blob, require_commit
from vcs_core._world_refs import encode_ref_component
from vcs_core._world_types import MaterializationReceipt, StructuredIssue, canonical_bytes, load_canonical_json
from vcs_core.git_store import create_commit_with_recovery, create_or_update_reference, insert_tree_entry

RECEIPT_PATH = "meta/materialization-receipt.json"
RECEIPT_FAMILIES = ("open", "closed", "failed")


@dataclass(frozen=True)
class MaterializationReceiptEntry:
    """One stored materialization receipt commit."""

    oid: str
    family: str
    ref: str
    receipt: MaterializationReceipt


@dataclass(frozen=True)
class MaterializationReceiptFsckReport:
    """Validation result for one materialization receipt."""

    issues: tuple[StructuredIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


class MaterializationReceiptStore:
    """Coordinator-repo receipt store for planned v2 materialization recovery."""

    def __init__(self, repo: pygit2.Repository) -> None:
        self._repo = repo

    def write(self, receipt: MaterializationReceipt, *, family: str) -> MaterializationReceiptEntry:
        _validate_family(family)
        _validate_family_status(family, receipt.status)
        ref = materialization_receipt_ref(family, receipt.materialization_id, receipt.unit_id)
        if ref in self._repo.references:
            existing = self.read_ref(ref, family=family)
            if existing.receipt != receipt:
                raise InvalidRepositoryStateError(
                    f"materialization receipt ref already exists with different content: {ref}"
                )
            return existing
        builder = self._repo.TreeBuilder()
        meta = self._repo.TreeBuilder()
        insert_tree_entry(
            self._repo,
            meta,
            "materialization-receipt.json",
            self._repo.create_blob(canonical_bytes(receipt.to_json())),
            pygit2.GIT_FILEMODE_BLOB,
        )
        insert_tree_entry(self._repo, builder, "meta", meta.write(), pygit2.GIT_FILEMODE_TREE)
        signature = pygit2.Signature("vcs-core materialization receipt", "vcs-core@example.invalid")
        oid = create_commit_with_recovery(
            self._repo,
            None,
            signature,
            signature,
            f"materialization receipt: {receipt.materialization_id} {receipt.unit_id} {receipt.status}",
            builder.write(),
            [],
        )
        create_or_update_reference(self._repo, ref, oid)
        return MaterializationReceiptEntry(oid=str(oid), family=family, ref=ref, receipt=receipt)

    def read(self, *, family: str, materialization_id: str, unit_id: str) -> MaterializationReceiptEntry:
        _validate_family(family)
        ref = materialization_receipt_ref(family, materialization_id, unit_id)
        try:
            oid = str(self._repo.references[ref].target)
        except KeyError as exc:
            raise InvalidRepositoryStateError(f"materialization receipt ref is missing: {ref}") from exc
        return self.read_ref(ref, family=family, oid=oid)

    def read_ref(self, ref: str, *, family: str, oid: str | None = None) -> MaterializationReceiptEntry:
        _validate_family(family)
        if oid is None:
            try:
                oid = str(self._repo.references[ref].target)
            except KeyError as exc:
                raise InvalidRepositoryStateError(f"materialization receipt ref is missing: {ref}") from exc
        commit = require_commit(self._repo, pygit2.Oid(hex=oid), context="materialization receipt commit")
        receipt = _read_receipt(self._repo, commit)
        _validate_family_status(family, receipt.status)
        return MaterializationReceiptEntry(oid=str(commit.id), family=family, ref=ref, receipt=receipt)

    def fsck(self, *, family: str, materialization_id: str, unit_id: str) -> MaterializationReceiptFsckReport:
        try:
            self.read(family=family, materialization_id=materialization_id, unit_id=unit_id)
        except (InvalidRepositoryStateError, KeyError, TypeError, ValueError) as exc:
            return MaterializationReceiptFsckReport(
                (StructuredIssue(code="materialization_receipt_invalid", message=str(exc)),)
            )
        return MaterializationReceiptFsckReport(())


def materialization_receipt_ref(family: str, materialization_id: str, unit_id: str) -> str:
    return (
        "refs/vcscore/materializations/"
        f"{encode_ref_component(family)}/{encode_ref_component(materialization_id)}/{encode_ref_component(unit_id)}"
    )


def _read_receipt(repo: pygit2.Repository, commit: pygit2.Commit) -> MaterializationReceipt:
    obj: pygit2.Object = commit.tree
    for component in RECEIPT_PATH.split("/"):
        if not isinstance(obj, pygit2.Tree):
            raise TypeError(f"{RECEIPT_PATH!r} did not resolve to a blob")
        entry = obj[component]
        if component == "materialization-receipt.json":
            blob = require_blob(repo, entry.id, context="materialization receipt blob")
            payload = load_canonical_json(bytes(blob.data))
            return MaterializationReceipt.from_json(payload)
        obj = repo[entry.id]
    raise TypeError(f"{RECEIPT_PATH!r} did not resolve to a receipt")


def _validate_family(family: str) -> None:
    if family not in RECEIPT_FAMILIES:
        raise ValueError(f"unsupported materialization receipt family: {family!r}")


def _validate_family_status(family: str, status: str) -> None:
    expected = {"open": "open", "closed": "completed", "failed": "failed"}[family]
    if status != expected:
        raise InvalidRepositoryStateError(f"materialization receipt status {status!r} does not belong in {family!r}")
