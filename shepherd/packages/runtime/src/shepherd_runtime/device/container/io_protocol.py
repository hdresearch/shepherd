"""Container task-runner input/output protocol helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TASK_INPUT_PATH = Path("/task/input.json")
TASK_OUTPUT_PATH = Path("/task/output.json")
REBIND_ENV_PATH = Path("/task/rebind.env")


def load_rebind_env(path: Path = REBIND_ENV_PATH) -> dict[str, str]:
    """Load path-rebinding environment from file."""
    env: dict[str, str] = {}

    if not path.exists():
        return env

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()

    return env


def load_input(path: Path = TASK_INPUT_PATH) -> dict[str, Any]:
    """Load task input from JSON file."""
    with open(path) as f:
        return json.load(f)  # type: ignore[no-any-return]


def write_output(data: dict[str, Any], path: Path = TASK_OUTPUT_PATH) -> None:
    """Write task output to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def write_error(error: str, path: Path = TASK_OUTPUT_PATH) -> None:
    """Write error output envelope."""
    write_output(
        {
            "success": False,
            "result": None,
            "collected_effects": None,
            "error": error,
        },
        path,
    )


__all__ = [
    "REBIND_ENV_PATH",
    "TASK_INPUT_PATH",
    "TASK_OUTPUT_PATH",
    "load_input",
    "load_rebind_env",
    "write_error",
    "write_output",
]
