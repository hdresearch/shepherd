"""Repo-root shim for the Shepherd package in the nested monorepo layout."""

from __future__ import annotations

from pathlib import Path

_REPO_SHEPHERD_DIR = Path(__file__).resolve().parent
_REAL_PACKAGE_DIR = _REPO_SHEPHERD_DIR / "packages" / "meta" / "src" / "shepherd"
_REAL_INIT = _REAL_PACKAGE_DIR / "__init__.py"

if not _REAL_INIT.is_file():
    raise ImportError(f"Could not find the Shepherd package entrypoint at {_REAL_INIT}.")

__file__ = str(_REAL_INIT)
__path__ = [str(_REAL_PACKAGE_DIR)]

exec(compile(_REAL_INIT.read_text(encoding="utf-8"), __file__, "exec"), globals())
