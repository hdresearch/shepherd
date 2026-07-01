"""Private operation journal storage for v2 world workflows."""

from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pygit2

from vcs_core._errors import InvalidRepositoryStateError
from vcs_core._pygit2_helpers import require_commit
from vcs_core._ref_txn import RefMove, run_update_ref_stdin
from vcs_core._world_operation_protocol import (
    TERMINAL_OPERATION_STATUSES,
    validate_operation_status,
    validate_operation_status_fields,
    validate_operation_transition,
    validate_operation_transition_payload,
)
from vcs_core._world_refs import encode_ref_component, operation_journal_ref
from vcs_core._world_types import canonical_bytes, load_canonical_json
from vcs_core.git_store import create_commit_with_recovery, insert_tree_entry

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

OPERATION_JOURNAL_SCHEMA = "vcscore/operation-journal/v1"
OPERATION_JOURNAL_PATH = "meta/operation-journal.json"
OPERATION_JOURNAL_REF_FAMILIES = ("open", "closed", "archived")
_PROTECTED_APPEND_FIELDS = {
    "schema",
    "operation_id",
    "operation_kind",
    "status",
    "seq",
    "target_ref",
    "input_world_oid",
    "parent_operation_id",
    "previous_journal_oid",
    "created_at_unix_ns",
    "updated_at_unix_ns",
}


@dataclass(frozen=True)
class OperationJournalEntry:
    """One immutable operation journal state commit."""

    oid: str
    payload: dict[str, Any]
    parent_oid: str | None


@dataclass(frozen=True)
class OperationJournalHistory:
    """Validated operation journal chain from first state to current tip."""

    operation_id: str
    ref: str
    family: str
    entries: tuple[OperationJournalEntry, ...]

    @property
    def tip(self) -> OperationJournalEntry:
        return self.entries[-1]


@dataclass(frozen=True)
class OperationJournalSummary:
    """Read-only summary for one operation journal tip."""

    operation_id: str
    family: str
    ref: str
    status: str
    seq: int
    target_ref: str
    input_world_oid: str | None
    world_oid: str | None = None


