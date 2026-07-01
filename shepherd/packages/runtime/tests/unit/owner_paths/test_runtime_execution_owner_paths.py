"""Tests for hardened runtime execution owner paths."""

from __future__ import annotations

from typing import Any

import pytest
from shepherd_runtime.execution import ScopeExecutionCacheFacade
from shepherd_runtime.scope import Scope


def test_runtime_execution_owner_path_installed_on_scope() -> None:
    scope = Scope(root=True)

    assert isinstance(scope._execution, ScopeExecutionCacheFacade)


@pytest.mark.asyncio
async def test_scope_execute_routes_through_runtime_execution_owner_path(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = Scope(root=True)
    provider = object()
    captured: dict[str, Any] = {}

    async def fake_execute(
        runtime_scope: Scope,
        prompt: str,
        *,
        provider: object,
        task_name: str | None,
    ) -> tuple[str, dict[str, Any]]:
        captured["scope"] = runtime_scope
        captured["prompt"] = prompt
        captured["provider"] = provider
        captured["task_name"] = task_name
        return "ok", {}

    monkeypatch.setattr("shepherd_runtime.lifecycle.execute", fake_execute)

    result, outputs = await scope.execute(
        "hello",
        provider=provider,
        task_name="runtime-owner-path",
    )

    assert result == "ok"
    assert outputs == {}
    assert captured == {
        "scope": scope,
        "prompt": "hello",
        "provider": provider,
        "task_name": "runtime-owner-path",
    }
