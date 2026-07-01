"""Internal feature-flag scopes used by workspace-control retained-output paths."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


@contextmanager
def _seal_and_select_enabled() -> Any:
    with _env_enabled({"VCS_CORE_SEAL_AND_SELECT": "1"}):
        yield


@contextmanager
def _env_enabled(required: Mapping[str, str]) -> Any:
    old_values = {name: os.environ.get(name) for name in required}
    os.environ.update(required)
    try:
        yield
    finally:
        for name, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_value