class OperationJournalStore:
    """Coordinator-repo operation journal for private v2 world workflows."""

    def __init__(self, repo: pygit2.Repository) -> None:
        self._repo = repo
        self._mutation_lock = threading.RLock()

    @contextlib.contextmanager
    def mutation_transaction(self) -> Iterator[None]:
        """Hold the store's in-process mutation lock across an external prepare + co-write.

        Lets the manager run ``prepare_*`` and the atomic index co-write under the SAME in-process
        serialization the store has always provided, without the store learning about the
        accelerator. Re-entrant, so the ``prepare_*`` methods may re-acquire it freely.
        """
        with self._mutation_lock:
            yield

    def open_operation(
        self,
        *,
        operation_id: str,
        operation_kind: str,
        target_ref: str,
        input_world_oid: str | None,
        parent_operation_id: str | None = None,
        causal_links: Mapping[str, object] | None = None,
    ) -> OperationJournalEntry:
        """Create an opened operation journal under the open ref family (full commit, no accelerator)."""
        with self._mutation_lock:
            entry, moves = self.prepare_open(
                operation_id=operation_id,
                operation_kind=operation_kind,
                target_ref=target_ref,
                input_world_oid=input_world_oid,
                parent_operation_id=parent_operation_id,
                causal_links=causal_links,
            )
            _commit_moves(self._repo, moves)
            return entry

    def prepare_open(
        self,
        *,
        operation_id: str,
        operation_kind: str,
        target_ref: str,
        input_world_oid: str | None,
        parent_operation_id: str | None = None,
        causal_links: Mapping[str, object] | None = None,
    ) -> tuple[OperationJournalEntry, tuple[RefMove, ...]]:
        """Prepare an open: write the entry commit and return ``(entry, authority RefMoves)``.

        Validates, writes the opened journal entry commit, and returns the entry plus authority
        RefMoves (create the open ref). Moves NO ref — the caller commits the moves atomically (via
        :func:`_commit_moves`, or the manager's index co-write) under :meth:`mutation_transaction`.
        """
        _require_non_empty_str(operation_id, "operation_id")
        _require_non_empty_str(operation_kind, "operation_kind")
        _require_non_empty_str(target_ref, "target_ref")
        if input_world_oid is not None:
            _require_non_empty_str(input_world_oid, "input_world_oid")
        now = time.time_ns()
        payload: dict[str, Any] = {
            "schema": OPERATION_JOURNAL_SCHEMA,
            "operation_id": operation_id,
            "operation_kind": operation_kind,
            "status": "opened",
            "seq": 0,
            "target_ref": target_ref,
            "input_world_oid": input_world_oid,
            "candidate_refs": [],
            "candidate_outcomes": [],
            "selected": {},
            "created_at_unix_ns": now,
            "updated_at_unix_ns": now,
        }
        if parent_operation_id is not None:
            _require_non_empty_str(parent_operation_id, "parent_operation_id")
            payload["parent_operation_id"] = parent_operation_id
        if causal_links is not None:
            payload["causal_links"] = dict(causal_links)
        with self._mutation_lock:
            _reject_existing_operation_refs(self._repo, operation_id)
            entry = self._write_entry(payload, parent_oid=None)
            moves = (RefMove(operation_journal_ref("open", operation_id), entry.oid, expected_oid=None),)
            return entry, moves

    def append(
        self,
        operation_id: str,
        *,
        status: str,
        updates: Mapping[str, object] | None = None,
    ) -> OperationJournalEntry:
        """Append one non-terminal state to an open operation journal."""
        return self._append_to_open(operation_id, status=status, updates=updates)

    def close(
        self,
        operation_id: str,
        *,
        updates: Mapping[str, object] | None = None,
    ) -> OperationJournalEntry:
        """Append ``closed``, publish the closed ref, and remove the open ref if unchanged."""
        return self._append_terminal(operation_id, family="closed", status="closed", updates=updates)

    def archive(
        self,
        operation_id: str,
        *,
        updates: Mapping[str, object] | None = None,
    ) -> OperationJournalEntry:
        """Append ``archived``, publish the archived ref, and remove the open ref if unchanged."""
        return self._append_terminal(operation_id, family="archived", status="archived", updates=updates)

    def _append_terminal(
        self,
        operation_id: str,
        *,
        family: str,
        status: str,
        updates: Mapping[str, object] | None,
    ) -> OperationJournalEntry:
        with self._mutation_lock:
            entry, moves = self.prepare_terminal(operation_id, family=family, status=status, updates=updates)
            _commit_moves(self._repo, moves)  # ATOMIC: create terminal + delete open together
            return entry

    def prepare_terminal(
        self,
        operation_id: str,
        *,
        family: str,
        status: str,
        updates: Mapping[str, object] | None,
    ) -> tuple[OperationJournalEntry, tuple[RefMove, ...]]:
        """Prepare a terminal: write the entry commit and return ``(entry, authority RefMoves)``.

        Validates the terminal transition, writes the terminal entry commit, and returns the entry
        plus authority RefMoves (create the terminal ref, delete the open ref). Moves NO ref — the
        caller commits these two **atomically together** (all-or-none, retiring the old split-brain
        where the terminal ref was published but a delete failure left a stale open ref) under
        :meth:`mutation_transaction`, optionally co-written with the open-journal index tombstone.
        """
        with self._mutation_lock:
            history = self.read(operation_id, family="open")
            prior = history.tip
            prior_status = _required_str(prior.payload, "status")
            _validate_transition(prior_status, status)
            payload = _next_payload(prior.payload, status=status, previous_journal_oid=prior.oid, updates=updates)
            entry = self._write_entry(payload, parent_oid=prior.oid)
            moves = (
                RefMove(operation_journal_ref(family, operation_id), entry.oid, expected_oid=None),
                RefMove(operation_journal_ref("open", operation_id), None, expected_oid=prior.oid),
            )
            return entry, moves

    def prepare_cleanup_stale_open_ref(
        self, operation_id: str, *, terminal_family: str
    ) -> tuple[str | None, tuple[RefMove, ...]]:
        """Prepare deleting a stale open ref left after a terminal family ref was published.

        Returns ``(open_ref, (delete RefMove,))`` when a stale open ref exists at the terminal tip
        (or its parent), or ``(None, ())`` when there is nothing to clean up. Moves NO ref — the
        caller commits the delete atomically, co-written with the open-journal index tombstone, so
        this THIRD ``ops/open/*`` membership writer is on the co-write like open/terminal. The
        atomic transaction is itself all-or-none and surfaces a precondition failure, so no
        post-delete re-read is needed here. Raises if the open ref points at an unexpected commit.
        """
        if terminal_family not in {"closed", "archived"}:
            raise ValueError(f"terminal_family must be 'closed' or 'archived': {terminal_family!r}")
        with self._mutation_lock:
            terminal = self.read(operation_id, family=terminal_family).tip
            open_ref = operation_journal_ref("open", operation_id)
            open_target = _current_ref_target(self._repo, open_ref)
            if open_target is None:
                return None, ()
            allowed_targets = {terminal.oid}
            if terminal.parent_oid is not None:
                allowed_targets.add(terminal.parent_oid)
            if open_target not in allowed_targets:
                raise InvalidRepositoryStateError(
                    f"operation journal open ref {open_ref!r} points at an unexpected commit"
                )
            return open_ref, (RefMove(open_ref, None, expected_oid=open_target),)

    def read(self, operation_id: str, *, family: str = "open") -> OperationJournalHistory:
        """Read and validate one operation journal by operation id and ref family."""
        _validate_family(family)
        ref = operation_journal_ref(family, operation_id)
        try:
            tip_oid = str(self._repo.references[ref].target)
        except KeyError as exc:
            raise InvalidRepositoryStateError(f"operation journal ref is missing: {ref}") from exc
        return self.read_ref(ref, expected_family=family, expected_operation_id=operation_id, tip_oid=tip_oid)

    def read_ref(
        self,
        ref: str,
        *,
        expected_family: str,
        expected_operation_id: str,
        tip_oid: str | None = None,
    ) -> OperationJournalHistory:
        """Read and validate one operation journal from a concrete ref."""
        _validate_family(expected_family)
        current_oid = tip_oid
        if current_oid is None:
            try:
                current_oid = str(self._repo.references[ref].target)
            except KeyError as exc:
                raise InvalidRepositoryStateError(f"operation journal ref is missing: {ref}") from exc
        entries_descending: list[OperationJournalEntry] = []
        while current_oid is not None:
            entry = self._read_entry(current_oid)
            entries_descending.append(entry)
            current_oid = entry.parent_oid
        entries = tuple(reversed(entries_descending))
        _validate_history(entries, expected_operation_id=expected_operation_id, expected_family=expected_family)
        return OperationJournalHistory(
            operation_id=expected_operation_id,
            ref=ref,
            family=expected_family,
            entries=entries,
        )

    def list(self, *, family: str | None = None) -> tuple[OperationJournalSummary, ...]:
        """Return validated summaries for operation journals in one family or all families."""
        families = OPERATION_JOURNAL_REF_FAMILIES if family is None else (family,)
        summaries: list[OperationJournalSummary] = []
        for current_family in families:
            _validate_family(current_family)
            prefix = f"refs/vcscore/ops/{encode_ref_component(current_family)}/"
            for ref in sorted(name for name in self._repo.references if name.startswith(prefix)):
                try:
                    tip = self._read_entry(str(self._repo.references[ref].target))
                    operation_id = _required_str(tip.payload, "operation_id")
                    history = self.read_ref(
                        ref,
                        expected_family=current_family,
                        expected_operation_id=operation_id,
                        tip_oid=tip.oid,
                    )
                except (InvalidRepositoryStateError, KeyError, TypeError, ValueError):
                    continue
                summaries.append(_summary_from_history(history))
        return tuple(summaries)

    def _append_to_open(
        self,
        operation_id: str,
        *,
        status: str,
        updates: Mapping[str, object] | None,
    ) -> OperationJournalEntry:
        with self._mutation_lock:
            history = self.read(operation_id, family="open")
            prior = history.tip
            prior_status = _required_str(prior.payload, "status")
            _validate_transition(prior_status, status)
            payload = _next_payload(prior.payload, status=status, previous_journal_oid=prior.oid, updates=updates)
            entry = self._write_entry(payload, parent_oid=prior.oid)
            _cas_update_ref(self._repo, operation_journal_ref("open", operation_id), entry.oid, expected_oid=prior.oid)
            return entry

    def _write_entry(self, payload: Mapping[str, object], *, parent_oid: str | None) -> OperationJournalEntry:
        _validate_payload(payload)
        with self._mutation_lock:
            meta_builder = self._repo.TreeBuilder()
            insert_tree_entry(
                self._repo,
                meta_builder,
                "operation-journal.json",
                self._repo.create_blob(canonical_bytes(dict(payload))),
                pygit2.GIT_FILEMODE_BLOB,
            )
            root_builder = self._repo.TreeBuilder()
            insert_tree_entry(self._repo, root_builder, "meta", meta_builder.write(), pygit2.GIT_FILEMODE_TREE)
            root_tree = root_builder.write()
            signature = pygit2.Signature("vcs-core operation journal", "vcs-core@example.invalid")
            parent_oids = [] if parent_oid is None else [pygit2.Oid(hex=parent_oid)]
            oid = create_commit_with_recovery(
                self._repo,
                None,
                signature,
                signature,
                f"operation journal: {_required_str(payload, 'operation_id')} {_required_str(payload, 'status')}",
                root_tree,
                parent_oids,
            )
            return OperationJournalEntry(oid=str(oid), payload=dict(payload), parent_oid=parent_oid)

    def _read_entry(self, oid: str) -> OperationJournalEntry:
        commit = require_commit(self._repo, pygit2.Oid(hex=oid), context="operation journal commit")
        payload = load_canonical_json(_read_blob_bytes(self._repo, commit.tree, OPERATION_JOURNAL_PATH))
        _validate_payload(payload)
        parent_oids = tuple(str(parent) for parent in commit.parent_ids)
        previous_journal_oid = payload.get("previous_journal_oid")
        if not isinstance(previous_journal_oid, str):
            previous_journal_oid = None
        if previous_journal_oid is None and parent_oids:
            raise InvalidRepositoryStateError("operation journal root state must not have Git parents")
        if previous_journal_oid is not None and parent_oids != (previous_journal_oid,):
            raise InvalidRepositoryStateError("operation journal previous_journal_oid disagrees with Git parent")
        return OperationJournalEntry(oid=str(commit.id), payload=payload, parent_oid=previous_journal_oid)


