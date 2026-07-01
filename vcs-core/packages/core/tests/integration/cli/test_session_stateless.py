"""Stateless CLI behavior around session-only commands."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from vcs_core.cli import main

from ...support.cli import init_repo as _init


def test_session_stop_no_session(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)

    result = runner.invoke(main, ["session", "stop"])

    assert result.exit_code != 0
    assert "No session" in result.output or "Error" in result.output


def test_switch_no_session(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)

    result = runner.invoke(main, ["switch", "ground"])

    assert result.exit_code != 0
    assert "no session" in result.output.lower() or "Error" in result.output


def test_branch_isolated_no_session(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)

    result = runner.invoke(main, ["branch", "test-scope", "--isolated"])

    assert result.exit_code != 0
    assert "session" in result.output.lower()


def test_branch_merge_discard_stateless_still_works(tmp_path: Path) -> None:
    """Stateless branch/merge/discard should still work."""
    runner = CliRunner()
    _init(runner, tmp_path)

    result = runner.invoke(main, ["branch", "test-scope"])
    assert result.exit_code == 0, result.output
    assert "Created scope 'test-scope'" in result.output

    result = runner.invoke(main, ["exec", "marker", "mark", "-p", "label=checkpoint", "--scope", "test-scope"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(main, ["merge", "test-scope"])
    assert result.exit_code == 0, result.output
    assert "Merged" in result.output

    result = runner.invoke(main, ["branch", "doomed"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(main, ["discard", "doomed"])
    assert result.exit_code == 0, result.output
    assert "Discarded" in result.output
