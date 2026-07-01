"""Resume and deferred replay ownership for ScopeProxy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ._persistence import apply_resumed_effects
from .substrate import ImmutableScope

if TYPE_CHECKING:
    from pathlib import Path

    from shepherd_core.context.kernel import ExecutionContext

    from .scope import ScopeProxy

__all__ = ["ResumeCoordinator", "resume_scope"]


class ResumeHost(Protocol):
    """Narrow host contract for deferred replay state."""

    @property
    def resume_layers(self) -> list[Any] | None: ...

    @resume_layers.setter
    def resume_layers(self, value: list[Any] | None) -> None: ...


class ResumeCoordinator:
    """Owns deferred replay helpers on a live scope instance."""

    __slots__ = ("_host",)

    def __init__(self, host: ResumeHost) -> None:
        self._host = host

    def apply_resumed_effects(
        self,
        binding_name: str,
        context: ExecutionContext,
    ) -> ExecutionContext:
        """Apply persisted layers that match the bound context."""
        return apply_resumed_effects(self._host.resume_layers, binding_name, context)

    def clear_resumed_layers(self) -> None:
        """Release deferred replay layers after rebinding is complete."""
        self._host.resume_layers = None


def resume_scope(
    scope_type: type[ScopeProxy],
    project_path: Path,
    stream_id: str | None = None,
    *,
    continues_from: bool = True,
) -> ScopeProxy:
    """Rebuild a scope from persisted layers and seed deferred replay state."""
    from shepherd_runtime.persistence import PersistenceConfig, PersistenceManager, ProjectId

    config = PersistenceConfig()
    project_id = ProjectId.from_path(project_path)
    manager = PersistenceManager(config.base_dir, project_id)
    manager.initialize()

    if stream_id is not None:
        layers = manager.read_stream(stream_id)
        previous_stream_id = stream_id
    else:
        layers = manager.read_latest_stream()
        latest = manager._index.get_latest_stream() if manager._index else None
        previous_stream_id = latest.stream_id if latest else None  # type: ignore[assignment]

    initial_scope = ImmutableScope()
    for layer in layers:
        initial_scope = initial_scope.with_layer(layer)

    proxy = scope_type(
        project_path=project_path,
        persistence=False,
        _scope=initial_scope,
    )

    proxy.resume_layers = list(initial_scope._stream._layers)

    if continues_from:
        proxy._persistence_manager.manager = manager
        manager.start_stream(continues_from=previous_stream_id)

    return proxy
