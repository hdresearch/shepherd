# ruff: noqa: INP001
"""Repo-side entrypoint for shared workspace layout helpers."""

from __future__ import annotations

import _workspace_layout_impl as _impl
from _workspace_layout_impl import *  # noqa: F403

__all__ = list(_impl.__all__)
