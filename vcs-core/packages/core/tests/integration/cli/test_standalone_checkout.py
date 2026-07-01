"""Smoke checks for the documented standalone-repo command loop."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

VCS_CORE_ROOT = Path(__file__).resolve().parents[5]
HARNESS = VCS_CORE_ROOT / "scripts" / "standalone_repo_smoke.py"


def test_extracted_repo_root_keeps_documented_repo_root_smoke_working(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(HARNESS), "--work-root", str(tmp_path / "standalone-work")],
        cwd=VCS_CORE_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        msg = (
            f"command failed: {sys.executable} {HARNESS}\n"
            f"cwd: {VCS_CORE_ROOT}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
        raise AssertionError(msg)
