"""Tests for the simplified Agent facade class."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestAgentConstruction:
    def test_local_mode(self):
        """Agent(model=...) with no container should have no device."""
        from shepherd.agent import Agent

        agent = Agent(model="claude-sonnet-4-6")
        assert agent._device is None

    def test_capabilities_default(self):
        """Default capabilities should be read, write, bash."""
        from shepherd.agent import Agent

        agent = Agent.__new__(Agent)
        agent._scope = MagicMock()
        agent._provider = MagicMock()
        agent._capabilities = frozenset(["read", "write", "bash"])
        agent._device = None
        agent._trajectory = []
        assert agent._capabilities == frozenset(["read", "write", "bash"])


class TestAgentRun:
    @pytest.mark.asyncio
    async def test_basic_run(self):
        """Basic arun should call provider and return Result."""
        from shepherd.agent import Agent, Result

        agent = Agent.__new__(Agent)
        agent._scope = MagicMock()
        child_scope = MagicMock()
        child_scope.effects = MagicMock()
        agent._scope.fork.return_value = child_scope

        agent._provider = MagicMock()
        agent._provider.execute_sdk = AsyncMock(
            return_value=MagicMock(
                output_text="Done",
                success=True,
                metadata={"turns": 1},
            )
        )
        agent._capabilities = frozenset(["bash"])
        agent._device = None
        agent._trajectory = []

        result = await agent.arun("Do something")
        assert isinstance(result, Result)
        assert result.output == "Done"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_gate_rejects(self):
        """Gate returning False should produce rejected Result."""
        from shepherd.agent import Agent

        agent = Agent.__new__(Agent)
        agent._scope = MagicMock()
        child_scope = MagicMock()
        child_scope.effects = MagicMock()
        agent._scope.fork.return_value = child_scope

        agent._provider = MagicMock()
        agent._provider.execute_sdk = AsyncMock(
            return_value=MagicMock(
                output_text="Bad",
                success=True,
                metadata={"turns": 1},
            )
        )
        agent._capabilities = frozenset(["bash"])
        agent._device = None
        agent._trajectory = []

        result = await agent.arun("Do something", gate=lambda r, e: False)
        assert result.rejected is True
        child_scope.discard.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        """Retry should call provider again after failure."""
        from shepherd.agent import Agent

        agent = Agent.__new__(Agent)
        agent._scope = MagicMock()
        child1 = MagicMock()
        child1.effects = MagicMock()
        child2 = MagicMock()
        child2.effects = MagicMock()
        agent._scope.fork.side_effect = [child1, child2]

        agent._provider = MagicMock()
        agent._provider.execute_sdk = AsyncMock(
            side_effect=[
                RuntimeError("failed"),
                MagicMock(output_text="Success", success=True, metadata={"turns": 1}),
            ]
        )
        agent._capabilities = frozenset(["bash"])
        agent._device = None
        agent._trajectory = []

        result = await agent.arun("Do something", retry=1)
        assert result.success is True
        assert result.output == "Success"
        child1.discard.assert_called_once()


class TestForkMergeDiscard:
    def test_fork(self):
        """Fork should create new Agent with forked scope."""
        from shepherd.agent import Agent

        agent = Agent.__new__(Agent)
        agent._scope = MagicMock()
        agent._scope.fork.return_value = MagicMock()
        agent._provider = MagicMock()
        agent._device = None
        agent._capabilities = frozenset(["bash"])
        agent._trajectory = []

        forked = agent.fork()
        assert forked._scope == agent._scope.fork.return_value
        assert forked._provider is agent._provider  # shared

    def test_merge(self):
        """Merge should call scope.merge_effects."""
        from shepherd.agent import Agent

        agent = Agent.__new__(Agent)
        agent._scope = MagicMock()

        branch = Agent.__new__(Agent)
        branch._scope = MagicMock()

        agent.merge(branch)
        agent._scope.merge_effects.assert_called_once_with(branch._scope.effects)

    def test_discard(self):
        """Discard should call branch scope.discard."""
        from shepherd.agent import Agent

        branch = Agent.__new__(Agent)
        branch._scope = MagicMock()

        agent = Agent.__new__(Agent)
        agent._scope = MagicMock()

        agent.discard(branch)
        branch._scope.discard.assert_called_once()


class TestBindAndCapabilities:
    def test_bind(self):
        """Bind should delegate to scope."""
        from shepherd.agent import Agent

        agent = Agent.__new__(Agent)
        agent._scope = MagicMock()

        resource = MagicMock()
        agent.bind("workspace", resource)
        agent._scope.bind.assert_called_once_with("workspace", resource)

    def test_grant(self):
        """Grant should add capabilities."""
        from shepherd.agent import Agent

        agent = Agent.__new__(Agent)
        agent._capabilities = frozenset(["read"])
        agent.grant("write", "bash")
        assert agent._capabilities == frozenset(["read", "write", "bash"])

    def test_revoke(self):
        """Revoke should remove capabilities."""
        from shepherd.agent import Agent

        agent = Agent.__new__(Agent)
        agent._capabilities = frozenset(["read", "write", "bash"])
        agent.revoke("bash")
        assert agent._capabilities == frozenset(["read", "write"])


class TestProperties:
    def test_effects_delegates_to_scope(self):
        """Effects property should return scope.effects."""
        from shepherd.agent import Agent

        agent = Agent.__new__(Agent)
        agent._scope = MagicMock()
        agent._scope.effects = "mock_effects"
        assert agent.effects == "mock_effects"

    def test_scope_returns_scope(self):
        """scope() should return the underlying ScopeProxy."""
        from shepherd.agent import Agent

        agent = Agent.__new__(Agent)
        agent._scope = MagicMock()
        assert agent.scope() is agent._scope


class TestLifecycle:
    def test_context_manager(self):
        """Agent should work as context manager."""
        from shepherd.agent import Agent

        agent = Agent.__new__(Agent)
        agent._device = None
        agent._scope = MagicMock()

        with agent as a:
            assert a is agent
        # cleanup should not raise
