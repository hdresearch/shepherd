"""Checkout-oriented CLI integration tests."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from vcs_core.cli import main

from ...support.cli import init_repo as _init


def _payload(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def test_checkout_extracts_files(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    runner.invoke(main, ["branch", "task-1"])
    payload = _payload(tmp_path, "hello.payload", "hello world")
    runner.invoke(
        main,
        ["exec", "filesystem", "write", "--scope", "task-1", "-p", "path=hello.txt", "-p", f"content=@{payload}"],
    )
    runner.invoke(main, ["merge", "task-1"])

    dest = str(tmp_path / "snap")
    result = runner.invoke(main, ["checkout", "ground", "--dest", dest])
    assert result.exit_code == 0, result.output
    assert "Extracted" in result.output
    assert (Path(dest) / "hello.txt").read_text() == "hello world"


def test_checkout_bad_ref_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    result = runner.invoke(main, ["checkout", "nonexistent"])
    assert result.exit_code != 0
    assert "cannot resolve ref" in result.output


def test_checkout_by_oid(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    runner.invoke(main, ["branch", "task-oid"])
    payload = _payload(tmp_path, "oid.payload", "oid data")
    runner.invoke(
        main,
        ["exec", "filesystem", "write", "--scope", "task-oid", "-p", "path=oid.txt", "-p", f"content=@{payload}"],
    )
    runner.invoke(main, ["merge", "task-oid"])

    log_result = runner.invoke(main, ["log", "-n", "1"])
    assert log_result.exit_code == 0, log_result.output
    lines = [ln for ln in log_result.output.splitlines() if ln.strip()]
    oid_token = lines[0].split()[0]

    dest = str(tmp_path / "oid-snap")
    result = runner.invoke(main, ["checkout", oid_token, "--dest", dest])
    assert result.exit_code == 0, result.output
    assert "Extracted" in result.output
    assert (Path(dest) / "oid.txt").read_text() == "oid data"


def test_checkout_default_dest_outside_vcscore(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    runner.invoke(main, ["branch", "task-default"])
    payload = _payload(tmp_path, "default.payload", "default")
    runner.invoke(
        main,
        ["exec", "filesystem", "write", "--scope", "task-default", "-p", "path=df.txt", "-p", f"content=@{payload}"],
    )
    runner.invoke(main, ["merge", "task-default"])

    result = runner.invoke(main, ["checkout", "ground"])
    assert result.exit_code == 0, result.output
    assert ".vcscore-checkouts/" in result.output
    assert (tmp_path / ".vcscore-checkouts" / "ground" / "df.txt").read_text() == "default"


def test_checkout_discarded_scope_by_name(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    runner.invoke(main, ["branch", "doomed"])
    payload = _payload(tmp_path, "discarded.payload", "discarded data")
    runner.invoke(
        main,
        ["exec", "filesystem", "write", "--scope", "doomed", "-p", "path=gone.txt", "-p", f"content=@{payload}"],
    )
    runner.invoke(main, ["discard", "doomed"])

    dest = str(tmp_path / "archive-snap")
    result = runner.invoke(main, ["checkout", "doomed", "--dest", dest])
    assert result.exit_code == 0, result.output
    assert "Extracted" in result.output
    assert (Path(dest) / "gone.txt").read_text() == "discarded data"
