"""Persistent identity helpers for repo-scoped durable ids."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from vcs_core._errors import InvalidIdentityError

_IDENTITY_FILE = "identity.json"
_VERSION = 1
_EXECUTION_HISTORY_EPOCH = 1
_CONTROL_PLANE_EPOCH = 2


def _identity_path(repo_path: str) -> Path:
    return Path(repo_path) / _IDENTITY_FILE


def _new_world_id() -> str:
    return f"world_{uuid.uuid4().hex[:12]}"


def _read_identity_payload(repo_path: str) -> dict[str, Any] | None:
    path = _identity_path(repo_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise InvalidIdentityError(
            f"{path} contains malformed JSON; activation refused to preserve durable identity."
        ) from exc
    except OSError as exc:
        raise InvalidIdentityError(
            f"{path} could not be read; activation refused to preserve durable identity."
        ) from exc
    if not isinstance(data, dict):
        raise InvalidIdentityError(
            f"{path} must contain a JSON object; activation refused to preserve durable identity."
        )
    if data.get("version") != _VERSION:
        raise InvalidIdentityError(
            f"{path} has unsupported identity version {data.get('version')!r}; "
            "activation refused to preserve durable identity."
        )
    return data


def _write_identity_payload(repo_path: str, payload: dict[str, Any]) -> None:
    path = _identity_path(repo_path)
    fd, tmp = tempfile.mkstemp(dir=repo_path, suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        tmp_path.replace(path)
    except BaseException:
        with suppress(OSError):
            tmp_path.unlink()
        raise


def _validated_ground_world_id(path: Path, payload: dict[str, Any]) -> str:
    execution_history_epoch = payload.get("execution_history_epoch")
    if execution_history_epoch != _EXECUTION_HISTORY_EPOCH:
        raise InvalidIdentityError(
            f"{path} has unsupported execution history epoch {execution_history_epoch!r}; "
            "activation refused to preserve durable identity."
        )
    control_plane_epoch = payload.get("control_plane_epoch")
    if control_plane_epoch != _CONTROL_PLANE_EPOCH:
        raise InvalidIdentityError(
            f"{path} has unsupported control plane epoch {control_plane_epoch!r}; "
            "activation refused to preserve durable identity."
        )
    world_id = payload.get("ground_world_id")
    if isinstance(world_id, str) and world_id:
        return world_id
    raise InvalidIdentityError(
        f"{path} is missing a valid 'ground_world_id'; activation refused to preserve durable identity."
    )


def read_ground_world_id(repo_path: str) -> str:
    """Return the repo-stable ground world id without mutating repository state."""
    payload = _read_identity_payload(repo_path)
    path = _identity_path(repo_path)
    if payload is None:
        raise InvalidIdentityError(f"{path} is missing; activation refused to preserve durable identity.")
    return _validated_ground_world_id(path, payload)


def initialize_ground_world_id(repo_path: str) -> str:
    """Create repo identity if absent, otherwise validate and return it."""
    payload = _read_identity_payload(repo_path)
    path = _identity_path(repo_path)
    if payload is not None:
        return _validated_ground_world_id(path, payload)
    world_id = _new_world_id()
    _write_identity_payload(
        repo_path,
        {
            "version": _VERSION,
            "execution_history_epoch": _EXECUTION_HISTORY_EPOCH,
            "control_plane_epoch": _CONTROL_PLANE_EPOCH,
            "ground_world_id": world_id,
        },
    )
    return world_id
