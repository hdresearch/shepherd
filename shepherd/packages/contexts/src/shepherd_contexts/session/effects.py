"""Session-specific effects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from shepherd_core.effects import Effect

if TYPE_CHECKING:
    from collections.abc import Mapping


class SessionCreated(Effect):
    """New session was created."""

    effect_type: Literal["session_created"] = "session_created"
    session_id: str = ""
    transcript_path: str | None = None
    cwd: str | None = None
    caused_by: str | None = None


class SessionForked(Effect):
    """Session branched from a parent session."""

    effect_type: Literal["session_forked"] = "session_forked"
    parent_session_id: str = ""
    new_session_id: str = ""
    fork_reason: str | None = None
    transcript_path: str | None = None
    caused_by: str | None = None


class SessionResumed(Effect):
    """Session continued without forking."""

    effect_type: Literal["session_resumed"] = "session_resumed"
    session_id: str = ""
    turn_count: int | None = None


def get_effect_types() -> Mapping[str, type[Effect]]:
    """Return the explicit effect contributor surface for runtime decode."""
    return {
        "session_created": SessionCreated,
        "session_forked": SessionForked,
        "session_resumed": SessionResumed,
    }


__all__ = [
    "SessionCreated",
    "SessionForked",
    "SessionResumed",
    "get_effect_types",
]
