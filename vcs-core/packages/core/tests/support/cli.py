"""Shared CLI helpers for vcs-core tests."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from vcs_core.cli import main


def init_repo(runner: CliRunner, path: Path) -> None:
    """Initialize a vcs-core repo and fail loudly if setup breaks."""
    result = runner.invoke(main, ["init", str(path)])
    assert result.exit_code == 0, result.output
