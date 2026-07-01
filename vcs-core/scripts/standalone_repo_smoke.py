#!/usr/bin/env python3
"""Validate the documented vcs-core repo-root smoke flow in a copied tree."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from textwrap import dedent


SOURCE_ROOT = Path(__file__).resolve().parents[1]
COMMONS_VCS_DIR_NAME = "commons-vcs"
IGNORED_NAMES = (
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".podman",
    "__pycache__",
    ".jj",
    ".git",
    "build",
    "dist",
)


def _copy_source_tree(source_root: Path, work_root: Path) -> Path:
    standalone_root = work_root / source_root.name
    shutil.copytree(source_root, standalone_root, ignore=shutil.ignore_patterns(*IGNORED_NAMES))
    commons_vcs_root = source_root.parent / COMMONS_VCS_DIR_NAME
    if commons_vcs_root.is_dir():
        shutil.copytree(
            commons_vcs_root,
            work_root / COMMONS_VCS_DIR_NAME,
            ignore=shutil.ignore_patterns(*IGNORED_NAMES),
        )
    else:
        msg = f"required sibling dependency missing: {commons_vcs_root}"
        raise AssertionError(msg)
    (work_root / "pyproject.toml").write_text(
        dedent(
            f"""\
            [tool.uv.workspace]
            members = [
                "{source_root.name}/packages/*",
                "{COMMONS_VCS_DIR_NAME}",
            ]

            [tool.uv.sources]
            vcs-core = {{ workspace = true }}
            commons-vcs = {{ workspace = true }}
            """
        ),
        encoding="utf-8",
    )
    return standalone_root


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("PYTHONPATH", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "PYTHONHOME", "UV_FROZEN"):
        env.pop(key, None)
    return env


def _run(args: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    completed = subprocess.run(args, cwd=cwd, env=env, check=False, capture_output=True, text=True)
    if completed.returncode == 0:
        return
    msg = (
        f"command failed: {' '.join(args)}\n"
        f"cwd: {cwd}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    raise AssertionError(msg)

def run_standalone_repo_smoke(*, source_root: Path, work_root: Path) -> Path:
    standalone_root = _copy_source_tree(source_root, work_root)
    env = _clean_env()
    env["UV_CACHE_DIR"] = str(work_root / ".uv-cache")
    _run(["uv", "sync", "--package", "vcs-core", "--all-groups"], cwd=work_root, env=env)
    _run(["make", "-C", "packages/core", "smoke"], cwd=standalone_root, env=env)
    return standalone_root


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=SOURCE_ROOT,
        help="Source vcs-core tree to copy before running the repo-root smoke.",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        help="Optional directory to hold the copied tree. Defaults to a temporary directory.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.work_root is not None:
        args.work_root.mkdir(parents=True, exist_ok=True)
        run_standalone_repo_smoke(source_root=args.source_root.resolve(), work_root=args.work_root.resolve())
        return 0

    with tempfile.TemporaryDirectory(prefix="vcs-core-standalone-") as tmp_dir:
        run_standalone_repo_smoke(source_root=args.source_root.resolve(), work_root=Path(tmp_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
