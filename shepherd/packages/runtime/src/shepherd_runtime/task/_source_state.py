"""Shared state for task source capture during reconstruction."""

from __future__ import annotations

from contextvars import ContextVar

reconstruction_source: ContextVar[str | None] = ContextVar("reconstruction_source", default=None)

__all__ = ["reconstruction_source"]