def _next_payload(
    prior: Mapping[str, object],
    *,
    status: str,
    previous_journal_oid: str,
    updates: Mapping[str, object] | None,
) -> dict[str, object]:
    payload = dict(prior)
    for key in updates or {}:
        if key in _PROTECTED_APPEND_FIELDS:
            raise InvalidRepositoryStateError(f"operation journal update cannot replace protected field {key!r}")
    payload.update(dict(updates or {}))
    payload["status"] = status
    payload["seq"] = _required_int(prior, "seq") + 1
    payload["previous_journal_oid"] = previous_journal_oid
    payload["updated_at_unix_ns"] = time.time_ns()
    validate_operation_transition_payload(prior, payload)
    return payload


def _summary_from_history(history: OperationJournalHistory) -> OperationJournalSummary:
    tip = history.tip.payload
    seq = tip.get("seq")
    if not isinstance(seq, int):
        raise InvalidRepositoryStateError("operation journal seq must be an integer")
    world_oid = tip.get("world_oid")
    if world_oid is not None and not isinstance(world_oid, str):
        raise InvalidRepositoryStateError("operation journal world_oid must be a string when present")
    return OperationJournalSummary(
        operation_id=history.operation_id,
        family=history.family,
        ref=history.ref,
        status=_required_str(tip, "status"),
        seq=seq,
        target_ref=_required_str(tip, "target_ref"),
        input_world_oid=_nullable_str(tip, "input_world_oid"),
        world_oid=world_oid,
    )


