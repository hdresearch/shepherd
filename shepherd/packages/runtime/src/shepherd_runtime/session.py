"""Runtime-owned scope activation and dangling-cleanup coordination."""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import types

    from ._scope.scope import ScopeProxy

__all__ = [
    "SessionCleanupCoordinator",
    "SessionCoordinator",
    "current_scope",
    "require_scope",
]

logger = logging.getLogger(__name__)

_current_scope: ContextVar[ScopeProxy | None] = ContextVar("current_scope", default=None)


def current_scope() -> ScopeProxy | None:
    """Get the current active scope, if any."""
    return _current_scope.get()


def require_scope() -> ScopeProxy:
    """Get the current scope, raising if none active."""
    scope = _current_scope.get()
    if scope is None:
        raise RuntimeError("No active scope. Use 'with Scope() as scope:' to create one.")
    return scope


class SessionCleanupHost(Protocol):
    """Narrow host contract for exit-time dangling cleanup."""

    def iter_session_local_bindings(self) -> list[Any]: ...

    def mark_session_binding_cleaned(self, name: str) -> None: ...

    def close_session_persistence(self) -> None: ...


class SessionHost(Protocol):
    """Narrow host contract for scope activation and lifetime."""

    @property
    def session_is_root(self) -> bool: ...

    @property
    def session_is_global(self) -> bool: ...

    @property
    def session_parent(self) -> ScopeProxy | None: ...

    def validate_session_auto_nesting_configuration(self) -> None: ...

    def attach_session_to_parent(self, parent: ScopeProxy) -> None: ...

    def initialize_session_root_persistence(self) -> None: ...

    @property
    def session_token(self) -> Any: ...

    @session_token.setter
    def session_token(self, value: Any) -> None: ...

    @property
    def session_token_depth(self) -> int: ...

    def pop_session_token(self) -> Any | None: ...

    @property
    def session_exited(self) -> bool: ...

    @session_exited.setter
    def session_exited(self, value: bool) -> None: ...


class SessionCleanupCoordinator:
    """Owns dangling prepared-context cleanup on scope exit."""

    __slots__ = ("_host",)

    def __init__(self, host: SessionCleanupHost) -> None:
        self._host = host

    def cleanup_dangling(self, error: BaseException | None = None) -> None:
        """Clean prepared bindings that are not managed by an active lifecycle."""
        for binding in self._host.iter_session_local_bindings():
            if not binding.is_prepared or binding.in_lifecycle:
                continue

            try:
                binding.context.cleanup(error=error)
                self._host.mark_session_binding_cleaned(binding.name)
            except (OSError, RuntimeError, TypeError, AttributeError) as exc:
                logger.warning(
                    "Cleanup failed for '%s': %s",
                    binding.name,
                    exc,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )
            except Exception as exc:
                logger.exception(
                    "Unexpected error during cleanup for '%s': %s",
                    binding.name,
                    exc,
                )

        self._host.close_session_persistence()


class SessionCoordinator:
    """Owns current-scope activation and context-manager delegation."""

    __slots__ = ("_cleanup", "_host", "_owner")

    def __init__(
        self,
        owner: ScopeProxy,
        host: SessionHost,
        cleanup: SessionCleanupCoordinator,
    ) -> None:
        self._owner = owner
        self._host = host
        self._cleanup = cleanup

    def enter(self) -> ScopeProxy:
        """Enter the scope, activating it and resolving implicit nesting."""
        is_outermost_enter = self._host.session_token_depth == 0
        if (
            is_outermost_enter
            and not self._host.session_is_root
            and not self._host.session_is_global
            and self._host.session_parent is None
        ):
            parent = current_scope()
            if parent is not None:
                self._host.validate_session_auto_nesting_configuration()
                self._host.attach_session_to_parent(parent)

        if is_outermost_enter:
            self._host.initialize_session_root_persistence()
            self._host.session_exited = False
        self._host.session_token = _current_scope.set(self._owner)
        return self._owner

    def exit(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Exit the scope, cleaning dangling bindings and clearing activation."""
        del exc_type, exc_tb
        is_final_exit = self._host.session_token_depth <= 1

        try:
            if is_final_exit:
                self._host.session_exited = True
                self._cleanup.cleanup_dangling(error=exc_val)
        finally:
            token = self._host.pop_session_token()
            if token is not None:
                _current_scope.reset(token)

    async def aenter(self) -> ScopeProxy:
        """Async context-manager entry."""
        return self.enter()

    async def aexit(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Async context-manager exit."""
        self.exit(exc_type, exc_val, exc_tb)
