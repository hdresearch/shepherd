"""Session context for multi-turn conversation continuity.

This module provides SessionState, an invisible context that enables
multi-turn conversations by tracking session ID.

Example:
    from shepherd_contexts.session import SessionState

    session = SessionState()
    # After first execution, session.session_id is populated
"""

from shepherd_contexts.session.effects import (
    SessionCreated,
    SessionForked,
    SessionResumed,
)
from shepherd_contexts.session.state import SessionState

__all__ = [
    "SessionCreated",
    "SessionForked",
    "SessionResumed",
    "SessionState",
]