def _validate_history(
    entries: tuple[OperationJournalEntry, ...],
    *,
    expected_operation_id: str,
    expected_family: str,
) -> None:
    if not entries:
        raise InvalidRepositoryStateError("operation journal history is empty")
    prior_status: str | None = None
    for index, entry in enumerate(entries):
        payload = entry.payload
        operation_id = _required_str(payload, "operation_id")
        if operation_id != expected_operation_id:
            raise InvalidRepositoryStateError("operation journal operation_id disagrees with ref")
        seq = _required_int(payload, "seq")
        if seq != index:
            raise InvalidRepositoryStateError("operation journal seq values are not contiguous")
        previous_journal_oid = payload.get("previous_journal_oid")
        if index == 0:
            if previous_journal_oid is not None:
                raise InvalidRepositoryStateError("operation journal first state has previous_journal_oid")
        elif previous_journal_oid != entries[index - 1].oid:
            raise InvalidRepositoryStateError("operation journal previous_journal_oid breaks the chain")
        status = _required_str(payload, "status")
        if prior_status is not None:
            _validate_transition(prior_status, status)
            validate_operation_transition_payload(entries[index - 1].payload, payload)
        prior_status = status
    tip_status = _required_str(entries[-1].payload, "status")
    if expected_family == "closed" and tip_status != "closed":
        raise InvalidRepositoryStateError("closed operation journal ref must point at closed status")
    if expected_family == "archived" and tip_status != "archived":
        raise InvalidRepositoryStateError("archived operation journal ref must point at archived status")
    if expected_family == "open" and tip_status in TERMINAL_OPERATION_STATUSES:
        raise InvalidRepositoryStateError("open operation journal ref must not point at a terminal status")


