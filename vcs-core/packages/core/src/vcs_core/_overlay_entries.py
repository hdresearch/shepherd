"""Shared overlay diff entry classification."""

from __future__ import annotations

import stat


def unsupported_overlay_entry_kind(mode: int) -> str | None:
    """Return a user-facing unsupported kind, or None for regular files/directories."""
    if stat.S_ISREG(mode) or stat.S_ISDIR(mode):
        return None
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "character-device"
    if stat.S_ISBLK(mode):
        return "block-device"
    return "unsupported"
