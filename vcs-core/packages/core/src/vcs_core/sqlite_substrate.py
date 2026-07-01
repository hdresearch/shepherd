"""SQLite substrate: explicit exec-path buffered SQL with replayable push."""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from vcs_core._errors import SubstrateCommandError
from vcs_core._sqlite_replay import (
    SqlReplayEntry,
    SqlReplayPlan,
    build_sql_replay_plan,
    fork_marker_for_scope,
    resolve_base_availability,
)
from vcs_core._substrate_runtime import (
    BuiltInRuntimeBinding,
    BuiltInSubstrateContext,
    PythonPatch,
    bootstrap_builtin_runtime,
)
from vcs_core._upstream import PreflightResult, PreflightStatus, UpstreamBaseAvailability
from vcs_core.authority import SubstrateAuthority, make_authority_aspect
from vcs_core.materialization import VerificationResult
from vcs_core.spi import (
    CapabilitySet,
    CommandRequest,
    CommandSpec,
    DriverContext,
    DriverIngressResult,
    DriverSchema,
    IngressRequest,
    ParamSpec,
    UnsupportedRequestError,
)
from vcs_core.types import EffectRecord, ScopeInfo

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from vcs_core.materialization import InternalMaterializer, MaterializationUnit
    from vcs_core.types import CommitInfo, DiffSummary, Status