def _validate_payload(payload: Mapping[str, object]) -> None:
    if payload.get("schema") != OPERATION_JOURNAL_SCHEMA:
        raise InvalidRepositoryStateError(f"unsupported operation journal schema: {payload.get('schema')!r}")
    _required_str(payload, "operation_id")
    _required_str(payload, "operation_kind")
    status = _required_str(payload, "status")
    validate_operation_status(status)
    _required_int(payload, "seq")
    _required_str(payload, "target_ref")
    _nullable_str(payload, "input_world_oid")
    _required_int(payload, "created_at_unix_ns")
    _required_int(payload, "updated_at_unix_ns")
    _optional_str(payload, "parent_operation_id")
    _optional_str(payload, "previous_journal_oid")
    _optional_str(payload, "world_oid")
    _optional_str(payload, "operation_final_digest")
    _optional_str(payload, "error")
    _validate_candidate_refs(payload.get("candidate_refs", []))
    _validate_object_list(payload.get("candidate_outcomes", []), "candidate_outcomes")
    _validate_string_map(payload.get("selected", {}), "selected")
    validate_operation_status_fields(payload)


def _validate_candidate_refs(value: object) -> None:
    if not isinstance(value, list):
        raise InvalidRepositoryStateError("operation journal candidate_refs must be a list")
    for item in value:
        if not isinstance(item, dict):
            raise InvalidRepositoryStateError("operation journal candidate_refs entries must be objects")
        _required_str(item, "store_id")
        _required_str(item, "binding")
        _required_str(item, "operation_id")
        _optional_str(item, "candidate_id")
        _required_str(item, "resource_id")
        _required_str(item, "ref")
        _required_str(item, "head")


