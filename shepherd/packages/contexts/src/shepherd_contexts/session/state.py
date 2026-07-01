"""SessionState: Invisible context for conversation continuity.

This is a simple reference implementation demonstrating:
- Invisible context (not in LLM prompts)
- Abstract session_isolation field (providers translate)
- Session capture from execution result

v2 API:
- extract_effects(sandbox, result): Extract session effects from result (PURE)
- apply_effect(effect): Derive new state from effect (PURE)

The v2 API enables:
- Time-travel debugging (reconstruct state by replaying effects)
- Speculative execution (fork, run, approve/reject)
- Effect-sourced state derivation
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Self

from shepherd_core.constants import SDK_BUFFER_LIMIT_BYTES
from shepherd_core.foundation.protocols.device import ContextStateBase
from shepherd_core.types import (
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ReversibilityLevel,
)
from shepherd_runtime.context import BindableContext, Sandbox

from shepherd_contexts.session.effects import (
    SessionCreated,
    SessionForked,
    SessionResumed,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shepherd_core.effects import Effect

# =============================================================================
# SessionStateData (for container serialization)
# =============================================================================


@dataclass(frozen=True)
class SessionStateData(ContextStateBase):
    """Serializable state for SessionState transfer across device boundaries.

    This dataclass captures everything needed to reconstruct a SessionState
    inside a container sandbox. The rebind() method handles path translation
    from host to container filesystem.

    Attributes:
        session_id: Session identifier (if existing session).
        transcript_path: Path to transcript file (will be rebound for container).
        host_cwd: Working directory when session was created (for container symlinks).
    """

    session_id: str | None = None
    transcript_path: str | None = None
    host_cwd: str | None = None  # CWD when session was created (for container symlinks)

    @property
    def context_type(self) -> str:
        """Type discriminator for deserialization."""
        return "session"

    def rebind(self, env: Mapping[str, str]) -> SessionStateData:
        """Return state with transcript_path rebound for container environment.

        Args:
            env: Environment variables with SESSION_PATH mapping.

        Returns:
            New state with updated transcript_path.
        """
        new_path = env.get("SESSION_PATH")
        if new_path and self.transcript_path:
            return replace(self, transcript_path=new_path)
        return self

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionStateData:
        """Deserialize from dictionary.

        Args:
            data: Dictionary with state fields.

        Returns:
            SessionStateData instance.
        """
        return cls(
            session_id=data.get("session_id"),
            transcript_path=data.get("transcript_path"),
            host_cwd=data.get("host_cwd"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary.

        Returns:
            Dictionary representation for JSON serialization.
        """
        return {
            "context_type": self.context_type,
            "session_id": self.session_id,
            "transcript_path": self.transcript_path,
            "host_cwd": self.host_cwd,
        }


# =============================================================================
# Transcript Size Thresholds
# =============================================================================
# Constants anchored to SDK's 1MB buffer limit for transcript size warnings
LARGE_TRANSCRIPT_BYTES = int(SDK_BUFFER_LIMIT_BYTES * 0.10)  # ~100KB - warning threshold
DANGER_TRANSCRIPT_BYTES = int(SDK_BUFFER_LIMIT_BYTES * 0.50)  # ~500KB - danger threshold


