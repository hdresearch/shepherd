"""Tests for the `vcs-core run` CLI command."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from vcs_core.cli import main
from vcs_core.store import Store

from ..support.cli import init_repo as _init


def _operation_id(entry) -> object:  # type: ignore[no-untyped-def]
    return entry.metadata["mg"]["operation"]["id"]


def _scope_registry_entry(store: Store, scope_name: str):
    snapshot = store.require_scope_registry_projection()
    return snapshot.entries_by_name[scope_name]


def _write_script(path: Path, name: str, code: str) -> str:
    script = path / name
    script.write_text(code)
    return str(script)


@pytest.fixture(autouse=True)
def _repo_cwd(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)


# ---------------------------------------------------------------------------
# Basic capture
# ---------------------------------------------------------------------------


def test_run_missing_script_errors_cleanly(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)

    result = runner.invoke(main, ["run", str(tmp_path / "missing.py")])

    assert result.exit_code == 1
    assert "script does not exist" in result.output


def test_run_captures_file_create(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "create.py",
        """\
with open("hello.txt", "w") as f:
    f.write("hello world")
""",
    )

    result = runner.invoke(main, ["run", script])
    assert result.exit_code == 0, result.output
    assert "Merged 'run-create'" in result.output
    assert "1 file effect(s) captured" in result.output

    # File should exist on disk (store-only mode, no isolation)
    assert (tmp_path / "hello.txt").read_text() == "hello world"


def test_run_captures_file_read(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    (tmp_path / "existing.txt").write_text("payload")
    script = _write_script(
        tmp_path,
        "reader.py",
        """\
with open("existing.txt") as f:
    f.read()
""",
    )

    result = runner.invoke(main, ["run", script])
    assert result.exit_code == 0, result.output
    assert "1 file effect(s) captured" in result.output


def test_run_captures_multiple_operations(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "multi.py",
        """\
import os

with open("a.txt", "w") as f:
    f.write("file a")
with open("b.txt", "w") as f:
    f.write("file b")
with open("a.txt") as f:
    f.read()
os.remove("b.txt")
""",
    )

    result = runner.invoke(main, ["run", script])
    assert result.exit_code == 0, result.output
    # FileCreate a.txt, FileCreate b.txt, FileRead a.txt, FileDelete b.txt
    assert "4 file effect(s) captured" in result.output


def test_run_groups_script_effects_under_one_operation(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "grouped.py",
        """\
with open("a.txt", "w") as f:
    f.write("file a")
with open("b.txt", "w") as f:
    f.write("file b")
""",
    )

    result = runner.invoke(main, ["run", script])
    assert result.exit_code == 0, result.output

    store = Store(str(tmp_path / ".vcscore"))
    entries = store.log(ref=store.GROUND_REF, max_count=6)
    started = next(entry for entry in entries if entry.metadata.get("type") == "OperationStarted")
    file_entries = [entry for entry in entries if entry.metadata.get("type") == "FileCreate"]
    completed = next(entry for entry in entries if entry.metadata.get("type") == "OperationCompleted")

    assert started.metadata["mg"]["operation"]["kind"] == "python.run"
    assert started.metadata["mg"]["operation"]["label"] == "run-grouped"
    assert len(file_entries) == 2
    assert {_operation_id(entry) for entry in file_entries} == {_operation_id(started)}
    assert _operation_id(completed) == _operation_id(started)


def test_run_captures_os_remove(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    # The file must be known to the Store before it can be deleted.
    # Create it via a prior run so the workspace tree contains it.
    creator = _write_script(
        tmp_path,
        "creator.py",
        """\
with open("doomed.txt", "w") as f:
    f.write("bye")
""",
    )
    result = runner.invoke(main, ["run", creator])
    assert result.exit_code == 0, result.output
    # T4b note: the pre-T4 anti-pattern sweep flagged this site as
    # ``runner.invoke(main, ["push"])`` with no ``result.exit_code``
    # assertion (per readiness baseline). Investigation showed the push
    # call was incidental — it was never expected to succeed because the
    # test writes script files (creator.py, deleter.py) directly into
    # the worktree without adopting them via ``vcs-core init --adopt``.
    # The push call was effectively a no-op when its result was ignored.
    # The test's actual subject is the run-time capture / removal
    # semantics that follow; removing the unused push call clarifies
    # intent without changing what the test validates.

    script = _write_script(
        tmp_path,
        "deleter.py",
        """\
