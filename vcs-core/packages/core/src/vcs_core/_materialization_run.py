"""Durable materialization run ledger for crash recovery."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

_RUN_LEDGER_NAME = "materialization-run.json"
_RUN_ARTIFACTS_DIR = "materialization-runs"
_INVALID_RUN_IDS: Final = frozenset({"", ".", ".."})


@dataclass(frozen=True)
class MaterializationRun:
    """Durable push-run ledger used by verify-oriented recovery."""

    session_id: str
    run_id: str
    timestamp: float
    planned_unit_ids: tuple[str, ...]
    completed_unit_ids: tuple[str, ...] = ()
    unit_state: dict[str, dict[str, object]] = field(default_factory=dict)

    def with_completed(self, unit_ids: tuple[str, ...]) -> MaterializationRun:
        completed = list(self.completed_unit_ids)
        for unit_id in unit_ids:
            if unit_id not in completed:
                completed.append(unit_id)
        return MaterializationRun(
            session_id=self.session_id,
            run_id=self.run_id,
            timestamp=self.timestamp,
            planned_unit_ids=self.planned_unit_ids,
            completed_unit_ids=tuple(completed),
            unit_state={key: dict(value) for key, value in self.unit_state.items()},
        )


def _ledger_path(repo_path: str) -> Path:
    return Path(repo_path) / _RUN_LEDGER_NAME


def _artifacts_root(repo_path: str) -> Path:
    return Path(repo_path) / _RUN_ARTIFACTS_DIR


def _validate_run_id(run_id: str) -> str:
    if run_id in _INVALID_RUN_IDS or "/" in run_id or "\\" in run_id:
        msg = f"invalid materialization run id: {run_id!r}"
        raise ValueError(msg)
    return run_id


def materialization_run_directory(repo_path: str, run_id: str) -> Path:
    return _artifacts_root(repo_path) / _validate_run_id(run_id)


def write_materialization_run(repo_path: str, run: MaterializationRun) -> None:
    _ledger_path(repo_path).write_text(
        json.dumps(
            {
                "session_id": run.session_id,
                "run_id": run.run_id,
                "timestamp": run.timestamp,
                "planned_unit_ids": list(run.planned_unit_ids),
                "completed_unit_ids": list(run.completed_unit_ids),
                "unit_state": run.unit_state,
            }
        )
    )


def read_materialization_run(repo_path: str) -> MaterializationRun | None:
    path = _ledger_path(repo_path)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return MaterializationRun(
        session_id=str(data["session_id"]),
        run_id=str(data["run_id"]),
        timestamp=float(data.get("timestamp", time.time())),
        planned_unit_ids=tuple(str(unit_id) for unit_id in data.get("planned_unit_ids", ())),
        completed_unit_ids=tuple(str(unit_id) for unit_id in data.get("completed_unit_ids", ())),
        unit_state={str(unit_id): dict(state) for unit_id, state in dict(data.get("unit_state", {})).items()},
    )


def mark_materialization_units_completed(repo_path: str, unit_ids: tuple[str, ...]) -> MaterializationRun | None:
    run = read_materialization_run(repo_path)
    if run is None:
        return None
    updated = run.with_completed(unit_ids)
    write_materialization_run(repo_path, updated)
    return updated


def clear_materialization_run(repo_path: str) -> None:
    run: MaterializationRun | None = None
    corrupt_ledger = False
    try:
        run = read_materialization_run(repo_path)
    except (OSError, TypeError, ValueError, KeyError):
        corrupt_ledger = True
    _ledger_path(repo_path).unlink(missing_ok=True)
    if run is None:
        if corrupt_ledger:
            _clear_materialization_artifacts(repo_path)
        return
    _clear_materialization_run_artifacts(repo_path, run.run_id)


def _clear_materialization_run_artifacts(repo_path: str, run_id: str) -> None:
    try:
        run_dir = materialization_run_directory(repo_path, run_id)
    except ValueError:
        return
    root = _artifacts_root(repo_path).resolve(strict=False)
    resolved = run_dir.resolve(strict=False)
    if root not in (resolved, *resolved.parents):
        return
    if run_dir.is_symlink() or run_dir.is_file():
        run_dir.unlink(missing_ok=True)
    else:
        shutil.rmtree(run_dir, ignore_errors=True)


def _clear_materialization_artifacts(repo_path: str) -> None:
    root = _artifacts_root(repo_path)
    if not root.exists():
        return
    if root.is_symlink() or root.is_file():
        root.unlink(missing_ok=True)
        return
    for child in root.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
