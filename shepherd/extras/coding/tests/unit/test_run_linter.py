"""Tests for the function-form run_linter task."""

from __future__ import annotations

import importlib
from typing import Any

from shepherd_coding.models import ToolRunResult
from shepherd_coding.tasks import RunLinter, run_linter
from shepherd_runtime.nucleus import workspace
from shepherd_runtime.scope import Scope


def test_run_linter_function_form_builds_ruff_command(monkeypatch: Any) -> None:
    module = importlib.import_module("shepherd_coding.tasks.run_linter")
    captured: dict[str, Any] = {}

    def fake_run_tool(**kwargs: Any) -> ToolRunResult:
        captured.update(kwargs)
        return ToolRunResult(tool=kwargs["tool_name"], passed=True)

    monkeypatch.setattr(module, "run_tool", fake_run_tool)
    monkeypatch.setattr(module.shutil, "which", lambda binary: f"/bin/{binary}")

    with workspace(model="offline-coding-test"):
        result = run_linter(
            workspace_path="/repo",
            files=["src/app.py", "README.md"],
            fix=True,
        )

    assert result == ToolRunResult(tool="ruff-check", passed=True)
    assert captured["binary"] == "ruff"
    assert captured["cmd"] == ["/bin/ruff", "check", "--fix", "src/app.py"]
    assert captured["cwd"] == "/repo"
    assert captured["timeout"] == 60


def test_run_linter_class_wrapper_delegates_to_shared_runner(monkeypatch: Any) -> None:
    module = importlib.import_module("shepherd_coding.tasks.run_linter")
    captured: dict[str, Any] = {}

    def fake_run_tool(**kwargs: Any) -> ToolRunResult:
        captured.update(kwargs)
        return ToolRunResult(tool=kwargs["tool_name"], passed=True)

    monkeypatch.setattr(module, "run_tool", fake_run_tool)
    monkeypatch.setattr(module.shutil, "which", lambda binary: f"/bin/{binary}")

    with Scope(root=True):
        task = RunLinter(workspace_path="/repo", files=["src/app.py", "docs/index.md"], fix=False)

    assert task.result == ToolRunResult(tool="ruff-check", passed=True)
    assert captured["cmd"] == ["/bin/ruff", "check", "src/app.py"]
