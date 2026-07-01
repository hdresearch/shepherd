"""Shared SQLite replay planning for runtime rebuild and push."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from vcs_core._upstream import UpstreamBaseAvailability
from vcs_core.types import CommitInfo, ScopeInfo

if TYPE_CHECKING:
    from vcs_core._substrate_runtime import BuiltInRuntimeBinding
    from vcs_core.recording import RecordingPipeline


_CURRENT_FRONTIER: Final = object()


@dataclass(frozen=True)
class SqlReplayEntry:
    """One replayable SQLite statement in semantic application order."""

    commit_oid: str
    commit_index: int
    carrier_scope: str
    carrier_seq: int
    basis_token: str
    sql: str
    params: object | None
    kind: str | None


@dataclass(frozen=True)
class SqlReplayPlan:
    """Resolved SQLite replay state for one target in one scope view."""

    target_id: str
    basis_token: str | None
    observed_token: str
    base_availability: UpstreamBaseAvailability
    entries: tuple[SqlReplayEntry, ...]
    frontier: str | None


def branch_commits(*, pipeline: RecordingPipeline, scope: ScopeInfo, substrate: str) -> list[CommitInfo]:
    """Return branch-local commits in causal order for the requested substrate."""
    return _branch_commits_until_frontier(
        pipeline=pipeline,
        scope=scope,
        substrate=substrate,
        visible_frontier=_CURRENT_FRONTIER,
    )


def _branch_commits_until_frontier(
    *,
    pipeline: RecordingPipeline,
    scope: ScopeInfo,
    substrate: str,
    visible_frontier: object,
) -> list[CommitInfo]:
    """Return branch-local commits in causal order, optionally bounded by a visible frontier."""
    if scope.name == "ground":
        commits = [
            commit
            for commit in pipeline.store.walk_pending(max_count=10_000)
            if commit.metadata.get("substrate") == substrate
        ]
        return _bounded_commits(
            commits,
            visible_frontier=visible_frontier,
            scope=scope,
        )

    commits = pipeline.store.log(ref=scope.ref, max_count=10_000)
    branch_local: list[CommitInfo] = []
    for commit in commits:
        if commit.oid == scope.creation_oid:
            break
        if commit.metadata.get("substrate") != substrate:
            continue
        branch_local.append(commit)
    branch_local.reverse()
    return _bounded_commits(
        branch_local,
        visible_frontier=visible_frontier,
        scope=scope,
    )


def _bounded_commits(
    commits: list[CommitInfo],
    *,
    visible_frontier: object,
    scope: ScopeInfo,
) -> list[CommitInfo]:
    if visible_frontier is _CURRENT_FRONTIER:
        return commits
    if visible_frontier is None:
        return []
    if not isinstance(visible_frontier, str):
        raise RuntimeError(f"SQLite replay frontier for {scope.name!r} must be a string or None.")  # noqa: TRY004

    bounded: list[CommitInfo] = []
    for commit in commits:
        bounded.append(commit)
        if commit.oid == visible_frontier:
            return bounded

    raise RuntimeError(f"SQLite replay frontier {visible_frontier!r} is not visible from scope {scope.name!r}.")


def fork_marker_for_scope(
    *,
    pipeline: RecordingPipeline,
    scope: ScopeInfo,
    substrate: str,
    target_id: str,
) -> dict[str, object] | None:
    """Return the latest durable carrier fork marker for a scope, if present."""
    for commit in reversed(branch_commits(pipeline=pipeline, scope=scope, substrate=substrate)):
        meta = commit.metadata
        if meta.get("type") != "SqlCarrierForked":
            continue
        if meta.get("target_id") != target_id:
            continue
        if meta.get("child_carrier_scope") != scope.name:
            continue
        return meta
    return None


def _restore_scope_from_marker(
    *,
    pipeline: RecordingPipeline,
    scope_queries: BuiltInRuntimeBinding,
    parent_scope_name: str,
    marker: dict[str, object],
) -> ScopeInfo | None:
    restored = scope_queries.lookup_scope(parent_scope_name)
    if restored is not None:
        return restored

    ref = marker.get("parent_scope_ref")
    if not isinstance(ref, str) or not ref:
        ref = f"refs/vcscore/scopes/{parent_scope_name}"
    if not pipeline.store.ref_exists(ref):
        return None

    creation_oid = marker.get("parent_creation_oid")
    if creation_oid is not None and not isinstance(creation_oid, str):
        raise RuntimeError(f"SQLite fork marker for {parent_scope_name!r} has non-string parent_creation_oid.")

    return ScopeInfo(
        name=parent_scope_name,
        ref=ref,
        instance_id=f"replay:{parent_scope_name}",
        creation_oid=creation_oid or "",
    )


def build_sql_replay_plan(
    *,
    pipeline: RecordingPipeline,
    scope_queries: BuiltInRuntimeBinding,
    scope: ScopeInfo,
    substrate: str,
    target_id: str,
    observed_token: str,
    visible_frontier: object = _CURRENT_FRONTIER,
) -> SqlReplayPlan:
    """Build the authoritative replay plan for one SQLite target and scope."""
    entries = _with_replay_indexes(
        _visible_entries_for_scope(
            pipeline=pipeline,
            scope_queries=scope_queries,
            scope=scope,
            substrate=substrate,
            target_id=target_id,
            visible_frontier=visible_frontier,
        )
    )

    basis_token = _resolve_basis_token(
        entries=entries,
        pipeline=pipeline,
        scope=scope,
        substrate=substrate,
        target_id=target_id,
        observed_token=observed_token,
    )
    base_availability = resolve_base_availability(
        substrate=substrate,
        target_id=target_id,
        basis_token=basis_token,
        observed_token=observed_token,
    )
    frontier = entries[-1].commit_oid if entries else None
    return SqlReplayPlan(
        target_id=target_id,
        basis_token=basis_token,
        observed_token=observed_token,
        base_availability=base_availability,
        entries=tuple(entries),
        frontier=frontier,
    )


def _visible_entries_for_scope(
    *,
    pipeline: RecordingPipeline,
    scope_queries: BuiltInRuntimeBinding,
    scope: ScopeInfo,
    substrate: str,
    target_id: str,
    visible_frontier: object,
) -> list[SqlReplayEntry]:
    if visible_frontier is None:
        return []

    inherited: list[SqlReplayEntry] = []
    marker = fork_marker_for_scope(
        pipeline=pipeline,
        scope=scope,
        substrate=substrate,
        target_id=target_id,
    )
    if marker is not None:
        parent_scope_name = marker.get("parent_carrier_scope")
        if not isinstance(parent_scope_name, str):
            raise RuntimeError(f"SQLite fork marker for {scope.name!r} is missing parent_carrier_scope.")
        parent_scope = _restore_scope_from_marker(
            pipeline=pipeline,
            scope_queries=scope_queries,
            parent_scope_name=parent_scope_name,
            marker=marker,
        )
        if parent_scope is None:
            raise RuntimeError(
                "SQLite carrier lineage for "
                f"{scope.name!r} depends on unavailable parent carrier scope {parent_scope_name!r}."
            )

        if "parent_visible_frontier" in marker:
            parent_visible_frontier = marker.get("parent_visible_frontier")
            if parent_visible_frontier is not None and not isinstance(parent_visible_frontier, str):
                raise RuntimeError(f"SQLite fork marker for {scope.name!r} has non-string parent_visible_frontier.")
            inherited = _visible_entries_for_scope(
                pipeline=pipeline,
                scope_queries=scope_queries,
                scope=parent_scope,
                substrate=substrate,
                target_id=target_id,
                visible_frontier=parent_visible_frontier,
            )
        else:
            base_seq = marker.get("base_seq")
            if not isinstance(base_seq, int):
                raise RuntimeError(f"SQLite fork marker for {scope.name!r} is missing base_seq.")
            inherited = _statement_entries_from_commits(
                branch_commits(pipeline=pipeline, scope=parent_scope, substrate=substrate),
                target_id=target_id,
                allowed_carriers={parent_scope.name},
                cutoff_by_carrier={parent_scope.name: base_seq},
            )

    local_entries = _statement_entries_from_commits(
        _branch_commits_until_frontier(
            pipeline=pipeline,
            scope=scope,
            substrate=substrate,
            visible_frontier=visible_frontier,
        ),
        target_id=target_id,
    )
    return [*inherited, *local_entries]


def _with_replay_indexes(entries: list[SqlReplayEntry]) -> list[SqlReplayEntry]:
    return [_replace_entry_index(entry, index) for index, entry in enumerate(entries)]


def _replace_entry_index(entry: SqlReplayEntry, commit_index: int) -> SqlReplayEntry:
    return SqlReplayEntry(
        commit_oid=entry.commit_oid,
        commit_index=commit_index,
        carrier_scope=entry.carrier_scope,
        carrier_seq=entry.carrier_seq,
        basis_token=entry.basis_token,
        sql=entry.sql,
        params=entry.params,
        kind=entry.kind,
    )


def _statement_entries_from_commits(
    commits: list[CommitInfo],
    *,
    target_id: str,
    allowed_carriers: set[str] | None = None,
    cutoff_by_carrier: dict[str, int] | None = None,
) -> list[SqlReplayEntry]:
    entries: list[SqlReplayEntry] = []
    for commit in commits:
        meta = commit.metadata
        if meta.get("type") != "SqlStatementBuffered":
            continue
        if meta.get("target_id") != target_id:
            continue

        carrier_scope = meta.get("carrier_scope")
        carrier_seq = meta.get("carrier_seq")
        basis_token = meta.get("basis_token")
        sql = meta.get("sql")

        if not isinstance(carrier_scope, str):
            raise RuntimeError(f"Pending SQLite commit {commit.oid[:12]} is missing carrier_scope.")  # noqa: TRY004
        if allowed_carriers is not None and carrier_scope not in allowed_carriers:
            continue
        if not isinstance(carrier_seq, int):
            raise RuntimeError(  # noqa: TRY004
                f"SQLite carrier lineage requires integer carrier_seq metadata for carrier {carrier_scope!r}."
            )
        if cutoff_by_carrier is not None:
            cutoff = cutoff_by_carrier.get(carrier_scope)
            if cutoff is not None and carrier_seq > cutoff:
                continue
        if not isinstance(basis_token, str):
            raise RuntimeError(f"Pending SQLite commit {commit.oid[:12]} is missing basis_token.")  # noqa: TRY004
        if not isinstance(sql, str):
            raise RuntimeError(f"Buffered SQLite statement for {carrier_scope!r} is missing SQL text.")  # noqa: TRY004

        raw_kind = meta.get("kind")
        entries.append(
            SqlReplayEntry(
                commit_oid=commit.oid,
                commit_index=0,
                carrier_scope=carrier_scope,
                carrier_seq=carrier_seq,
                basis_token=basis_token,
                sql=sql,
                params=meta.get("params"),
                kind=raw_kind if isinstance(raw_kind, str) else None,
            )
        )
    return entries


def _resolve_basis_token(
    *,
    entries: list[SqlReplayEntry],
    pipeline: RecordingPipeline,
    scope: ScopeInfo,
    substrate: str,
    target_id: str,
    observed_token: str,
) -> str:
    basis_tokens = {entry.basis_token for entry in entries}
    if basis_tokens:
        if len(basis_tokens) != 1:
            raise RuntimeError(
                "SQLite replay requires exactly one basis_token per visible target. "
                f"Got {len(basis_tokens)} for {target_id!r}."
            )
        return next(iter(basis_tokens))

    marker = fork_marker_for_scope(
        pipeline=pipeline,
        scope=scope,
        substrate=substrate,
        target_id=target_id,
    )
    if marker is not None:
        basis_token = marker.get("basis_token")
        if not isinstance(basis_token, str):
            raise RuntimeError(f"SQLite fork marker for {scope.name!r} is missing basis_token.")
        return basis_token

    return observed_token


def resolve_base_availability(
    *,
    substrate: str,
    target_id: str,
    basis_token: str | None,
    observed_token: str,
) -> UpstreamBaseAvailability:
    if basis_token is not None and basis_token == observed_token:
        return UpstreamBaseAvailability(
            substrate=substrate,
            target_id=target_id,
            basis_token=basis_token,
            base_available=True,
            source="live-upstream",
        )
    return UpstreamBaseAvailability(
        substrate=substrate,
        target_id=target_id,
        basis_token=basis_token,
        base_available=False,
        source="none",
    )
