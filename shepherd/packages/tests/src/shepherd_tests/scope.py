"""Scope helpers for tests."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from shepherd_runtime.scope import Scope

from shepherd_tests.mock_provider import MockProvider

if TYPE_CHECKING:
    from collections.abc import Generator

    from shepherd_core.provider import Provider


def _get_main_script_directory() -> Path:
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None) if main_module is not None else None
    if isinstance(main_file, str) and main_file:
        return Path(main_file).resolve().parent
    return Path.cwd()


def _resolve_cache_path(cache: bool | Path | str) -> Path | None:
    if isinstance(cache, bool):
        return _get_main_script_directory() if cache else None
    return Path(cache).resolve()


@contextmanager
def mock_steps(
    provider: Provider | None = None,
    cache: bool | Path | str = False,
) -> Generator[Scope, None, None]:
    """Run task/step code inside an isolated mock-backed runtime scope."""
    with Scope(root=True, project_path=_resolve_cache_path(cache), _is_global=True) as scope:
        scope.register_provider("default", provider or MockProvider(), default=True)
        yield scope
