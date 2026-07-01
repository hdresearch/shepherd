from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from shepherd.cli import _workspace_layout as workspace_layout
from shepherd.cli._workspace_layout import (
    WorkspaceLayoutError,
    detect_layout,
    find_workspace_root,
    new_shepherd_package_dir,
    new_shepherd_workspace_member,
    require_workspace_root,
    workspace_member_covers,
)


def _write_workspace_pyproject(path: Path, members: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    members_block = ",\n".join(f'    "{member}"' for member in members)
    path.write_text(
        f'[project]\nname = "workspace"\nversion = "0.1.0"\n\n[tool.uv.workspace]\nmembers = [\n{members_block}\n]\n',
        encoding="utf-8",
    )


def _write_package_pyproject(package_dir: Path, name: str) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )


def _standalone_module_source() -> str:
    module_path = Path(__file__).resolve().parents[2] / "src" / "shepherd" / "cli" / "_workspace_layout.py"
    return module_path.read_text(encoding="utf-8")


def _repo_impl_source() -> str:
    module_path = Path(__file__).resolve().parents[5] / "scripts" / "_workspace_layout_impl.py"
    return module_path.read_text(encoding="utf-8")


def test_detects_flat_layout_and_package_dirs(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_workspace_pyproject(root / "pyproject.toml", ["packages/shepherd", "packages/vcs-core"])
    _write_package_pyproject(root / "packages" / "shepherd", "shepherd")
    _write_package_pyproject(root / "packages" / "vcs-core", "vcs-core")

    assert detect_layout(root) == "flat"
    assert new_shepherd_package_dir(root, "payments") == root / "packages" / "shepherd-payments"
    assert new_shepherd_workspace_member(root, "payments") == "packages/shepherd-payments"


def test_in_checkout_adapter_delegates_flat_layout_helpers(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_workspace_pyproject(root / "pyproject.toml", ["packages/shepherd", "packages/vcs-core"])
    _write_package_pyproject(root / "packages" / "shepherd", "shepherd")
    _write_package_pyproject(root / "packages" / "vcs-core", "vcs-core")
    (root / "integration-tests").mkdir(parents=True)
    (root / "design" / "vcs-core").mkdir(parents=True)

    assert workspace_layout.iter_workspace_package_dirs(root) == (
        root / "packages" / "shepherd",
        root / "packages" / "vcs-core",
    )
    assert workspace_layout.workspace_collection_targets(root) == (root / "packages",)
    assert workspace_layout.package_dir(root, "shepherd") == root / "packages" / "shepherd"
    assert workspace_layout.package_dir_map(root)["vcs-core"] == root / "packages" / "vcs-core"
    assert workspace_layout.integration_tests_dir(root) == root / "integration-tests"
    assert workspace_layout.project_docs_dir(root, "shepherd", "design") is None
    assert workspace_layout.project_docs_dir(root, "vcs-core", "design") == root / "design" / "vcs-core"


def test_detects_nested_layout_and_uses_project_names_from_pyproject(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_workspace_pyproject(
        root / "pyproject.toml",
        ["shepherd/packages/*", "shepherd/extras/*", "vcs-core/packages/*", "vcs-core/extras/*"],
    )
    _write_package_pyproject(root / "shepherd" / "packages" / "meta", "shepherd")
    _write_package_pyproject(root / "shepherd" / "extras" / "payments", "shepherd-payments")
    _write_package_pyproject(root / "vcs-core" / "packages" / "core", "vcs-core")

    assert detect_layout(root) == "nested"
    assert new_shepherd_package_dir(root, "payments") == root / "shepherd" / "extras" / "payments"
    assert new_shepherd_workspace_member(root, "payments") == "shepherd/extras/payments"
    assert workspace_member_covers(root, "shepherd/extras/payments")


def test_in_checkout_adapter_delegates_nested_layout_helpers(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_workspace_pyproject(
        root / "pyproject.toml",
        ["shepherd/packages/*", "shepherd/extras/*", "vcs-core/packages/*", "vcs-core/extras/*"],
    )
    _write_package_pyproject(root / "shepherd" / "packages" / "meta", "shepherd")
    _write_package_pyproject(root / "shepherd" / "extras" / "payments", "shepherd-payments")
    _write_package_pyproject(root / "vcs-core" / "packages" / "core", "vcs-core")
    (root / "shepherd" / "integration-tests").mkdir(parents=True)
    (root / "vcs-core" / "design").mkdir(parents=True)

    assert workspace_layout.iter_workspace_package_dirs(root) == (
        root / "shepherd" / "packages" / "meta",
        root / "shepherd" / "extras" / "payments",
        root / "vcs-core" / "packages" / "core",
    )
    assert workspace_layout.workspace_collection_targets(root) == (
        root / "shepherd" / "packages",
        root / "shepherd" / "extras",
        root / "vcs-core" / "packages",
    )
    assert workspace_layout.package_dir(root, "shepherd") == root / "shepherd" / "packages" / "meta"
    assert workspace_layout.package_dir(root, "vcs-core") == root / "vcs-core" / "packages" / "core"
    assert workspace_layout.integration_tests_dir(root) == root / "shepherd" / "integration-tests"
    assert workspace_layout.project_docs_dir(root, "vcs-core", "design") == root / "vcs-core" / "design"


def test_workspace_member_covers_explicit_and_glob_members(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_workspace_pyproject(root / "pyproject.toml", ["packages/shepherd", "shepherd/extras/*"])

    assert workspace_member_covers(root, "packages/shepherd")
    assert workspace_member_covers(root, "shepherd/extras/payments")
    assert not workspace_member_covers(root, "packages/shepherd-payments")


def test_find_workspace_root_walks_up_from_nested_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_workspace_pyproject(root / "pyproject.toml", ["packages/shepherd"])
    nested = root / "packages" / "shepherd" / "src" / "shepherd"
    nested.mkdir(parents=True)

    assert find_workspace_root(nested) == root
    assert require_workspace_root(nested) == root


def test_require_workspace_root_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceLayoutError):
        require_workspace_root(tmp_path / "missing")


def test_flat_workspace_members_win_even_if_project_dirs_exist(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_workspace_pyproject(root / "pyproject.toml", ["packages/shepherd"])
    (root / "shepherd").mkdir()
    (root / "vcs-core").mkdir()

    assert detect_layout(root) == "flat"
    assert new_shepherd_package_dir(root, "payments") == root / "packages" / "shepherd-payments"


def test_umbrella_workspace_member_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_workspace_pyproject(root / "pyproject.toml", ["packages"])

    with pytest.raises(WorkspaceLayoutError, match="Could not detect a supported repo layout"):
        detect_layout(root)


def test_standalone_repo_only_helpers_raise(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_workspace_pyproject(root / "pyproject.toml", ["packages/shepherd"])
    module_path = tmp_path / "standalone" / "shepherd" / "cli" / "_workspace_layout.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(_standalone_module_source(), encoding="utf-8")

    code = (
        "import importlib.util\n"
        "import json\n"
        "from pathlib import Path\n"
        f"module_path = {str(module_path)!r}\n"
        "spec = importlib.util.spec_from_file_location('standalone_workspace_layout', module_path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        f"root = Path({str(root)!r})\n"
        "helpers = [\n"
        "    ('iter_workspace_package_dirs', lambda: module.iter_workspace_package_dirs(root)),\n"
        "    ('workspace_collection_targets', lambda: module.workspace_collection_targets(root)),\n"
        "    ('package_dir_map', lambda: module.package_dir_map(root)),\n"
        "    ('package_dir', lambda: module.package_dir(root, 'shepherd')),\n"
        "    ('integration_tests_dir', lambda: module.integration_tests_dir(root)),\n"
        "    ('project_docs_dir', lambda: module.project_docs_dir(root, 'vcs-core', 'design')),\n"
        "]\n"
        "payload = {}\n"
        "for helper_name, helper in helpers:\n"
        "    try:\n"
        "        helper()\n"
        "    except Exception as exc:\n"
        "        payload[helper_name] = type(exc).__name__ + ': ' + str(exc)\n"
        "print(json.dumps(payload, sort_keys=True))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "integration_tests_dir": (
            "WorkspaceLayoutError: integration_tests_dir is only available when running inside the source checkout."
        ),
        "iter_workspace_package_dirs": (
            "WorkspaceLayoutError: iter_workspace_package_dirs is only available when running inside the source checkout."
        ),
        "package_dir": "WorkspaceLayoutError: package_dir is only available when running inside the source checkout.",
        "package_dir_map": (
            "WorkspaceLayoutError: package_dir_map is only available when running inside the source checkout."
        ),
        "project_docs_dir": (
            "WorkspaceLayoutError: project_docs_dir is only available when running inside the source checkout."
        ),
        "workspace_collection_targets": (
            "WorkspaceLayoutError: workspace_collection_targets is only available when running inside the source checkout."
        ),
    }


def test_fallback_ignores_foreign_workspace_helper(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    _write_workspace_pyproject(workspace_root / "pyproject.toml", ["packages/shepherd"])
    (workspace_root / "packages").mkdir(parents=True)
    (workspace_root / "scripts").mkdir(parents=True)
    (workspace_root / "scripts" / "_workspace_layout_impl.py").write_text(
        "raise RuntimeError('foreign helper should not be imported')\n",
        encoding="utf-8",
    )
    module_path = tmp_path / "standalone" / "shepherd" / "cli" / "_workspace_layout.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(_standalone_module_source(), encoding="utf-8")

    code = (
        "import importlib.util\n"
        "import json\n"
        f"module_path = {str(module_path)!r}\n"
        "spec = importlib.util.spec_from_file_location('standalone_workspace_layout', module_path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "root = module.require_workspace_root()\n"
        "payload = {\n"
        "    'layout': module.detect_layout(root),\n"
        "    'new_dir': str(module.new_shepherd_package_dir(root, 'payments')),\n"
        "    'member': module.new_shepherd_workspace_member(root, 'payments'),\n"
        "    'covers': module.workspace_member_covers(root, 'packages/shepherd'),\n"
        "}\n"
        "try:\n"
        "    module.project_docs_dir(root, 'vcs-core', 'design')\n"
        "except Exception as exc:\n"
        "    payload['project_docs_error'] = type(exc).__name__ + ': ' + str(exc)\n"
        "print(json.dumps(payload))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "layout": "flat",
        "new_dir": str(workspace_root / "packages" / "shepherd-payments"),
        "member": "packages/shepherd-payments",
        "covers": True,
        "project_docs_error": (
            "WorkspaceLayoutError: project_docs_dir is only available when running inside the source checkout."
        ),
    }


def test_installed_module_inside_repo_uses_fallback_instead_of_repo_helper(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    _write_workspace_pyproject(workspace_root / "pyproject.toml", ["packages/shepherd"])
    _write_package_pyproject(workspace_root / "packages" / "shepherd", "shepherd")
    (workspace_root / "scripts").mkdir(parents=True)
    (workspace_root / "scripts" / "_workspace_layout_impl.py").write_text(_repo_impl_source(), encoding="utf-8")

    module_path = (
        workspace_root / ".venv" / "lib" / "python3.11" / "site-packages" / "shepherd" / "cli" / "_workspace_layout.py"
    )
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(_standalone_module_source(), encoding="utf-8")

    code = (
        "import importlib.util\n"
        "import json\n"
        f"module_path = {str(module_path)!r}\n"
        "spec = importlib.util.spec_from_file_location('installed_workspace_layout', module_path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "root = module.require_workspace_root()\n"
        "payload = {\n"
        "    'delegated': module._repo_impl() is not None,\n"
        "    'layout': module.detect_layout(root),\n"
        "    'new_dir': str(module.new_shepherd_package_dir(root, 'payments')),\n"
        "    'member': module.new_shepherd_workspace_member(root, 'payments'),\n"
        "    'covers': module.workspace_member_covers(root, 'packages/shepherd'),\n"
        "}\n"
        "try:\n"
        "    module.project_docs_dir(root, 'vcs-core', 'design')\n"
        "except Exception as exc:\n"
        "    payload['project_docs_error'] = type(exc).__name__ + ': ' + str(exc)\n"
        "print(json.dumps(payload, sort_keys=True))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "covers": True,
        "delegated": False,
        "layout": "flat",
        "member": "packages/shepherd-payments",
        "new_dir": str(workspace_root / "packages" / "shepherd-payments"),
        "project_docs_error": (
            "WorkspaceLayoutError: project_docs_dir is only available when running inside the source checkout."
        ),
    }


def test_installed_module_inside_repo_does_not_import_repo_helper(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    _write_workspace_pyproject(workspace_root / "pyproject.toml", ["packages/shepherd"])
    _write_package_pyproject(workspace_root / "packages" / "shepherd", "shepherd")
    (workspace_root / "scripts").mkdir(parents=True)
    (workspace_root / "scripts" / "_workspace_layout_impl.py").write_text(
        "raise RuntimeError('repo helper imported')\n",
        encoding="utf-8",
    )

    module_path = (
        workspace_root / ".venv" / "lib" / "python3.11" / "site-packages" / "shepherd" / "cli" / "_workspace_layout.py"
    )
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(_standalone_module_source(), encoding="utf-8")

    code = (
        "import importlib.util\n"
        "import json\n"
        f"module_path = {str(module_path)!r}\n"
        "spec = importlib.util.spec_from_file_location('installed_workspace_layout', module_path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "root = module.require_workspace_root()\n"
        "payload = {\n"
        "    'delegated': module._repo_impl() is not None,\n"
        "    'layout': module.detect_layout(root),\n"
        "}\n"
        "print(json.dumps(payload, sort_keys=True))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"delegated": False, "layout": "flat"}
