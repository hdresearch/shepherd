"""Focused tests for the session and cleanup collaborators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from shepherd_runtime.scope import Scope, current_scope
from shepherd_runtime.session import SessionCleanupCoordinator, SessionCoordinator
from shepherd_tests.contexts import SimpleContext


@dataclass(frozen=True)
class _CleanupContext(SimpleContext):
    cleanup_events: ClassVar[list[str]] = []

    def cleanup(self, error=None) -> None:
        label = "none" if error is None else type(error).__name__
        type(self).cleanup_events.append(f"{self.name}:{label}")


class TestSessionCoordinator:
    def test_enter_uses_current_scope_stack_and_implicit_child_attachment(self) -> None:
        with Scope(root=True) as parent:
            child = Scope()

            entered = child._session.enter()

            assert isinstance(child._session, SessionCoordinator)
            assert entered is child
            assert current_scope() is child
            assert child._parent_proxy is parent
            assert child._depth == 1

            child._session.exit(None, None, None)

            assert current_scope() is parent

    def test_reentrant_enter_resets_only_inner_token(self, monkeypatch) -> None:
        scope = Scope(root=True)
        close_stream = MagicMock()
        monkeypatch.setattr(scope._persistence_manager, "close_stream", close_stream)

        scope._session.enter()
        scope._session.enter()

        assert current_scope() is scope
        assert scope._token is not None

        scope._session.exit(None, None, None)

        assert current_scope() is scope
        assert scope._token is not None
        assert scope.is_closed is False
        close_stream.assert_not_called()

        scope._session.exit(None, None, None)

        assert current_scope() is None
        assert scope._token is None
        assert scope.is_closed is True
        close_stream.assert_called_once_with()

    def test_cleanup_marks_only_dangling_bindings_and_closes_persistence(self, monkeypatch) -> None:
        _CleanupContext.cleanup_events.clear()

        scope = Scope(root=True)
        scope.bind("dangling", _CleanupContext(name="dangling"))
        scope.bind("active", _CleanupContext(name="active"))
        scope.mark_binding_lifecycle("dangling", is_prepared=True, in_lifecycle=False)
        scope.mark_binding_lifecycle("active", is_prepared=True, in_lifecycle=True)

        close_stream = MagicMock()
        monkeypatch.setattr(scope._persistence_manager, "close_stream", close_stream)

        scope._session_cleanup.cleanup_dangling(RuntimeError("boom"))

        assert isinstance(scope._session_cleanup, SessionCleanupCoordinator)
        assert _CleanupContext.cleanup_events == ["dangling:RuntimeError"]
        assert scope.get_binding("dangling").is_prepared is False
        assert scope.get_binding("active").is_prepared is True
        close_stream.assert_called_once_with()

    def test_exit_resets_current_scope_even_if_close_stream_fails(self, monkeypatch) -> None:
        scope = Scope(root=True)
        scope.bind("workspace", SimpleContext(name="workspace", value=1))
        scope._session.enter()

        monkeypatch.setattr(
            scope._persistence_manager, "close_stream", MagicMock(side_effect=RuntimeError("close failed"))
        )

        with pytest.raises(RuntimeError, match="close failed"):
            scope._session.exit(None, None, None)

        assert current_scope() is None
        assert scope._token is None
        assert scope.is_closed is True
