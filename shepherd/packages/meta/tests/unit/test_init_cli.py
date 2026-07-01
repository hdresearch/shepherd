from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

init_module = importlib.import_module("shepherd.cli.init")
package_module = importlib.import_module("shepherd.cli.package")
cli_module = importlib.import_module("shepherd.cli")
workspace_init = init_module.init
package_init = package_module.init


def _assert_generated_nucleus_templates(package_dir: Path, module_name: str) -> None:
    tasks_path = package_dir / "src" / f"shepherd_{module_name}" / "tasks.py"
    test_path = package_dir / "tests" / "test_tasks.py"
    tasks_text = tasks_path.read_text(encoding="utf-8")
    test_text = test_path.read_text(encoding="utf-8")

    assert "{{" not in tasks_text
    assert "{{" not in test_text
    assert "from shepherd import task" in tasks_text
    assert f"def hello_{module_name}(name: str) -> str:" in tasks_text
    assert "MockProvider" not in tasks_text
    assert "MockProvider" not in test_text
    assert "Input" not in tasks_text
    assert "Output" not in tasks_text
    compile(tasks_text, str(tasks_path), "exec")
    compile(test_text, str(test_path), "exec")


def _write_workspace_pyproject(path: Path, members: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    members_block = ",\n".join(f'    "{member}"' for member in members)
    path.write_text(
        "[project]\n"
        'name = "workspace"\n'
        'version = "0.1.0"\n\n'
        "[tool.uv.workspace]\n"
        "members = [\n"
        f"{members_block}\n"
        "]\n\n"
        "[tool.uv.sources]\n"
        "shepherd = { workspace = true }\n",
        encoding="utf-8",
    )


def test_package_init_creates_flat_layout_package_and_updates_workspace(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_workspace_pyproject(root / "pyproject.toml", ["packages/shepherd"])
    (root / "packages").mkdir(parents=True, exist_ok=True)
    (root / "shepherd").mkdir()
    (root / "vcs-core").mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        package_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )

    result = CliRunner().invoke(package_init, ["payments"])

    assert result.exit_code == 0, result.output
    package_dir = root / "packages" / "shepherd-payments"
    assert package_dir.is_dir()
    assert (package_dir / "src" / "shepherd_payments" / "tasks.py").exists()
    _assert_generated_nucleus_templates(package_dir, "payments")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"packages/shepherd-payments"' in pyproject
    assert "shepherd-payments = { workspace = true }" in pyproject


def test_shepherd_cli_exposes_init_run_and_task_groups() -> None:
    runner = CliRunner()

    top = runner.invoke(cli_module.main, ["--help"])
    assert top.exit_code == 0, top.output
    assert "demo" in top.output
    assert "doctor" in top.output
    assert "init" in top.output
    assert "package" in top.output
    assert "run" in top.output
    assert "task" in top.output

    run = runner.invoke(cli_module.main, ["run", "--help"])
    assert run.exit_code == 0, run.output
    assert "trace" in run.output
    assert "trace-revision" in run.output

    task = runner.invoke(cli_module.main, ["task", "--help"])
    assert task.exit_code == 0, task.output
    assert "list" in task.output
    assert "resolve" in task.output
    assert "show" in task.output


def test_package_init_creates_nested_layout_package_without_duplicate_glob_member(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_workspace_pyproject(root / "pyproject.toml", ["shepherd/packages/*", "shepherd/extras/*"])
    (root / "shepherd" / "extras").mkdir(parents=True, exist_ok=True)
    (root / "vcs-core").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        package_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )

    result = CliRunner().invoke(package_init, ["payments"])

    assert result.exit_code == 0, result.output
    package_dir = root / "shepherd" / "extras" / "payments"
    assert package_dir.is_dir()
    assert (package_dir / "src" / "shepherd_payments" / "__init__.py").exists()
    _assert_generated_nucleus_templates(package_dir, "payments")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"shepherd/extras/payments"' not in pyproject
    assert "shepherd-payments = { workspace = true }" in pyproject


def test_package_init_falls_back_to_local_packages_dir_outside_workspace(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    calls: list[object] = []

    def _fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(package_module.subprocess, "run", _fake_run)

    result = CliRunner().invoke(package_init, ["payments"])

    assert result.exit_code == 0, result.output
    package_dir = tmp_path / "packages" / "shepherd-payments"
    assert package_dir.is_dir()
    _assert_generated_nucleus_templates(package_dir, "payments")
    assert calls == []


def test_workspace_init_creates_git_and_vcscore_workspace(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    result = CliRunner().invoke(workspace_init, [str(root)])

    assert result.exit_code == 0, result.output
    assert (root / ".git").exists()
    assert (root / ".vcscore").exists()
    assert "Initialized Shepherd workspace" in result.output
    assert "sp demo write quickstart" in result.output


def test_pyproject_exposes_sp_and_shepherd_scripts() -> None:
    pyproject = Path(__file__).resolve().parents[2].joinpath("pyproject.toml").read_text(encoding="utf-8")

    assert 'shepherd = "shepherd.cli:main"' in pyproject
    assert 'sp = "shepherd.cli:main"' in pyproject
    assert 'shepherd = "shepherd.cli:main"' in pyproject