def _validate_object_list(value: object, field: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise InvalidRepositoryStateError(f"operation journal {field} must be a list of objects")


def _validate_string_map(value: object, field: str) -> None:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise InvalidRepositoryStateError(f"operation journal {field} must be a string map")


def _validate_transition(prior: str, next_status: str) -> None:
    validate_operation_transition(prior, next_status)


def _validate_family(family: str) -> None:
    if family not in OPERATION_JOURNAL_REF_FAMILIES:
        raise ValueError(f"unknown operation journal ref family: {family!r}")


def _reject_existing_operation_refs(repo: pygit2.Repository, operation_id: str) -> None:
    for family in OPERATION_JOURNAL_REF_FAMILIES:
        ref = operation_journal_ref(family, operation_id)
        if ref in repo.references:
            raise InvalidRepositoryStateError(f"operation journal already exists for operation_id {operation_id!r}")


def _required_str(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise InvalidRepositoryStateError(f"operation journal {field} is required")
    return value


def _optional_str(payload: Mapping[str, object], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InvalidRepositoryStateError(f"operation journal {field} must be a non-empty string")
    return value


def _nullable_str(payload: Mapping[str, object], field: str) -> str | None:
    if field not in payload:
        raise InvalidRepositoryStateError(f"operation journal {field} is required")
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InvalidRepositoryStateError(f"operation journal {field} must be null or a non-empty string")
    return value


def _required_int(payload: Mapping[str, object], field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidRepositoryStateError(f"operation journal {field} must be an integer")
    return value


def _require_non_empty_str(value: str, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is required")


def _read_blob_bytes(repo: pygit2.Repository, tree: pygit2.Tree, path: str) -> bytes:
    obj: pygit2.Object = tree
    for component in path.split("/"):
        if not isinstance(obj, pygit2.Tree):
            raise TypeError(f"{path!r} did not resolve to a blob")
        obj = repo[obj[component].id]
    if not isinstance(obj, pygit2.Blob):
        raise TypeError(f"{path!r} did not resolve to a blob")
    return bytes(obj.data)


def _commit_moves(repo: pygit2.Repository, moves: tuple[RefMove, ...]) -> None:
    """Commit operation-journal RefMoves as one atomic ``git update-ref --stdin`` (no accelerator).

    Under the store's mutation lock these moves are contention-free, so a rejection is a real error.
    """
    result = run_update_ref_stdin(repo, moves)
    if not result.ok:
        raise InvalidRepositoryStateError(f"operation journal ref transaction failed: {result.detail}")


def _cas_update_ref(repo: pygit2.Repository, ref: str, new_oid: str, *, expected_oid: str | None) -> None:
    if not pygit2.reference_is_valid_name(ref):
        raise InvalidRepositoryStateError(f"invalid operation journal ref name: {ref!r}")
    cmd = ["git", "update-ref", ref, new_oid, expected_oid or ""]
    result = subprocess.run(cmd, cwd=repo.path, capture_output=True, check=False, text=True)
    if result.returncode == 0:
        return
    current_target = _current_ref_target(repo, ref)
    if current_target != expected_oid:
        raise InvalidRepositoryStateError(f"operation journal ref moved before update: {ref}")
    detail = (result.stderr or result.stdout or "git update-ref failed").strip()
    raise InvalidRepositoryStateError(f"failed to update operation journal ref {ref!r}: {detail}")


def _current_ref_target(repo: pygit2.Repository, ref: str) -> str | None:
    try:
        return str(repo.references[ref].target)
    except KeyError:
        return None
