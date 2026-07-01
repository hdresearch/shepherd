"""Helpers for wheel-installed CLI integration tests."""

from __future__ import annotations

import json
import os
import subprocess
import venv
import zipfile
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PACKAGE_ROOT / "src"
WORKSPACE_ROOT = PACKAGE_ROOT.parents[2]
COMMONS_VCS_ROOT = WORKSPACE_ROOT / "commons-vcs"


@dataclass(frozen=True)
class InstalledVcsCoreEnv:
    """A wheel-installed vcs-core executable inside an isolated virtual environment."""

    root: Path
    python: Path
    executable: Path


def build_wheel(dist_dir: Path) -> Path:
    """Build a wheel for vcs-core and return its path.

    The installed-mode smoke is a packaging/runtime verification path,
    not a build-backend resolution test. Build against the already-synced
    test environment instead of triggering isolated backend resolution.
    """
    dist_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["uv", "build", "--wheel", "--no-build-isolation", "--out-dir", str(dist_dir)],
        cwd=PACKAGE_ROOT,
        check=True,
        env=_clean_env(),
        capture_output=True,
        text=True,
    )
    wheels = sorted(dist_dir.glob("vcs_core-*.whl"))
    if len(wheels) != 1:
        msg = f"expected one built wheel in {dist_dir}, found {len(wheels)}"
        raise AssertionError(msg)
    return wheels[0]


def build_commons_vcs_wheel(dist_dir: Path) -> Path:
    """Build a wheel for the local commons-vcs dependency and return its path.

    Like the vcs-core wheel smoke, this verifies packaging/runtime behavior
    against the already-synced test environment rather than re-resolving build
    backend dependencies from the network.
    """
    if not COMMONS_VCS_ROOT.is_dir():
        msg = f"local commons-vcs dependency missing: {COMMONS_VCS_ROOT}"
        raise AssertionError(msg)
    dist_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["uv", "build", "--wheel", "--no-build-isolation", "--out-dir", str(dist_dir)],
        cwd=COMMONS_VCS_ROOT,
        check=True,
        env=_clean_env(),
        capture_output=True,
        text=True,
    )
    wheels = sorted(dist_dir.glob("commons_vcs-*.whl"))
    if len(wheels) != 1:
        msg = f"expected one built commons-vcs wheel in {dist_dir}, found {len(wheels)}"
        raise AssertionError(msg)
    return wheels[0]


def wheel_members(wheel: Path) -> set[str]:
    """Return the archived file names for one built wheel."""
    with zipfile.ZipFile(wheel) as archive:
        return set(archive.namelist())


def create_installed_env(root: Path) -> InstalledVcsCoreEnv:
    """Create an isolated virtual environment for installed-wheel tests."""
    builder = venv.EnvBuilder(with_pip=True, system_site_packages=False)
    builder.create(root)
    bin_dir = root / ("Scripts" if os.name == "nt" else "bin")
    executable_name = "vcs-core.exe" if os.name == "nt" else "vcs-core"
    python_name = "python.exe" if os.name == "nt" else "python"
    return InstalledVcsCoreEnv(
        root=root,
        python=bin_dir / python_name,
        executable=bin_dir / executable_name,
    )


def install_wheel(env: InstalledVcsCoreEnv, *wheels: Path) -> None:
    """Install built wheels into the isolated environment."""
    subprocess.run(
        ["uv", "pip", "install", "--python", str(env.python), *(str(wheel) for wheel in wheels)],
        check=True,
        env=_clean_env(),
        capture_output=True,
        text=True,
    )


def assert_installed_provenance(env: InstalledVcsCoreEnv) -> None:
    """Ensure the executable and imported runtime deps come from the isolated environment."""
    if not env.executable.is_file():
        msg = f"installed executable missing: {env.executable}"
        raise AssertionError(msg)

    completed = subprocess.run(
        [
            str(env.python),
            "-c",
            (
                "import json, pathlib, sys; "
                "import click, commons_vcs, vcs_core, pygit2, pydantic; "
                "print(json.dumps({"
                "'module': str(pathlib.Path(vcs_core.__file__).resolve()), "
                "'deps': {"
                "'click': str(pathlib.Path(click.__file__).resolve()), "
                "'commons_vcs': str(pathlib.Path(commons_vcs.__file__).resolve()), "
                "'pygit2': str(pathlib.Path(pygit2.__file__).resolve()), "
                "'pydantic': str(pathlib.Path(pydantic.__file__).resolve())"
                "}, "
                "'sys_path': [str(pathlib.Path(p).resolve()) for p in sys.path if p]"
                "}))"
            ),
        ],
        check=True,
        env=_clean_env(),
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    module_path = Path(payload["module"])
    if env.root not in module_path.parents:
        msg = f"vcs_core imported from outside isolated env: {module_path}"
        raise AssertionError(msg)
    if SOURCE_ROOT in module_path.parents:
        msg = f"vcs_core resolved from source checkout instead of installed wheel: {module_path}"
        raise AssertionError(msg)

    for dep_name, dep_path_str in payload["deps"].items():
        dep_path = Path(dep_path_str)
        if env.root not in dep_path.parents:
            msg = f"{dep_name} imported from outside isolated env: {dep_path}"
            raise AssertionError(msg)

    for sys_path_entry_str in payload["sys_path"]:
        sys_path_entry = Path(sys_path_entry_str)
        if sys_path_entry == SOURCE_ROOT or SOURCE_ROOT in sys_path_entry.parents:
            msg = f"source checkout leaked onto installed sys.path: {sys_path_entry}"
            raise AssertionError(msg)


def assert_installed_public_modules(env: InstalledVcsCoreEnv, module_names: set[str] | list[str]) -> None:
    """Ensure public-looking modules import from the isolated environment."""
    completed = subprocess.run(
        [
            str(env.python),
            "-c",
            (
                "import importlib, json, pathlib, sys; "
                "modules = sys.argv[1:]; "
                "payload = {"
                "name: str(pathlib.Path(importlib.import_module(f'vcs_core.{name}').__file__).resolve()) "
                "for name in modules"
                "}; "
                "print(json.dumps(payload, sort_keys=True))"
            ),
            *sorted(module_names),
        ],
        check=True,
        env=_clean_env(),
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    for module_name, module_path_str in payload.items():
        module_path = Path(module_path_str)
        if env.root not in module_path.parents:
            msg = f"vcs_core.{module_name} imported from outside isolated env: {module_path}"
            raise AssertionError(msg)
        if SOURCE_ROOT in module_path.parents:
            msg = f"vcs_core.{module_name} resolved from source checkout instead of installed wheel: {module_path}"
            raise AssertionError(msg)


def run_installed_cli(
    env: InstalledVcsCoreEnv,
    args: list[str],
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the installed vcs-core executable and return the completed process."""
    return subprocess.run(
        [str(env.executable), *args],
        cwd=cwd,
        check=True,
        env=_clean_env(),
        capture_output=True,
        text=True,
    )


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("PYTHONPATH", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
        env.pop(key, None)
    return env