_LEADING_SQL_JUNK_RE = re.compile(
    r"""
    \A
    (?:
        \s+
      | --[^\n]*(?:\n|\Z)
      | /\*.*?\*/
    )*
    """,
    re.DOTALL | re.VERBOSE,
)
_WITH_TERMINATOR_RE = re.compile(r"\)\s*(SELECT|INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE | re.DOTALL)
_PRAGMA_RE = re.compile(
    r"""
    \APRAGMA
    \s+
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    (?:
        \s*\((?P<args>[^)]*)\)
      | \s*=\s*(?P<assignment>.+)
    )?
    \s*\Z
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)
_EXPLAIN_SELECT_RE = re.compile(r"\AEXPLAIN(?:\s+QUERY\s+PLAN)?\s+SELECT\b", re.IGNORECASE | re.DOTALL)
_REJECT_PREFIXES = ("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "ATTACH", "DETACH", "VACUUM")
_FORBIDDEN_CREATE_RE = re.compile(
    r"\ACREATE\s+(?:TEMP|TEMPORARY|VIRTUAL\s+TABLE|TRIGGER|VIEW)\b",
    re.IGNORECASE | re.DOTALL,
)
_FORBIDDEN_DROP_RE = re.compile(r"\ADROP\s+(?:TRIGGER|VIEW)\b", re.IGNORECASE | re.DOTALL)
_READ_ONLY_PRAGMAS = frozenset({"table_info"})


@dataclass(frozen=True)
class _CarrierState:
    scope: ScopeInfo
    runtime_path: Path
    basis_token: str
    next_seq: int = 0


def _coerce_sql_params(
    params: object,
) -> tuple[object, ...] | list[object] | dict[str, object]:
    if isinstance(params, tuple | list):
        return params
    if isinstance(params, dict) and all(isinstance(key, str) for key in params):
        return params
    raise RuntimeError("SQLite replay intent params must be a sequence or str-keyed mapping.")


class _SQLiteMaterializer:
    def __init__(self, substrate: SQLiteSubstrate) -> None:
        self._substrate = substrate
        self.materializer_key = substrate.materializer_key

    def collect_units(
        self,
        *,
        pending_commits: Sequence[CommitInfo],
        diff: DiffSummary,
        status: Status,
    ) -> tuple[MaterializationUnit, ...]:
        del diff, status
        if not any(
            commit.metadata.get("substrate") == "sqlite" and commit.metadata.get("type") == "SqlStatementBuffered"
            for commit in pending_commits
        ):
            return ()

        from vcs_core.materialization import MaterializationUnit, _semantic_pending_commit_indices

        index_by_oid = _semantic_pending_commit_indices(pending_commits)
        plan = self._substrate.build_pending_replay_plan()
        if not plan.entries:
            return ()
        first_commit_index = min(index_by_oid[entry.commit_oid] for entry in plan.entries)
        intents = tuple(
            {
                "sql": entry.sql,
                "params": entry.params,
                "kind": entry.kind,
                "carrier_scope": entry.carrier_scope,
                "carrier_seq": entry.carrier_seq,
                "target_id": plan.target_id,
                "commit_oid": entry.commit_oid,
            }
            for entry in plan.entries
        )
        return (
            MaterializationUnit(
                unit_id=f"sqlite:{plan.target_id}",
                materializer_key=self.materializer_key,
                substrate="sqlite",
                target_id=plan.target_id,
                reversibility="auto",
                commit_index=first_commit_index,
                upstream_aware=True,
                basis_token=plan.basis_token,
                frontier=plan.frontier,
                intents=intents,
            ),
        )

    def preflight_units(
        self,
        units: Sequence[MaterializationUnit],
        *,
        mode: str = "pure",
    ) -> dict[str, PreflightResult]:
        results: dict[str, PreflightResult] = {}
        for unit in units:
            plan = self._substrate.build_pending_replay_plan()
            current_token = plan.observed_token
            base_availability = plan.base_availability
            if unit.basis_token == current_token:
                results[unit.unit_id] = PreflightResult(
                    status="ready",
                    observed_token=current_token,
                    base_availability=base_availability,
                )
            elif not self._substrate.db_path.exists():
                reason = "required SQLite basis is no longer available from live upstream"
                recorded = self._substrate.latest_reconcile_preflight(plan) if mode == "recording" else None
                if recorded is not None and recorded.status == "unsupported":
                    results[unit.unit_id] = recorded
                elif mode == "recording":
                    results[unit.unit_id] = self._substrate.record_reconcile_outcome(
                        plan=plan,
                        outcome="unsupported",
                        reason=reason,
                    )
                else:
                    results[unit.unit_id] = PreflightResult(
                        status="unsupported",
                        reason=reason,
                        observed_token=current_token,
                        base_availability=base_availability,
                    )
            else:
                reason = "live SQLite basis token no longer matches pending work"
                recorded = self._substrate.latest_reconcile_preflight(plan) if mode == "recording" else None
                if recorded is not None and recorded.status == "conflicted":
                    results[unit.unit_id] = recorded
                elif mode == "recording":
                    results[unit.unit_id] = self._substrate.record_reconcile_outcome(
                        plan=plan,
                        outcome="conflicted",
                        reason=reason,
                    )
                else:
                    results[unit.unit_id] = PreflightResult(
                        status="conflicted",
                        reason=reason,
                        observed_token=current_token,
                        base_availability=base_availability,
                    )
        return results

    def prepare_run_artifacts(
        self,
        units: Sequence[MaterializationUnit],
        *,
        run_directory: Path,
    ) -> dict[str, dict[str, object]]:
        state: dict[str, dict[str, object]] = {}
        with self._substrate._control_plane_guard():
            run_directory.mkdir(parents=True, exist_ok=True)
            for unit in units:
                snapshot_name = f"{hashlib.sha256(unit.unit_id.encode('utf-8')).hexdigest()[:16]}.db"
                snapshot_path = run_directory / snapshot_name
                self._substrate._snapshot_database(self._substrate.db_path, snapshot_path)
                state[unit.unit_id] = {
                    "base_snapshot": snapshot_name,
                    "basis_token": unit.basis_token,
                }
        return state

    def verify_units(
        self,
        units: Sequence[MaterializationUnit],
        *,
        run_state: Mapping[str, Mapping[str, object]],
        run_directory: Path,
    ) -> dict[str, VerificationResult]:
        results: dict[str, VerificationResult] = {}
        for unit in units:
            unit_state = dict(run_state.get(unit.unit_id, {}))
            snapshot_name = unit_state.get("base_snapshot")
            if not isinstance(snapshot_name, str):
                results[unit.unit_id] = VerificationResult(
                    ok=False,
                    reason="missing preserved SQLite base snapshot for verification",
                )
                continue
            snapshot_path = run_directory / snapshot_name
            if not snapshot_path.exists():
                results[unit.unit_id] = VerificationResult(
                    ok=False,
                    reason="preserved SQLite base snapshot is unavailable",
                )
                continue
            with (
                self._substrate._control_plane_guard(),
                tempfile.TemporaryDirectory(prefix="vcscore-sqlite-verify-") as tmpdir,
            ):
                scratch_path = Path(tmpdir) / "verify.db"
                self._substrate._snapshot_database(snapshot_path, scratch_path)
                self._substrate._apply_unit_intents(scratch_path, unit.intents)
                if self._substrate._database_signature(scratch_path) == self._substrate._database_signature(
                    self._substrate.db_path
                ):
                    results[unit.unit_id] = VerificationResult(ok=True)
                else:
                    results[unit.unit_id] = VerificationResult(
                        ok=False,
                        reason="real SQLite DB diverged from expected materialized replay",
                    )
        return results

    def apply_units(self, units: Sequence[MaterializationUnit]) -> None:
        db_path = self._substrate.db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        intents = tuple(intent for unit in units for intent in unit.intents)
        self._substrate._apply_unit_intents(db_path, intents)


class SQLiteSubstrate:
    """Explicit exec-path SQLite substrate with buffered replay."""

    name = "sqlite"
    binding = "sqlite"
    role = "sqlite"
    driver_id = "sqlite"
    driver_version = "v1"
    _SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(accepts=frozenset({CommandRequest}), selectable=False)

    def describe(self) -> DriverSchema:
        return DriverSchema(
            driver_id=self.driver_id,
            driver_version=self.driver_version,
            capabilities=self.capabilities,
            commands=self.commands,
        )

    @property
    def commands(self) -> dict[str, CommandSpec]:
        return {
            "query": CommandSpec(
                description="Execute a read-only SQLite query against the buffered runtime view.",
                params={
                    "sql": ParamSpec(type="str", description="SQL query to execute."),
                    "params": ParamSpec(type="object", required=False, description="Optional SQLite parameters."),
                },
            ),
            "execute": CommandSpec(
                description="Execute a mutating SQLite statement against the buffered runtime view.",
                params={
                    "sql": ParamSpec(type="str", description="SQL statement to execute."),
                    "params": ParamSpec(type="object", required=False, description="Optional SQLite parameters."),
                },
            ),
        }

    def __init__(
        self,
        ctx: BuiltInSubstrateContext,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        runtime, workspace = bootstrap_builtin_runtime(ctx)
        self.bind_runtime(runtime)
        self._workspace = workspace
        config = ctx.config
        configured_db = db_path or config.get("path")
        if configured_db is None:
            self._db_path = self._workspace / "sqlite.db"
        else:
            candidate = Path(str(configured_db))
            if not candidate.is_absolute():
                candidate = self._workspace / candidate
            self._db_path = candidate.resolve(strict=False)
        configured_runtime_root = config.get("runtime_root")
        if configured_runtime_root is None:
            self._runtime_root = self._workspace / ".vcscore" / "runtime" / "sqlite"
        else:
            candidate = Path(str(configured_runtime_root))
            if not candidate.is_absolute():
                candidate = self._workspace / candidate
            self._runtime_root = candidate.resolve(strict=False)
        self._carriers: dict[str, _CarrierState] = {}
        self._carrier_lineage: dict[str, tuple[str, int]] = {}
        self._target_id = f"sqlite:{self._db_path}"
        self._materializer_key = f"builtin:sqlite:{hashlib.sha256(self._target_id.encode('utf-8')).hexdigest()[:12]}"

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def materializer_key(self) -> str:
        return self._materializer_key

    def bind_runtime(self, binding: BuiltInRuntimeBinding) -> None:
        self._runtime = binding
        self._pipeline = binding.pipeline

    def _control_plane_guard(self) -> Any:
        return self._runtime.control_plane_guard()

    def activate(self) -> None:
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        self._register_db_claims(self._db_path, "exclusive")

    def deactivate(self) -> None:
        self._carriers.clear()
        self._carrier_lineage.clear()

    def on_scope_merged(self, _scope_name: str, _parent_scope_name: str) -> None:
        self._invalidate_runtime_caches()

    def on_scope_discarded(self, _scope_name: str) -> None:
        self._invalidate_runtime_caches()

    def materializers(self) -> Sequence[InternalMaterializer]:
        return (_SQLiteMaterializer(self),)

    def authority(self) -> SubstrateAuthority:
        return SubstrateAuthority(
            substrate=self.name,
            containment=make_authority_aspect(
                regime="partial",
                access_gated=False,
                tier="recording",
                reason="SQLite buffering is authoritative only for callers routed through the substrate.",
            ),
            provenance=make_authority_aspect(
                regime="partial",
                access_gated=False,
                tier="recording",
                reason="SQLite provenance is recorded only for explicit substrate execution paths.",
            ),
            reason="SQLite buffering provides a substrate-managed runtime view without gating direct file access.",
        )

    def python_patches(self) -> Sequence[PythonPatch]:
        return ()

    def prepare(self, context: DriverContext, request: IngressRequest) -> DriverIngressResult:
        del context
        if not isinstance(request, CommandRequest):
            raise UnsupportedRequestError(driver_id=self.driver_id, request_type=type(request))
        return self.execute(request.command, self._pipeline.require_world(), **dict(request.params))

    def capture_adapters(self, context: DriverContext) -> tuple[Any, ...]:
        del context
        return ()

    def validate_result(self, request: IngressRequest, result: DriverIngressResult) -> None:
        del request, result

    def execute(
        self,
        command: str,
        scope: ScopeInfo,
        **params: Any,
    ) -> DriverIngressResult:
        if command not in {"query", "execute"}:
            raise ValueError(f"Unknown sqlite command: {command!r}")
        sql = params["sql"]
        sql_params = params.get("params")
        # SQLite keeps self-protection for direct callers as well as framework-routed execution.
        self._validate_command_sql(command, sql)
        carrier, carrier_effects = self._resolve_carrier(scope)

        conn = sqlite3.connect(carrier.runtime_path)
        try:
            cursor = conn.cursor()
            try:
                if sql_params is None:
                    cursor.execute(sql)
                else:
                    cursor.execute(sql, sql_params)
                if command == "query":
                    rows = [list(row) for row in cursor.fetchall()]
                    columns = [desc[0] for desc in (cursor.description or ())]
                    return DriverIngressResult(
                        effects=(
                            *carrier_effects,
                            EffectRecord(
                                effect_type="SqlQueryObserved",
                                metadata={
                                    "target_id": self._target_id,
                                    "sql": sql,
                                    "row_count": len(rows),
                                    "carrier_scope": carrier.scope.name,
                                },
                            ),
                        ),
                        value={"columns": columns, "rows": rows, "row_count": len(rows)},
                    )
                conn.commit()
            except sqlite3.Error as exc:
                raise SubstrateCommandError(substrate=self.name, command=command, message=str(exc)) from exc
            kind = self._statement_kind(sql)
            carrier_seq = carrier.next_seq
            self._carriers[carrier.scope.name] = replace(carrier, next_seq=carrier_seq + 1)
            return DriverIngressResult(
                effects=(
                    *carrier_effects,
                    EffectRecord(
                        effect_type="SqlStatementObserved",
                        metadata={
                            "target_id": self._target_id,
                            "sql": sql,
                            "kind": kind,
                            "carrier_scope": carrier.scope.name,
                        },
                    ),
                    EffectRecord(
                        effect_type="SqlStatementBuffered",
                        metadata={
                            "target_id": self._target_id,
                            "basis_token": carrier.basis_token,
                            "carrier_scope": carrier.scope.name,
                            "carrier_seq": carrier_seq,
                            "materializer_key": self.materializer_key,
                            "sql": sql,
                            "kind": kind,
                            "params": sql_params,
                        },
                    ),
                ),
                value={"rowcount": cursor.rowcount},
            )
        finally:
            conn.close()

    def current_basis_token(self) -> str:
        with self._control_plane_guard():
            return self._basis_token_for_path(self._db_path)

    def validate_command_invocation(
        self,
        command: str,
        scope: ScopeInfo,
        *,
        params: Mapping[str, Any],
    ) -> None:
        del scope
        self._validate_command_sql(command, params.get("sql"))

    def build_replay_plan(self, scope: ScopeInfo) -> SqlReplayPlan:
        return build_sql_replay_plan(
            pipeline=self._pipeline,
            scope_queries=self._runtime,
            scope=scope,
            substrate=self.name,
            target_id=self._target_id,
            observed_token=self.current_basis_token(),
        )

    def build_pending_replay_plan(self) -> SqlReplayPlan:
        ground = self._runtime.lookup_scope("ground")
        if ground is None:
            raise RuntimeError("SQLite pending replay planning requires an active ground scope.")
        return self.build_replay_plan(ground)

    def latest_reconcile_preflight(self, plan: SqlReplayPlan) -> PreflightResult | None:
        latest: dict[str, object] | None = None
        for commit in self._pipeline.store.walk_pending(max_count=10_000):
            meta = commit.metadata
            if meta.get("substrate") != self.name or meta.get("type") != "SqlReconcileRecorded":
                continue
            if meta.get("target_id") != plan.target_id:
                continue
            if meta.get("old_frontier") != plan.frontier:
                continue
            latest = meta

        if latest is None:
            return None

        outcome = latest.get("outcome")
        reason = latest.get("reason")
        if outcome not in {"conflicted", "unsupported"} or not isinstance(reason, str):
            raise RuntimeError("SQLite reconcile record has invalid outcome metadata.")
        status = cast("PreflightStatus", outcome)

        return PreflightResult(
            status=status,
            reason=reason,
            observed_token=plan.observed_token,
            base_availability=plan.base_availability,
        )

    def record_reconcile_outcome(
        self,
        *,
        plan: SqlReplayPlan,
        outcome: PreflightStatus,
        reason: str,
    ) -> PreflightResult:
        ground = self._runtime.lookup_scope("ground")
        if ground is None:
            raise RuntimeError("SQLite reconcile recording requires an active ground scope.")

        self._pipeline.record_runtime_effect(
            EffectRecord(
                effect_type="SqlReconcileRecorded",
                metadata={
                    "target_id": plan.target_id,
                    "old_frontier": plan.frontier,
                    "new_frontier": None,
                    "old_basis_token": plan.basis_token,
                    "new_basis_token": None,
                    "observed_token": plan.observed_token,
                    "outcome": outcome,
                    "reason": reason,
                    "materializer_key": self.materializer_key,
                },
            ),
            substrate=self.name,
            scope=ground,
            boundary_policy="append_or_root",
            operation_kind="sqlite.record_reconcile_outcome",
            operation_label=f"sqlite-reconcile-{plan.target_id}",
            operation_metadata={"target_id": plan.target_id, "outcome": outcome},
        )
        return PreflightResult(
            status=outcome,
            reason=reason,
            observed_token=plan.observed_token,
            base_availability=plan.base_availability,
        )

    def base_availability(self, basis_token: str | None) -> UpstreamBaseAvailability:
        return resolve_base_availability(
            substrate=self.name,
            target_id=self._target_id,
            basis_token=basis_token,
            observed_token=self.current_basis_token(),
        )

    def _resolve_carrier(self, scope: ScopeInfo) -> tuple[_CarrierState, tuple[EffectRecord, ...]]:
        if self._has_durable_carrier_state(scope):
            return self._ensure_exact_carrier(scope), ()

        carrier = self._ensure_nearest_carrier(scope)
        if carrier is not None:
            if self._runtime.can_create_carrier(self.name, self._target_id, scope):
                return self._fork_carrier(scope, parent=carrier)
            if not carrier.runtime_path.exists():
                carrier = self._rebuild_runtime_db(carrier.scope.name)
            return carrier, ()

        return self._create_root_carrier(scope), ()

    def _ensure_nearest_carrier(self, scope: ScopeInfo) -> _CarrierState | None:
        live = self._runtime.nearest_carrier_scope(self.name, self._target_id, scope)
        if live is not None:
            existing = self._carriers.get(live.name)
            if existing is not None:
                return existing

        current: ScopeInfo | None = scope
        while current is not None:
            existing = self._carriers.get(current.name)
            if existing is not None:
                return existing
            if self._has_durable_carrier_state(current):
                return self._restore_carrier_state(current)
            current = self._runtime.parent_scope(current)
        return None

    def _has_durable_carrier_state(self, scope: ScopeInfo) -> bool:
        return self._fork_marker_for_scope(scope) is not None or bool(self._scope_replay_plan(scope).entries)

    def _restore_carrier_state(self, scope: ScopeInfo) -> _CarrierState:
        existing = self._carriers.get(scope.name)
        if existing is not None:
            return existing

        runtime_path = self._runtime_path_for_scope(scope.name)
        fork_marker = self._fork_marker_for_scope(scope)
        if fork_marker is not None:
            parent_scope_name = fork_marker.get("parent_carrier_scope")
            base_seq = fork_marker.get("base_seq")
            if isinstance(parent_scope_name, str) and isinstance(base_seq, int):
                self._carrier_lineage[scope.name] = (parent_scope_name, base_seq)
            basis_token = str(fork_marker["basis_token"])
        else:
            basis_token = self._basis_token_for_carrier(scope, carrier_scope_name=scope.name)

        self._register_runtime_carrier(scope, runtime_path)
        carrier = _CarrierState(
            scope=scope,
            runtime_path=runtime_path,
            basis_token=basis_token,
            next_seq=self._next_seq_for_entries(self._carrier_entries_for_scope(scope), scope.name),
        )
        self._carriers[scope.name] = carrier
        if not runtime_path.exists():
            return self._rebuild_runtime_db(scope.name)
        return carrier

    def _ensure_exact_carrier(self, scope: ScopeInfo) -> _CarrierState:
        existing = self._carriers.get(scope.name)
        if existing is not None:
            if not existing.runtime_path.exists():
                return self._rebuild_runtime_db(scope.name)
            return existing
        if self._has_durable_carrier_state(scope):
            return self._restore_carrier_state(scope)
        return self._create_root_carrier(scope)

    def _create_root_carrier(self, scope: ScopeInfo) -> _CarrierState:
        runtime_path = self._runtime_path_for_scope(scope.name)
        basis_token = self._basis_token_for_carrier(scope, carrier_scope_name=scope.name)
        has_durable_state = self._has_durable_carrier_state(scope)
        if not has_durable_state:
            self._materialize_runtime_db(runtime_path)
        self._register_runtime_carrier(scope, runtime_path)
        carrier = _CarrierState(
            scope=scope,
            runtime_path=runtime_path,
            basis_token=basis_token,
            next_seq=self._next_seq_for_entries(self._carrier_entries_for_scope(scope), scope.name),
        )
        self._carriers[scope.name] = carrier
        if has_durable_state and not runtime_path.exists():
            return self._rebuild_runtime_db(scope.name)
        return carrier

    def _runtime_path_for_scope(self, scope_name: str) -> Path:
        return (
            self._runtime_root / f"{scope_name}-{hashlib.sha256(self._target_id.encode('utf-8')).hexdigest()[:12]}.db"
        )

    def _register_runtime_carrier(self, scope: ScopeInfo, runtime_path: Path) -> None:
        self._runtime.register_carrier(self.name, self._target_id, scope)
        self._register_db_claims(runtime_path, "authoritative_suppress_fs")

    def _fork_carrier(
        self,
        scope: ScopeInfo,
        *,
        parent: _CarrierState,
    ) -> tuple[_CarrierState, tuple[EffectRecord, ...]]:
        if not parent.runtime_path.exists():
            parent = self._rebuild_runtime_db(parent.scope.name)
        parent_plan = self.build_replay_plan(parent.scope)
        runtime_path = self._runtime_path_for_scope(scope.name)
        self._materialize_runtime_db(runtime_path, source_path=parent.runtime_path)
        self._register_runtime_carrier(scope, runtime_path)
        base_seq = parent.next_seq - 1
        self._carrier_lineage[scope.name] = (parent.scope.name, base_seq)
        carrier = _CarrierState(scope=scope, runtime_path=runtime_path, basis_token=parent.basis_token)
        self._carriers[scope.name] = carrier
        return (
            carrier,
            (
                EffectRecord(
                    effect_type="SqlCarrierForked",
                    metadata={
                        "target_id": self._target_id,
                        "basis_token": parent.basis_token,
                        "child_carrier_scope": scope.name,
                        "parent_carrier_scope": parent.scope.name,
                        "parent_scope_ref": parent.scope.ref,
                        "parent_creation_oid": parent.scope.creation_oid,
                        "parent_visible_frontier": parent_plan.frontier,
                        "base_seq": base_seq,
                        "materializer_key": self.materializer_key,
                    },
                ),
            ),
        )

    def _rebuild_runtime_db(self, carrier_scope_name: str) -> _CarrierState:
        carrier = self._carriers[carrier_scope_name]
        plan = self.build_replay_plan(carrier.scope)
        if not plan.base_availability.base_available:
            raise RuntimeError(
                "SQLite runtime rebuild requires the original basis to remain available. "
                f"Observed token {plan.observed_token!r} no longer matches {plan.basis_token!r}."
            )
        self._materialize_runtime_db(carrier.runtime_path)
        self._apply_replay_entries(carrier.runtime_path, plan.entries)
        rebuilt = replace(carrier, next_seq=self._next_seq_for_entries(plan.entries, carrier_scope_name))
        self._carriers[carrier_scope_name] = rebuilt
        return rebuilt

    def _fork_marker_for_scope(self, scope: ScopeInfo) -> dict[str, object] | None:
        return fork_marker_for_scope(
            pipeline=self._pipeline,
            scope=scope,
            substrate=self.name,
            target_id=self._target_id,
        )

    def _basis_token_for_carrier(self, scope: ScopeInfo, *, carrier_scope_name: str) -> str:
        basis_tokens = {
            entry.basis_token
            for entry in self._carrier_entries_for_scope(scope)
            if entry.carrier_scope == carrier_scope_name
        }
        if not basis_tokens:
            fork_marker = self._fork_marker_for_scope(scope)
            if fork_marker is not None:
                basis_token = fork_marker.get("basis_token")
                if isinstance(basis_token, str):
                    return basis_token
            return self._scope_replay_plan(scope).basis_token or self.current_basis_token()
        if len(basis_tokens) != 1:
            msg = (
                "SQLite carrier reconstruction requires exactly one basis_token for "
                f"{carrier_scope_name!r}. Got {len(basis_tokens)}."
            )
            raise RuntimeError(msg)
        return str(next(iter(basis_tokens)))

    def _carrier_entries_for_scope(self, scope: ScopeInfo) -> tuple[SqlReplayEntry, ...]:
        return tuple(entry for entry in self._scope_replay_plan(scope).entries if entry.carrier_scope == scope.name)

    def _scope_replay_plan(self, scope: ScopeInfo) -> SqlReplayPlan:
        return build_sql_replay_plan(
            pipeline=self._pipeline,
            scope_queries=self._runtime,
            scope=scope,
            substrate=self.name,
            target_id=self._target_id,
            observed_token=self.current_basis_token(),
        )

    @staticmethod
    def _next_seq_for_entries(entries: tuple[SqlReplayEntry, ...], carrier_scope_name: str) -> int:
        carrier_entries = [entry for entry in entries if entry.carrier_scope == carrier_scope_name]
        if not carrier_entries:
            return 0
        return carrier_entries[-1].carrier_seq + 1

    @staticmethod
    def _apply_replay_entries(runtime_path: Path, entries: tuple[SqlReplayEntry, ...]) -> None:
        if not entries:
            return
        intents = tuple({"sql": entry.sql, "params": entry.params} for entry in entries)
        SQLiteSubstrate._apply_unit_intents(runtime_path, intents)

    @staticmethod
    def _apply_unit_intents(runtime_path: Path, intents: tuple[dict[str, object], ...]) -> None:
        if not intents:
            return
        conn = sqlite3.connect(runtime_path)
        try:
            cursor = conn.cursor()
            for intent in intents:
                params = intent.get("params")
                sql = str(intent["sql"])
                if params is None:
                    cursor.execute(sql)
                else:
                    cursor.execute(sql, _coerce_sql_params(params))
            conn.commit()
        finally:
            conn.close()

    def _materialize_runtime_db(self, runtime_path: Path, *, source_path: Path | None = None) -> None:
        with self._control_plane_guard():
            runtime_path.parent.mkdir(parents=True, exist_ok=True)
            source = self._db_path if source_path is None else source_path
            self._snapshot_database(source, runtime_path)

    def _register_db_claims(self, db_path: Path, policy: str) -> None:
        self._runtime.register_claim(self.name, self._target_id, db_path, policy)
        for sidecar in self._sidecar_paths(db_path):
            self._runtime.register_claim(self.name, self._target_id, sidecar, policy)

    @classmethod
    def _sidecar_paths(cls, db_path: Path) -> tuple[Path, ...]:
        return tuple(db_path.with_name(f"{db_path.name}{suffix}") for suffix in cls._SIDECAR_SUFFIXES)

    @classmethod
    def _remove_snapshot_artifacts(cls, db_path: Path) -> None:
        db_path.unlink(missing_ok=True)
        for sidecar in cls._sidecar_paths(db_path):
            sidecar.unlink(missing_ok=True)

    @classmethod
    def _snapshot_database(cls, source_path: Path, dest_path: Path) -> None:
        cls._remove_snapshot_artifacts(dest_path)
        if not source_path.exists():
            conn = sqlite3.connect(dest_path)
            conn.close()
            return

        source = sqlite3.connect(source_path)
        try:
            dest = sqlite3.connect(dest_path)
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()

    def _invalidate_runtime_caches(self) -> None:
        self._carriers.clear()
        self._carrier_lineage.clear()
        with self._control_plane_guard():
            shutil.rmtree(self._runtime_root, ignore_errors=True)

    @staticmethod
    def _statement_kind(sql: str) -> str:
        stripped = sql.lstrip()
        if not stripped:
            return "UNKNOWN"
        return stripped.split(None, 1)[0].upper()

    @classmethod
    def _validate_command_sql(cls, command: str, sql: object) -> None:
        if not isinstance(sql, str):
            raise ValueError("SQLite commands require a string SQL statement.")  # noqa: TRY004
        mode, reason = cls._classify_sql(sql)
        if command == mode:
            return
        if command == "query":
            raise ValueError(f"SQLite query accepts only read-only statements: {reason}")
        raise ValueError(f"SQLite execute accepts only mutating statements: {reason}")

    @classmethod
    def _classify_sql(cls, sql: str) -> tuple[str, str]:
        stripped = cls._strip_leading_sql_junk(sql).strip()
        if not stripped:
            raise ValueError("SQLite commands require a non-empty SQL statement.")
        if cls._has_multiple_statements(stripped):
            raise ValueError("SQLite commands reject multi-statement input.")

        keyword = cls._first_keyword(stripped)
        if keyword == "SELECT":
            return ("query", "SELECT is read-only")
        if keyword == "EXPLAIN":
            if _EXPLAIN_SELECT_RE.match(stripped):
                return ("query", "EXPLAIN SELECT is read-only")
            raise ValueError("SQLite commands reject unsupported EXPLAIN forms in the first slice.")
        if keyword in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            return ("execute", f"{keyword} is mutating")
        if keyword == "CREATE":
            if _FORBIDDEN_CREATE_RE.match(stripped):
                raise ValueError("SQLite commands reject unsupported CREATE forms in the first slice.")
            return ("execute", "CREATE is allowed in the first-slice mutating contract")
        if keyword == "ALTER":
            return ("execute", "ALTER is allowed in the first-slice mutating contract")
        if keyword == "DROP":
            if _FORBIDDEN_DROP_RE.match(stripped):
                raise ValueError("SQLite commands reject unsupported DROP forms in the first slice.")
            return ("execute", "DROP is allowed in the first-slice mutating contract")
        if keyword in _REJECT_PREFIXES:
            raise ValueError(f"SQLite commands reject {keyword} in the first slice.")
        if keyword == "PRAGMA":
            name, has_args, has_assignment = cls._parse_pragma(stripped)
            if has_assignment:
                raise ValueError("SQLite commands reject mutating PRAGMA statements.")
            if name in _READ_ONLY_PRAGMAS and has_args:
                return ("query", f"PRAGMA {name} is allowed in the first slice")
            raise ValueError("SQLite commands reject unlisted or ambiguous PRAGMA statements.")
        if keyword == "WITH":
            match = _WITH_TERMINATOR_RE.search(stripped)
            if match is None:
                raise ValueError("SQLite commands reject ambiguous WITH statements.")
            terminal = match.group(1).upper()
            if terminal == "SELECT":
                return ("query", "WITH ... SELECT is read-only")
            return ("execute", f"WITH ... {terminal} is mutating")
        raise ValueError(
            f"SQLite commands reject unsupported statement family {keyword or 'unknown'} in the first slice."
        )

    @staticmethod
    def _strip_leading_sql_junk(sql: str) -> str:
        return _LEADING_SQL_JUNK_RE.sub("", sql, count=1)

    @staticmethod
    def _parse_pragma(sql: str) -> tuple[str, bool, bool]:
        match = _PRAGMA_RE.match(sql)
        if match is None:
            raise ValueError("SQLite commands reject malformed PRAGMA statements.")
        return (
            match.group("name").lower(),
            match.group("args") is not None,
            match.group("assignment") is not None,
        )

    @classmethod
    def _has_multiple_statements(cls, sql: str) -> bool:
        stripped = cls._strip_leading_sql_junk(sql).strip()
        if not stripped or ";" not in stripped:
            return False
        trimmed = stripped.rstrip()
        if trimmed.endswith(";"):
            trimmed = trimmed[:-1].rstrip()
        return ";" in trimmed

    @classmethod
    def _first_keyword(cls, sql: str) -> str:
        stripped = cls._strip_leading_sql_junk(sql).lstrip()
        if not stripped:
            return ""
        match = re.match(r"[A-Za-z]+", stripped)
        return match.group(0).upper() if match else ""

    @classmethod
    def _basis_token_for_path(cls, path: Path) -> str:
        if not path.exists():
            return "missing"
        with tempfile.TemporaryDirectory(prefix="vcscore-sqlite-basis-") as tmpdir:
            snapshot_path = Path(tmpdir) / "basis.db"
            cls._snapshot_database(path, snapshot_path)
            return hashlib.sha256(snapshot_path.read_bytes()).hexdigest()

    @classmethod
    def _database_signature(cls, path: Path) -> tuple[tuple[object, ...], ...]:
        if not path.exists():
            return (("missing",),)
        conn = sqlite3.connect(path)
        try:
            schema = tuple(
                conn.execute(
                    """
                    SELECT type, name, tbl_name, sql
                    FROM sqlite_master
                    WHERE type IN ('table', 'index', 'view', 'trigger')
                    ORDER BY type, name
                    """
                ).fetchall()
            )
            contents: list[tuple[object, ...]] = [("schema", *entry) for entry in schema]
            table_names = [str(entry[1]) for entry in schema if entry[0] == "table"]
            for table_name in table_names:
                quoted_table = cls._quote_identifier(table_name)
                columns = tuple(str(row[1]) for row in conn.execute(f"PRAGMA table_info({quoted_table})").fetchall())
                order_clause = ", ".join(cls._quote_identifier(column) for column in columns)
                rows = tuple(
                    conn.execute(f"SELECT * FROM {quoted_table} ORDER BY {order_clause}").fetchall()  # noqa: S608
                )
                contents.append(("table", table_name, columns, rows))
            return tuple(contents)
        finally:
            conn.close()

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        return f'"{identifier.replace(chr(34), chr(34) * 2)}"'