@dataclass(frozen=True)
class SessionState(BindableContext):
    """Invisible context for multi-turn conversation continuity.

    SessionState enables multi-turn conversations by tracking session ID.
    The provider handles actual session management via the SDK.

    Key characteristics:
    - Invisible: __str__() returns "" so it's excluded from prompts
    - Provider-captured: Output comes from execution result, not self-capture
    - Fork semantics: Each execution forks to a new session ID

    Lifecycle:
        configure(): Return binding with session config (invisible)
        prepare(): No-op
        extract_effects(): Extract session effects from result
        apply_effect(): Derive new state from session effects
        cleanup(): No-op
    """

    __binding_name__: ClassVar[str] = "session"

    session_id: str | None = None
    transcript_path: str | None = None
    host_cwd: str | None = None  # CWD when session was created (for container symlinks)

    @property
    def context_id(self) -> str:
        """Identity based on session_id."""
        return f"session:{self.session_id or 'new'}"

    @property
    def reversibility(self) -> ReversibilityLevel:
        """Sessions support forking/branching - mechanically reversible."""
        return ReversibilityLevel.AUTO

    def __str__(self) -> str:
        """Empty = invisible in prompts."""
        return ""

    def __repr__(self) -> str:
        if self.session_id:
            return f"SessionState({self.session_id[:12]}...)"
        return "SessionState(new)"

    @property
    def has_transcript(self) -> bool:
        """Check if a transcript file exists for this session."""
        if not self.transcript_path:
            return False
        return Path(self.transcript_path).exists()

    def debug_info(self) -> str:
        """Diagnostic information about session state with graduated warnings.

        Provides actionable information about the session including:
        - Session ID (truncated for display)
        - Transcript path and size
        - Size warnings when approaching SDK buffer limits

        Returns:
            Multi-line diagnostic string.

        Example output:
            Session Debug Info
            ==================
            Session ID: abc12345...
            Transcript: /path/to/transcript.jsonl
            Size: 523,264 bytes (512 KB)
            WARNING: Transcript size is large (50% of 1MB SDK limit)
            Suggestion: Consider forking to fresh session if errors occur
        """
        lines = []
        lines.append("Session Debug Info")
        lines.append("=" * 18)

        # Session ID
        if self.session_id:
            display_id = f"{self.session_id[:12]}..." if len(self.session_id) > 12 else self.session_id
            lines.append(f"Session ID: {display_id}")
        else:
            lines.append("Session ID: (new session)")

        # Transcript info
        if self.transcript_path:
            lines.append(f"Transcript: {self.transcript_path}")

            # Check file size
            path = Path(self.transcript_path)
            if path.exists():
                size_bytes = path.stat().st_size
                size_kb = size_bytes / 1024
                size_mb = size_bytes / (1024 * 1024)

                if size_mb >= 1:
                    lines.append(f"Size: {size_bytes:,} bytes ({size_mb:.1f} MB)")
                else:
                    lines.append(f"Size: {size_bytes:,} bytes ({size_kb:.0f} KB)")

                # Graduated warnings
                percentage = (size_bytes / SDK_BUFFER_LIMIT_BYTES) * 100

                if size_bytes >= DANGER_TRANSCRIPT_BYTES:
                    lines.append(f"DANGER: Transcript at {percentage:.0f}% of 1MB SDK buffer limit!")
                    lines.append("Action: Fork to fresh session immediately to avoid buffer overflow")
                elif size_bytes >= LARGE_TRANSCRIPT_BYTES:
                    lines.append(f"WARNING: Transcript at {percentage:.0f}% of 1MB SDK limit")
                    lines.append("Suggestion: Consider forking to fresh session if errors occur")
            else:
                lines.append("Size: (file not found)")
        else:
            lines.append("Transcript: (none)")

        return "\n".join(lines)

    # === Configuration ===

    def configure(
        self,
        capabilities: ProviderCapabilities | None = None,
    ) -> ProviderBinding:
        """Return binding - invisible but configures provider.

        Uses abstract session_isolation field - providers translate:
        - Claude: fork_session=True for "forked"
        - OpenAI: conversation_mode based on isolation
        """
        # Check if provider supports sessions
        if capabilities and not capabilities.supports_session:
            return ProviderBinding(
                context_id=self.context_id,
                context_type="SessionState",
                visible=False,
            )

        # Determine session isolation semantics:
        # - If we have a session_id, fork from it (creates new branch)
        # - If no session_id, start isolated (fresh session)
        isolation = "forked" if self.session_id else "isolated"

        return ProviderBinding(
            context_id=self.context_id,
            context_type="SessionState",
            visible=False,  # Never in prompts
            session_id=self.session_id,
            session_isolation=isolation,
        )

    # === State Serialization (for container transfer) ===

    def to_state(self) -> SessionStateData:
        """Serialize session to transportable state.

        Creates a SessionStateData that can be serialized to JSON and
        transferred across device boundaries.

        Returns:
            SessionStateData with session_id, transcript_path, and host_cwd.
        """
        return SessionStateData(
            session_id=self.session_id,
            transcript_path=self.transcript_path,
            host_cwd=self.host_cwd,  # Captured at creation time, not current cwd()
        )

    @classmethod
    def from_state(
        cls,
        state: SessionStateData,
        sandbox_path: Path | str | None = None,
    ) -> SessionState:
        """Reconstruct session from state.

        Creates a SessionState from a deserialized SessionStateData.
        If sandbox_path is provided, it overrides the transcript_path
        (useful when the container mounts session at different location).

        Args:
            state: SessionStateData from deserialization.
            sandbox_path: Optional override for transcript path.

        Returns:
            SessionState instance.
        """
        # Use sandbox_path if provided, otherwise state.transcript_path
        transcript_path = str(sandbox_path) if sandbox_path else state.transcript_path

        return cls(
            session_id=state.session_id,
            transcript_path=transcript_path,
            host_cwd=state.host_cwd,
        )

    # === v2 API: Effect-Driven State Derivation ===

    def extract_effects(
        self,
        sandbox: Sandbox | None,
        result: ExecutionResult,
    ) -> Sequence[Effect]:
        """Extract session effects from result. PURE.

        This method extracts:
        - SessionCreated: When first session is created
        - SessionForked: When session branches from existing
        - SessionResumed: When session continues without change (audit only)

        Args:
            sandbox: Ignored (sessions don't use sandbox)
            result: Execution result with session_id

        Returns:
            Sequence of effects (not yet attributed - lifecycle adds attribution)
        """
        new_session_id = result.session_id

        if not new_session_id:
            # No session in result - no effects
            return []

        # Extract transcript_path and cwd from metadata (set by ClaudeProvider)
        transcript_path = result.metadata.get("transcript_path")
        cwd = result.metadata.get("cwd")

        if self.session_id is None:
            # New session created
            return [
                SessionCreated(
                    session_id=new_session_id,
                    context_id=self.context_id,
                    transcript_path=transcript_path,
                    cwd=cwd,  # Capture CWD at creation time for container symlinks
                )
            ]

        if new_session_id != self.session_id:
            # Session forked to new branch
            return [
                SessionForked(
                    parent_session_id=self.session_id,
                    new_session_id=new_session_id,
                    context_id=self.context_id,
                    transcript_path=transcript_path,
                )
            ]

        # Session resumed without change (audit only)
        return [
            SessionResumed(
                session_id=new_session_id,
                context_id=self.context_id,
            )
        ]

    def apply_effect(self, effect: Effect) -> Self:
        """Apply effect to derive new context state. PURE.

        Handles:
        - SessionCreated: Sets session_id and transcript_path
        - SessionForked: Updates session_id and transcript_path to new branch
        - SessionResumed: No state change (audit only)

        Other effects are ignored (they don't affect session state).

        Note: We do NOT filter by context_id here because SessionState's context_id
        is derived from session_id, which changes during effect application. The
        lifecycle routes effects to us by binding_name (stable), so we can trust
        that we only receive effects intended for this context.

        Args:
            effect: Effect to apply

        Returns:
            New SessionState instance (or self if no state change)
        """
        # Handle session effects
        if isinstance(effect, SessionCreated):
            return replace(
                self,
                session_id=effect.session_id,
                transcript_path=effect.transcript_path,
                host_cwd=effect.cwd,  # Capture CWD at creation time
            )

        if isinstance(effect, SessionForked):
            # Preserve host_cwd from parent session (don't overwrite)
            return replace(
                self,
                session_id=effect.new_session_id,
                transcript_path=effect.transcript_path,
                # host_cwd unchanged - still the original creation cwd
            )

        if isinstance(effect, SessionResumed):
            # Audit only - no state change
            return self

        return self


__all__ = [
    "DANGER_TRANSCRIPT_BYTES",
    "LARGE_TRANSCRIPT_BYTES",
    "SessionState",
    "SessionStateData",
]


# =============================================================================
# Context Registry Registration
# =============================================================================


def _register_deserializer() -> None:
    """Register SessionStateData deserializer with context registry.

    Called at module import time. Wrapped in function to allow
    graceful handling if device module not yet available.
    """
    try:
        from shepherd_runtime.registry import register_context_deserializer

        register_context_deserializer("session", SessionStateData.from_dict)
    except ImportError:
        # Device module may not be installed - that's OK
        pass


_register_deserializer()
