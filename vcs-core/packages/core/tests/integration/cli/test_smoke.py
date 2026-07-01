"""Small offline smoke tests for common package-local workflows."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from vcs_core.cli import main

from ...support.cli import init_repo


def test_cli_happy_path_smoke(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runner = CliRunner()
    demo_root = tmp_path / "demo-world"
    demo_root.mkdir()
    init_repo(runner, demo_root)
    monkeypatch.chdir(demo_root)

    activate = runner.invoke(main, ["activate", "."])
    assert activate.exit_code == 0, activate.output

    branch = runner.invoke(main, ["branch", "smoke-task"])
    assert branch.exit_code == 0, branch.output

    payload = tmp_path / "hello.payload"
    payload.write_text("hello")
    write = runner.invoke(
        main,
        ["exec", "filesystem", "write", "--scope", "smoke-task", "-p", "path=hello.txt", "-p", f"content=@{payload}"],
    )
    assert write.exit_code == 0, write.output

    merge = runner.invoke(main, ["merge", "smoke-task"])
    assert merge.exit_code == 0, merge.output

    push = runner.invoke(main, ["push"])
    assert push.exit_code == 0, push.output

    checkout_dest = tmp_path / "smoke-snap"
    checkout = runner.invoke(main, ["checkout", "ground", "--dest", str(checkout_dest)])
    assert checkout.exit_code == 0, checkout.output
    assert (checkout_dest / "hello.txt").read_text() == "hello"

    status = runner.invoke(main, ["status"])
    assert status.exit_code == 0, status.output
    assert "Commits ahead: 0" in status.output


def test_branch_requires_repo_in_current_working_directory(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    runner = CliRunner()
    non_repo_root = tmp_path / "not-a-repo"
    non_repo_root.mkdir()
    monkeypatch.chdir(non_repo_root)

    branch = runner.invoke(main, ["branch", "smoke-task"])

    assert branch.exit_code != 0
    assert "not a vcs-core repository" in branch.output
