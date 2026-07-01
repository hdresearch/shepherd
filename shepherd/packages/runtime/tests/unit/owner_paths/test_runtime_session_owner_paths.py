"""Tests for hardened runtime session owner paths."""

from __future__ import annotations

import pytest
from shepherd_runtime.scope import Scope
from shepherd_runtime.scope import current_scope as current_core_scope
from shepherd_runtime.session import (
    SessionCleanupCoordinator,
    SessionCoordinator,
)
from shepherd_runtime.session import (
    current_scope as current_runtime_scope,
)
from shepherd_runtime.session import (
    require_scope as require_runtime_scope,
)


def test_runtime_session_owner_path_shares_scope_activation_stack() -> None:
    with Scope(root=True) as scope:
        assert current_runtime_scope() is scope
        assert require_runtime_scope() is scope
        assert current_core_scope() is scope
        assert isinstance(scope._session_cleanup, SessionCleanupCoordinator)
        assert isinstance(scope._session, SessionCoordinator)

    assert current_runtime_scope() is None
    assert current_core_scope() is None


def test_runtime_require_scope_raises_without_active_scope() -> None:
    with pytest.raises(RuntimeError, match="No active scope"):
        require_runtime_scope()
