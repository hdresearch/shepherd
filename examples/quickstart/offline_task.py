"""Offline Shepherd task quickstart.

Run:
    python offline_task.py
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass

import shepherd as sp


@dataclass(frozen=True)
class DemoModel:
    """Tiny offline model identity for the task workspace."""

    name: str = "offline-demo"


@sp.task
def draft_release_note(component: str, change: str) -> str:
    """Return a deterministic release-note line."""
    return f"{component}: {change}"


def main() -> None:
    """Run the offline task demo."""
    with tempfile.TemporaryDirectory(prefix="shepherd-offline-") as root:
        sp.workspace(model=DemoModel(), root=root)
        note = draft_release_note(
            "world channel",
            "retained outputs can be inspected before settlement",
        )
    sys.stdout.write(note + "\n")


if __name__ == "__main__":
    main()
