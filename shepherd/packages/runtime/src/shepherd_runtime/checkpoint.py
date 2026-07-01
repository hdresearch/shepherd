"""Public runtime checkpoint types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from typing_extensions import Self

if TYPE_CHECKING:
    import types

    from shepherd_runtime._scope.substrate import ImmutableScope, Stream


class CheckpointScopeObserver(Protocol):
    """Runtime-facing checkpoint host contract used by ``Checkpoint``."""

    def snapshot(self) -> ImmutableScope:
        """Return the current immutable scope snapshot."""
        ...

    def restore(self, checkpoint: Checkpoint) -> None:
        """Restore to the given checkpoint."""
        ...


class CheckpointValidationError(ValueError):
    """Raised when checkpoint validation fails during restore."""

    def __init__(
        self,
        checkpoint_name: str,
        reason: str,
        details: str | None = None,
    ):
        self.checkpoint_name = checkpoint_name
        self.reason = reason
        self.details = details
        message = f"Checkpoint '{checkpoint_name}' validation failed: {reason}"
        if details:
            message += f" ({details})"
        super().__init__(message)


@dataclass
class Checkpoint:
    """Named savepoint for runtime scope rollback via stream truncation."""

    name: str
    _scope: CheckpointScopeObserver = field(repr=False)
    _position: int = field(repr=False)
    _binding_count: int = field(repr=False)
    _restored: bool = field(default=False, repr=False)
    _fingerprint: str | None = field(default=None, repr=False)
    _exited: bool = field(default=False, repr=False)

    @property
    def position(self) -> int:
        """Stream position when this checkpoint was created."""
        return self._position

    def _snapshot(self) -> ImmutableScope:
        return self._scope.snapshot()

    @property
    def effects_since(self) -> Stream:
        """Effects emitted since this checkpoint was created."""
        from shepherd_runtime._scope.substrate import Stream

        current_stream = self._snapshot()._stream
        if self._restored or self._position >= len(current_stream._layers):
            return Stream()
        return Stream(
            _layers=current_stream._layers[self._position :],
            _scope_id=current_stream._scope_id,
            _scope_depth=current_stream._scope_depth,
        )

    @property
    def bindings_added(self) -> int:
        """Number of bindings added since this checkpoint was created."""
        if self._restored:
            return 0
        current_count = len(self._snapshot()._bindings)
        return max(0, current_count - self._binding_count)

    @property
    def is_restored(self) -> bool:
        """Whether this checkpoint has been restored."""
        return self._restored

    @property
    def is_stale(self) -> bool:
        """Whether checkpoint was invalidated by a previous restore."""
        if self._restored:
            return False
        return self._position > len(self._snapshot()._stream)

    @property
    def is_active(self) -> bool:
        """Whether checkpoint can still be restored."""
        return not self._restored and not self.is_stale

    def validate(self, strict: bool = False) -> tuple[bool, list[str]]:
        """Validate that this checkpoint can be safely restored."""
        warnings: list[str] = []

        if self._restored:
            return False, ["Checkpoint already restored"]

        if self.is_stale:
            return False, [
                f"Checkpoint stale: position {self._position} exceeds stream length {len(self._snapshot()._stream)}"
            ]

        current_binding_count = len(self._snapshot()._bindings)
        if current_binding_count < self._binding_count:
            issue = (
                f"Binding count decreased: checkpoint recorded {self._binding_count} "
                f"but scope now has {current_binding_count}"
            )
            if strict:
                return False, [issue]
            warnings.append(issue)

        if self._fingerprint is not None:
            current_fingerprint = self._compute_fingerprint()
            if current_fingerprint != self._fingerprint:
                issue = "Stream fingerprint mismatch - stream may have been modified"
                if strict:
                    return False, [issue]
                warnings.append(issue)

        return True, warnings

    def _compute_fingerprint(self) -> str:
        """Compute a fingerprint of stream state at checkpoint position."""
        import hashlib

        stream = self._snapshot()._stream
        if self._position == 0:
            return "empty"

        hasher = hashlib.md5(usedforsecurity=False)
        for i in range(min(self._position, len(stream._layers))):
            layer = stream._layers[i]
            hasher.update(f"{i}:{layer.effect.effect_type}".encode())
        return hasher.hexdigest()[:12]

    def __enter__(self) -> Self:
        """Enter checkpoint context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        """Exit checkpoint context, invalidating the checkpoint."""
        self._exited = True

    def restore(self) -> None:
        """Restore this checkpoint through its owning runtime scope."""
        if self._exited:
            raise ValueError(
                f"Cannot restore checkpoint '{self.name}' after exiting context manager. "
                f"Checkpoints are only valid within their `with` block."
            )
        self._scope.restore(self)

    def __repr__(self) -> str:
        if self._restored:
            return f"Checkpoint({self.name!r}, restored)"
        if self.is_stale:
            return f"Checkpoint({self.name!r}, stale)"
        effects = len(self.effects_since)
        extra = f", +{self.bindings_added} bindings" if self.bindings_added > 0 else ""
        return f"Checkpoint({self.name!r}, active, {effects} effects since{extra})"


__all__ = ["Checkpoint", "CheckpointValidationError"]
