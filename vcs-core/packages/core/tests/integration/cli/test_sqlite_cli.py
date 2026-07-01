"""SQLite CLI error handling regressions."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from vcs_core.cli import main


def test_sqlite_constraint_failure_renders_expected_command_error(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    (tmp_path / "vcscore.toml").write_text(
        """[bindings.main_db]
type = "sqlite"
path = "app.db"
"""
    )
    runner = CliRunner()

    assert runner.invoke(main, ["init", "."]).exit_code == 0
    assert runner.invoke(main, ["branch", "sql-a"]).exit_code == 0
    assert (
        runner.invoke(
            main,
            [
                "exec",
                "main_db",
                "execute",
                "--scope",
                "sql-a",
                "-p",
                "sql=CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT UNIQUE)",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            main,
            [
                "exec",
                "main_db",
                "execute",
                "--scope",
                "sql-a",
                "-p",
                "sql=INSERT INTO items (name) VALUES ('alpha')",
            ],
        ).exit_code
        == 0
    )

    duplicate = runner.invoke(
        main,
        [
            "exec",
            "main_db",
            "execute",
            "--scope",
            "sql-a",
            "-p",
            "sql=INSERT INTO items (name) VALUES ('alpha')",
        ],
    )

    assert duplicate.exit_code != 0
    assert (
        "Error: SQLite execute failed for binding 'main_db': UNIQUE constraint failed: items.name" in duplicate.output
    )
    assert "Traceback" not in duplicate.output

    archived = runner.invoke(main, ["operations", "--archived", "--scope", "sql-a"])
    assert archived.exit_code == 0, archived.output
    assert "sqlite.execute" in archived.output
    assert "[archived/error]" in archived.output
