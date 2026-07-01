"""Durable lifecycle run state for interrupted merge/discard recovery."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LifecycleScopeState:
    """Persisted snapshot of a scope involved in a lifecycle run."""

    name: str
    ref: str
    instance_id: str
    creation_oid: str
    world_id: str | None = None
    isolated: bool = False


@dataclass(frozen=True)
class LifecycleRun:
    """Persisted merge/discard progress for crash-safe recovery."""

    session_id: str
    operation: str
    phase: str
    scope: LifecycleScopeState
    parent: LifecycleScopeState
    scope_registry_head_oid: str | None = None
    active_ancestors: tuple[LifecycleScopeState, ...] = ()
    prepared_effect_counts: tuple[tuple[str, int], ...] = ()
    prepared_substrates: tuple[str, ...] = ()
    completed_substrates: tuple[str, ...] = ()
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        """Render the lifecycle run as a JSON-safe mapping."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> LifecycleRun:
        """Parse a lifecycle run from JSON data."""
        scope_data = _expect_mapping(data["scope"], field_name="scope")
        parent_data = _expect_mapping(data["parent"], field_name="parent")
        return cls(
            session_id=str(data["session_id"]),
            operation=str(data["operation"]),
            phase=str(data["phase"]),
            scope=_scope_state_from_mapping(scope_data),
            parent=_scope_state_from_mapping(parent_data),
            scope_registry_head_oid=(
                None if data.get("scope_registry_head_oid") is None else str(data["scope_registry_head_oid"])
            ),
            active_ancestors=tuple(
                _scope_state_from_mapping(_expect_mapping(item, field_name="active_ancestors[]"))
                for item in _expect_iterable(data.get("active_ancestors", ()), field_name="active_ancestors")
            ),
            prepared_effect_counts=tuple(
                _effect_count_from_item(item)
                for item in _expect_iterable(
                    data.get("prepared_effect_counts", ()), field_name="prepared_effect_counts"
                )
            ),
            prepared_substrates=tuple(
                str(name)
                for name in _expect_iterable(data.get("prepared_substrates", ()), field_name="prepared_substrates")
            ),
            completed_substrates=tuple(
                str(name)
                for name in _expect_iterable(data.get("completed_substrates", ()), field_name="completed_substrates")
            ),
            timestamp=_float_value(data.get("timestamp"), default=time.time()),
        )


def _expect_mapping(value: object, *, field_name: str) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    raise TypeError(f"Lifecycle run field '{field_name}' must be a mapping.")


def _expect_iterable(value: object, *, field_name: str) -> Sequence[object]:
    if isinstance(value, (list, tuple)):
        return value
    raise TypeError(f"Lifecycle run field '{field_name}' must be a sequence.")


def _scope_state_from_mapping(data: Mapping[str, object]) -> LifecycleScopeState:
    return LifecycleScopeState(
        name=str(data["name"]),
        ref=str(data["ref"]),
        instance_id=str(data["instance_id"]),
        creation_oid=str(data["creation_oid"]),
        world_id=None if data.get("world_id") is None else str(data["world_id"]),
        isolated=_bool_value(data.get("isolated"), default=False),
    )


def _effect_count_from_item(item: object) -> tuple[str, int]:
    if not isinstance(item, (list, tuple)) or len(item) != 2:
        raise TypeError("Lifecycle run effect counts must be 2-item sequences.")
    name, count = item
    if not isinstance(count, int) or isinstance(count, bool):
        raise TypeError("Lifecycle run effect counts must be integer counts.")
    return str(name), count


def _bool_value(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise TypeError("Lifecycle run isolated flag must be boolean.")


def _float_value(value: object, *, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise TypeError("Lifecycle run timestamp must be numeric.")


def _lifecycle_run_path(repo_path: str) -> Path:
    return Path(repo_path) / "lifecycle.json"


def write_lifecycle_run(repo_path: str, run: LifecycleRun) -> None:
    """Persist lifecycle run state before destructive work begins."""
    _lifecycle_run_path(repo_path).write_text(json.dumps(run.to_dict(), indent=2, sort_keys=True))


def read_lifecycle_run(repo_path: str) -> LifecycleRun | None:
    """Read lifecycle run state, or None if no interrupted run exists."""
    path = _lifecycle_run_path(repo_path)
    if not path.exists():
        return None
    return LifecycleRun.from_dict(json.loads(path.read_text()))


def clear_lifecycle_run(repo_path: str) -> None:
    """Remove lifecycle run state after successful recovery/finalization."""
    _lifecycle_run_path(repo_path).unlink(missing_ok=True)