import os
os.remove("doomed.txt")
""",
    )

    result = runner.invoke(main, ["run", script])
    assert result.exit_code == 0, result.output
    assert "1 file effect(s) captured" in result.output
    assert not (tmp_path / "doomed.txt").exists()


# ---------------------------------------------------------------------------
# Scope naming
# ---------------------------------------------------------------------------


def test_run_default_scope_name_from_script(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "my_task.py",
        """\
with open("out.txt", "w") as f:
    f.write("ok")
""",
    )

    result = runner.invoke(main, ["run", script])
    assert result.exit_code == 0, result.output
    assert "run-my_task" in result.output


def test_run_custom_scope_name(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "work.py",
        """\
with open("out.txt", "w") as f:
    f.write("ok")
""",
    )

    result = runner.invoke(main, ["run", "--scope", "custom-name", script])
    assert result.exit_code == 0, result.output
    assert "Merged 'custom-name'" in result.output


# ---------------------------------------------------------------------------
# Args passthrough
# ---------------------------------------------------------------------------


def test_run_passes_args_to_script(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "argecho.py",
        """\
import sys
with open("args.txt", "w") as f:
    f.write("\\n".join(sys.argv[1:]))
""",
    )

    result = runner.invoke(main, ["run", script, "--", "hello", "world"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "args.txt").read_text() == "hello\nworld"


def test_run_restores_sys_argv(tmp_path: Path) -> None:
    """sys.argv is restored after the run command completes."""
    import sys

    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(tmp_path, "noop.py", "pass\n")

    saved = sys.argv[:]
    runner.invoke(main, ["run", script, "--", "arg1"])
    assert sys.argv == saved


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_run_error_keep_preserves_scope(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "boom.py",
        """\
with open("partial.txt", "w") as f:
    f.write("before crash")
raise ValueError("boom")
""",
    )

    result = runner.invoke(main, ["run", "--on-error", "keep", script])
    assert result.exit_code != 0
    assert "kept for inspection" in result.output

    store = Store(str(tmp_path / ".vcscore"))
    assert not (tmp_path / ".vcscore" / "cli_state.json").exists()
    scope_entry = _scope_registry_entry(store, "run-boom")
    assert scope_entry.status == "live"
    entries = store.log(ref=scope_entry.ref, max_count=10)
    completed = next(entry for entry in entries if entry.metadata.get("type") == "OperationCompleted")
    file_entry = next(entry for entry in entries if entry.metadata.get("type") == "FileCreate")
    started = next(entry for entry in entries if entry.metadata.get("type") == "OperationStarted")

    assert completed.metadata["mg"]["operation"]["result"] == "error"
    assert started.metadata["mg"]["operation"]["label"] == "run-boom"
    assert file_entry.metadata["path"] == "partial.txt"
    assert _operation_id(file_entry) == _operation_id(started)
    assert _operation_id(completed) == _operation_id(started)

    merge = runner.invoke(main, ["merge", "run-boom"])
    assert merge.exit_code == 0, merge.output

    ground_entries = store.log(ref=store.GROUND_REF, max_count=10)
    assert any(
        entry.metadata.get("type") == "OperationCompleted"
        and entry.metadata.get("mg", {}).get("operation", {}).get("result") == "error"
        and entry.metadata.get("mg", {}).get("operation", {}).get("label") == "run-boom"
        for entry in ground_entries
    )
    assert any(
        entry.metadata.get("type") == "FileCreate" and entry.metadata.get("path") == "partial.txt"
        for entry in ground_entries
    )


def test_run_error_discard_removes_scope(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "boom.py",
        """\
with open("partial.txt", "w") as f:
    f.write("before crash")
