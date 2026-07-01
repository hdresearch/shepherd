"""CLI help-surface regression tests."""

from __future__ import annotations

from click.testing import CliRunner
from vcs_core.cli import main


def test_root_help_exposes_execution_commands() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["--help"])

    assert result.exit_code == 0, result.output
    assert "log" in result.output
    assert "operations" in result.output
    assert "operation" in result.output
    assert "recovery" in result.output


def test_log_help_frames_raw_history_separately_from_execution_history() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["log", "--help"])

    assert result.exit_code == 0, result.output
    assert "raw commit-carrier history" in result.output
    assert "retained structural records" in result.output


def test_operations_help_frames_execution_history_as_first_class_surface() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["operations", "--help"])

    assert result.exit_code == 0, result.output
    assert "operation-shaped execution history" in result.output
    assert "--all" in result.output


def test_operation_show_help_frames_operation_summary_surface() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["operation", "show", "--help"])

    assert result.exit_code == 0, result.output
    assert "operation summary" in result.output


def test_operation_group_help_frames_summary_level_surface() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["operation", "--help"])

    assert result.exit_code == 0, result.output
    assert "operation summaries" in result.output


def test_recovery_help_frames_non_canonical_recovery_state() -> None:
    runner = CliRunner()

    result = runner.invoke(main, ["recovery", "--help"])

    assert result.exit_code == 0, result.output
    assert "non-canonical recovery/debug state" in result.output


def test_exec_help_does_not_claim_hidden_current_scope() -> None:
    runner = CliRunner()

    exec_help = runner.invoke(main, ["exec", "--help"])

    assert exec_help.exit_code == 0, exec_help.output
    assert "most recent" not in exec_help.output
    assert "default: ground; required when live" in exec_help.output
    assert "scopes exist" in exec_help.output
