"""Packaging-level contract checks for installed CLI mode."""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from vcs_core._fs_capture import shim_source_path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict[str, object]:
    with (PACKAGE_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_python_lt_311_tomli_support_is_a_normal_dependency() -> None:
    project = _pyproject()["project"]
    dependencies = project["dependencies"]

    assert "tomli>=2.0; python_version < '3.11'" in dependencies


def test_fs_capture_shim_source_exists_in_package_tree() -> None:
    assert shim_source_path().is_file()
    assert shim_source_path().name == "fs_capture_shim.c"


def test_wheel_metadata_explicitly_includes_fs_capture_source() -> None:
    wheel_target = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert "src/vcs_core/_native/fs_capture_shim.c" in wheel_target["include"]