raise ValueError("boom")
""",
    )

    result = runner.invoke(main, ["run", "--on-error", "discard", script])
    assert result.exit_code != 0
    assert "Discarded scope 'run-boom'" in result.output

    store = Store(str(tmp_path / ".vcscore"))
    assert not (tmp_path / ".vcscore" / "cli_state.json").exists()
    assert _scope_registry_entry(store, "run-boom").status == "discarded"
    archive_ref = next(ref for ref in store.list_archive_refs() if ref.startswith("refs/vcscore/archive/run-boom-"))
    archive_entries = store.log(ref=archive_ref, max_count=10)
    assert any(entry.metadata.get("type") == "DiscardSnapshot" for entry in archive_entries)
    completed = next(entry for entry in archive_entries if entry.metadata.get("type") == "OperationCompleted")
    file_entry = next(entry for entry in archive_entries if entry.metadata.get("type") == "FileCreate")
    started = next(entry for entry in archive_entries if entry.metadata.get("type") == "OperationStarted")

    assert completed.metadata["mg"]["operation"]["result"] == "error"
    assert started.metadata["mg"]["operation"]["label"] == "run-boom"
    assert file_entry.metadata["path"] == "partial.txt"
    assert _operation_id(file_entry) == _operation_id(started)
    assert _operation_id(completed) == _operation_id(started)
    assert (tmp_path / "partial.txt").read_text() == "before crash"


def test_run_nonzero_exit_keep(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "exit42.py",
        """\
import sys
with open("before_exit.txt", "w") as f:
    f.write("wrote this")
sys.exit(42)
""",
    )

    result = runner.invoke(main, ["run", "--on-error", "keep", script])
    assert result.exit_code == 42
    assert "exited with code 42" in result.output
    assert "kept for inspection" in result.output


def test_run_nonzero_exit_discard(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "exit42.py",
        """\
import sys
sys.exit(1)
""",
    )

    result = runner.invoke(main, ["run", "--on-error", "discard", script])
    assert result.exit_code == 1
    assert "Discarded scope" in result.output


def test_run_clean_exit_zero_succeeds(tmp_path: Path) -> None:
    """sys.exit(0) is treated as success."""
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "clean_exit.py",
        """\
import sys
with open("ok.txt", "w") as f:
    f.write("ok")
sys.exit(0)
""",
    )

    result = runner.invoke(main, ["run", script])
    assert result.exit_code == 0, result.output
    assert "Merged" in result.output


# ---------------------------------------------------------------------------
# Nested scopes
# ---------------------------------------------------------------------------


def test_run_nested_under_parent_scope(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "write_file.py",
        """\
with open("nested.txt", "w") as f:
    f.write("from nested scope")
""",
    )

    # Create a parent scope, run inside it, then merge
    result = runner.invoke(main, ["branch", "task-parent"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(main, ["run", "--scope", "tool-0", "--parent", "task-parent", script])
    assert result.exit_code == 0, result.output
    assert "Merged 'tool-0'" in result.output

    result = runner.invoke(main, ["merge", "task-parent"])
    assert result.exit_code == 0, result.output


def test_run_uses_app_restored_parent_scope(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "write_file.py",
        """\
with open("nested.txt", "w") as f:
    f.write("from nested scope")
""",
    )

    result = runner.invoke(main, ["branch", "task-parent"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(main, ["run", "--scope", "tool-0", "--parent", "task-parent", script])
    assert result.exit_code == 0, result.output
    assert "Merged 'tool-0'" in result.output


def test_run_multiple_nested_runs(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script_a = _write_script(
        tmp_path,
        "step_a.py",
        """\
with open("a.txt", "w") as f:
    f.write("step a")
""",
    )
    script_b = _write_script(
        tmp_path,
        "step_b.py",
        """\
with open("b.txt", "w") as f:
    f.write("step b")
""",
    )

    runner.invoke(main, ["branch", "task"])
    runner.invoke(main, ["run", "--scope", "tool-0", "--parent", "task", script_a])
    runner.invoke(main, ["run", "--scope", "tool-1", "--parent", "task", script_b])
    runner.invoke(main, ["merge", "task"])

    result = runner.invoke(main, ["log", "--graph"])
    assert result.exit_code == 0
    assert "tool-0" in result.output
    assert "tool-1" in result.output
    assert "a.txt" in result.output
    assert "b.txt" in result.output


# ---------------------------------------------------------------------------
# Graph output
# ---------------------------------------------------------------------------


def test_run_shows_in_graph(tmp_path: Path) -> None:
    runner = CliRunner()
    _init(runner, tmp_path)
    script = _write_script(
        tmp_path,
        "traced.py",
        """\
with open("traced.txt", "w") as f:
    f.write("visible in graph")
""",
    )

    runner.invoke(main, ["run", script])

    result = runner.invoke(main, ["log", "--graph"])
    assert result.exit_code == 0
    assert "run-traced" in result.output
    assert "FileCreate" in result.output
    assert "traced.txt" in result.output
    assert "ScopeMerge" in result.output
