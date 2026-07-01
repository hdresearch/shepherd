"""Tests for CacheCheckPhase and CacheStorePhase.

These phases integrate caching into the ExecutionLifecycle pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
from shepherd_core.types import ExecutionResult
from shepherd_runtime._lifecycle import PhaseContext
from shepherd_runtime._phase_cache import CacheCheckPhase, CacheStorePhase
from shepherd_runtime.cache import CachedOutputs
from shepherd_runtime.task.markers import TaskRef
from shepherd_runtime.task.output import TaskRefReconstructionPolicy


@dataclass
class MockFieldInfo:
    """Minimal FieldInfo for testing."""

    name: str
    inner_type: type = str
    marker_type: str = "input"
    description: str = ""
    required: bool = True
    default: Any = None
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass
class MockTaskMeta:
    """Minimal TaskMetadata for testing."""

    name: str = "TestTask"
    cacheable: bool = True
    docstring: str = ""
    guidance: str = ""
    inputs: dict[str, MockFieldInfo] = field(default_factory=lambda: {"input_val": MockFieldInfo(name="input_val")})
    outputs: dict[str, MockFieldInfo] = field(
        default_factory=lambda: {"output_val": MockFieldInfo(name="output_val", marker_type="output")}
    )
    artifacts: dict[str, Any] = field(default_factory=dict)
    contexts: dict[str, Any] = field(default_factory=dict)
    artifact_markers: dict[str, Any] = field(default_factory=dict)


@dataclass
class MockCacheConfig:
    """Mock cache configuration."""

    cache_policy: str = "inputs_only"
    cache_mode: str = "outputs_only"


class TestCacheCheckPhase:
    """Tests for CacheCheckPhase."""

    @pytest.fixture
    def phase(self):
        return CacheCheckPhase()

    @pytest.fixture
    def mock_scope(self):
        """Create a mock scope with cache infrastructure."""
        scope = MagicMock()
        scope._get_cache_store.return_value = MagicMock()
        scope._get_cache_config.return_value = MockCacheConfig()
        scope.all_bindings.return_value = []
        return scope

    @pytest.fixture
    def mock_ctx(self, mock_scope):
        """Create a mock PhaseContext with cache infrastructure."""
        provider = MagicMock()
        provider.provider_id = "test-provider"

        return PhaseContext(
            scope=mock_scope,
            provider=provider,
            task_name="TestTask",
            task_meta=MockTaskMeta(),
            task_inputs={"input_val": "test"},
        )

    @pytest.mark.asyncio
    async def test_skip_when_no_task_meta(self, phase, mock_scope):
        """Should skip when task_meta is None."""
        provider = MagicMock()
        provider.provider_id = "test-provider"

        ctx = PhaseContext(
            scope=mock_scope,
            provider=provider,
            task_name="TestTask",
            task_meta=None,
        )

        result = await phase.execute(ctx)

        assert result is ctx
        assert not result.cache_hit

    @pytest.mark.asyncio
    async def test_skip_when_cacheable_false(self, phase, mock_ctx):
        """Should skip when task has cacheable=False."""
        mock_ctx = PhaseContext(
            scope=mock_ctx.scope,
            provider=mock_ctx.provider,
            task_name="TestTask",
            task_meta=MockTaskMeta(cacheable=False),
            task_inputs={"input_val": "test"},
        )

        result = await phase.execute(mock_ctx)

        assert result is mock_ctx
        assert not result.cache_hit

    @pytest.mark.asyncio
    async def test_skip_when_no_cache_store(self, phase, mock_ctx):
        """Should skip when cache store is None."""
        mock_ctx.scope._get_cache_store.return_value = None

        result = await phase.execute(mock_ctx)

        assert result is mock_ctx
        assert not result.cache_hit

    @pytest.mark.asyncio
    async def test_skip_when_policy_disabled(self, phase, mock_ctx):
        """Should skip when cache policy is DISABLED."""
        mock_ctx.scope._get_cache_config.return_value = MockCacheConfig(cache_policy="disabled")

        result = await phase.execute(mock_ctx)

        assert result is mock_ctx
        assert not result.cache_hit

    @pytest.mark.asyncio
    async def test_cache_miss_stores_execution_key(self, phase, mock_ctx):
        """Should store execution_key on cache miss for CacheStorePhase."""
        mock_ctx.scope._get_cache_store.return_value.get.return_value = None

        result = await phase.execute(mock_ctx)

        assert result.execution_key != ""
        assert not result.cache_hit

    @pytest.mark.asyncio
    async def test_cache_hit_sets_outputs(self, phase, mock_ctx):
        """Should set cache_hit and cached_outputs on hit."""
        cached = CachedOutputs(
            outputs={"output_val": "cached_result"},
            task_name="TestTask",
            execution_key="test_key",
        )
        mock_ctx.scope._get_cache_store.return_value.get.return_value = cached

        result = await phase.execute(mock_ctx)

        assert result.cache_hit is True
        assert result.cached_outputs == {"output_val": "cached_result"}
        assert result.execution_key == mock.ANY  # Key was set

    @pytest.mark.asyncio
    async def test_cache_hit_sets_result(self, phase, mock_ctx):
        """Should set synthetic result on cache hit."""
        cached = CachedOutputs(
            outputs={"output_val": "cached_result"},
            task_name="TestTask",
            execution_key="test_key",
        )
        mock_ctx.scope._get_cache_store.return_value.get.return_value = cached

        result = await phase.execute(mock_ctx)

        assert result.result is not None
        assert result.result.output_text == "[cached]"
        assert result.result.metadata == {"cache_hit": True}

    @pytest.mark.asyncio
    async def test_cache_hit_emits_effect(self, phase, mock_ctx):
        """Should emit CacheHit effect on cache hit."""
        cached = CachedOutputs(
            outputs={"output_val": "cached_result"},
            task_name="TestTask",
            execution_key="test_key",
        )
        mock_ctx.scope._get_cache_store.return_value.get.return_value = cached

        await phase.execute(mock_ctx)

        mock_ctx.scope.emit.assert_called_once()
        emitted = mock_ctx.scope.emit.call_args[0][0]
        assert emitted.task_name == "TestTask"

    @pytest.mark.asyncio
    async def test_cache_hit_rehydrates_taskref_outputs(self, phase, mock_ctx):
        """Cached TaskRef payloads should be rehydrated before assignment."""
        task_source = "@task\nclass CachedTask(BaseModel):\n    text: Input(str)\n    result: Output(str)"
        task_meta = MockTaskMeta(
            outputs={"output_val": MockFieldInfo(name="output_val", inner_type=TaskRef, marker_type="output")}
        )
        cached = CachedOutputs(
            outputs={"output_val": task_source},
            task_name="TestTask",
            execution_key="test_key",
        )
        mock_ctx.scope._get_cache_store.return_value.get.return_value = cached
        mock_ctx = PhaseContext(
            scope=mock_ctx.scope,
            provider=mock_ctx.provider,
            task_name="TestTask",
            task_meta=task_meta,
            task_inputs={"input_val": "test"},
        )

        result = await phase.execute(mock_ctx)

        assert result.cache_hit is True
        assert isinstance(result.cached_outputs["output_val"], type)
        assert result.cached_outputs["output_val"].__name__ == "CachedTask"

    @pytest.mark.asyncio
    async def test_cache_hit_rehydrates_taskref_outputs_with_allowlisted_policy(
        self, phase, mock_ctx, tmp_path, monkeypatch
    ):
        """Cache rehydration should honor explicit local TaskRef policy."""
        domain_module = tmp_path / "my_domain.py"
        domain_module.write_text("Alias = str\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))

        task_source = "from my_domain import Alias\n@task\nclass CachedTask(BaseModel):\n    text: Input(Alias)\n    result: Output(str)"
        task_meta = MockTaskMeta(
            outputs={"output_val": MockFieldInfo(name="output_val", inner_type=TaskRef, marker_type="output")}
        )
        cached = CachedOutputs(
            outputs={"output_val": task_source},
            task_name="TestTask",
            execution_key="test_key",
        )
        mock_ctx.scope._get_cache_store.return_value.get.return_value = cached
        mock_ctx = PhaseContext(
            scope=mock_ctx.scope,
            provider=mock_ctx.provider,
            task_name="TestTask",
            task_meta=task_meta,
            task_inputs={"input_val": "test"},
            taskref_policy=TaskRefReconstructionPolicy.allowlisted("my_domain"),
        )

        result = await phase.execute(mock_ctx)

        assert result.cache_hit is True
        assert result.cached_outputs["output_val"].__name__ == "CachedTask"

    def test_calculate_age_with_valid_timestamp(self, phase):
        """Should calculate age from ISO timestamp."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        one_hour_ago = (now - timedelta(hours=1)).isoformat()

        age = phase._calculate_age(one_hour_ago)

        assert 3599 < age < 3601  # Approximately 1 hour

    def test_calculate_age_with_none(self, phase):
        """Should return 0.0 for None timestamp."""
        age = phase._calculate_age(None)
        assert age == 0.0

    def test_calculate_age_with_invalid_timestamp(self, phase):
        """Should return 0.0 for invalid timestamp."""
        age = phase._calculate_age("not-a-timestamp")
        assert age == 0.0


class TestCacheStorePhase:
    """Tests for CacheStorePhase."""

    @pytest.fixture
    def phase(self):
        return CacheStorePhase()

    @pytest.fixture
    def mock_scope(self):
        """Create a mock scope with cache infrastructure."""
        scope = MagicMock()
        scope._get_cache_store.return_value = MagicMock()
        scope._get_cache_config.return_value = MockCacheConfig()
        scope.all_bindings.return_value = []
        return scope

    @pytest.fixture
    def mock_ctx_with_result(self, mock_scope):
        """Create a mock PhaseContext with execution result."""
        provider = MagicMock()
        provider.provider_id = "test-provider"

        result = ExecutionResult(
            output_text='{"output_val": "result_value"}',
        )

        return PhaseContext(
            scope=mock_scope,
            provider=provider,
            task_name="TestTask",
            task_meta=MockTaskMeta(),
            task_inputs={"input_val": "test"},
            result=result,
            execution_key="test_execution_key",
        )

    @pytest.mark.asyncio
    async def test_skip_on_cache_hit(self, phase, mock_ctx_with_result):
        """Should skip when cache_hit is True."""
        ctx = PhaseContext(
            scope=mock_ctx_with_result.scope,
            provider=mock_ctx_with_result.provider,
            task_name="TestTask",
            task_meta=MockTaskMeta(),
            task_inputs={"input_val": "test"},
            result=mock_ctx_with_result.result,
            cache_hit=True,
        )

        result = await phase.execute(ctx)

        assert result is ctx
        mock_ctx_with_result.scope._get_cache_store.return_value.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_no_task_meta(self, phase, mock_scope):
        """Should skip when task_meta is None."""
        provider = MagicMock()

        ctx = PhaseContext(
            scope=mock_scope,
            provider=provider,
            task_name="TestTask",
            task_meta=None,
            result=ExecutionResult(output_text="test"),
        )

        result = await phase.execute(ctx)

        assert result is ctx
        mock_scope._get_cache_store.return_value.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_cacheable_false(self, phase, mock_ctx_with_result):
        """Should skip when task has cacheable=False."""
        ctx = PhaseContext(
            scope=mock_ctx_with_result.scope,
            provider=mock_ctx_with_result.provider,
            task_name="TestTask",
            task_meta=MockTaskMeta(cacheable=False),
            task_inputs={"input_val": "test"},
            result=mock_ctx_with_result.result,
        )

        result = await phase.execute(ctx)

        assert result is ctx
        mock_ctx_with_result.scope._get_cache_store.return_value.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_no_result(self, phase, mock_scope):
        """Should skip when result is None."""
        provider = MagicMock()

        ctx = PhaseContext(
            scope=mock_scope,
            provider=provider,
            task_name="TestTask",
            task_meta=MockTaskMeta(),
            task_inputs={"input_val": "test"},
            result=None,
        )

        result = await phase.execute(ctx)

        assert result is ctx
        mock_scope._get_cache_store.return_value.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_no_cache_store(self, phase, mock_ctx_with_result):
        """Should skip when cache store is None."""
        mock_ctx_with_result.scope._get_cache_store.return_value = None

        result = await phase.execute(mock_ctx_with_result)

        assert result is mock_ctx_with_result

    @pytest.mark.asyncio
    async def test_skip_when_policy_disabled(self, phase, mock_ctx_with_result):
        """Should skip when cache policy is DISABLED."""
        mock_ctx_with_result.scope._get_cache_config.return_value = MockCacheConfig(cache_policy="disabled")

        result = await phase.execute(mock_ctx_with_result)

        assert result is mock_ctx_with_result
        mock_ctx_with_result.scope._get_cache_store.return_value.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_stores_outputs_on_fresh_execution(self, phase, mock_ctx_with_result):
        """Should store outputs after fresh execution."""
        await phase.execute(mock_ctx_with_result)

        cache_store = mock_ctx_with_result.scope._get_cache_store.return_value
        cache_store.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_emits_cache_stored_effect(self, phase, mock_ctx_with_result):
        """Should emit CacheStored effect after storing."""
        await phase.execute(mock_ctx_with_result)

        mock_ctx_with_result.scope.emit.assert_called_once()
        emitted = mock_ctx_with_result.scope.emit.call_args[0][0]
        assert emitted.task_name == "TestTask"

    @pytest.mark.asyncio
    async def test_taskref_outputs_are_serialized_as_source_for_cache(self, phase, mock_scope):
        """TaskRef outputs should be stored as raw source, not live classes."""
        task_source = "@task\nclass GeneratedTask(BaseModel):\n    text: Input(str)\n    result: Output(str)"
        provider = MagicMock()
        provider.provider_id = "test-provider"
        ctx = PhaseContext(
            scope=mock_scope,
            provider=provider,
            task_name="TestTask",
            task_meta=MockTaskMeta(
                outputs={"output_val": MockFieldInfo(name="output_val", inner_type=TaskRef, marker_type="output")}
            ),
            task_inputs={"input_val": "test"},
            result=ExecutionResult(
                output_text="[result]",
                structured_output={"output_val": task_source},
            ),
            execution_key="test_execution_key",
        )

        await phase.execute(ctx)

        cache_store = mock_scope._get_cache_store.return_value
        cache_store.put.assert_called_once()
        cached = cache_store.put.call_args[0][1]
        assert cached.outputs == {"output_val": task_source}


class TestCachePhaseIntegration:
    """Integration tests for cache phases working together."""

    @pytest.mark.asyncio
    async def test_cache_check_prepares_key_for_store(self):
        """CacheCheckPhase stores execution_key for CacheStorePhase to reuse."""
        check_phase = CacheCheckPhase()
        store_phase = CacheStorePhase()

        # Mock setup
        scope = MagicMock()
        cache_store = MagicMock()
        cache_store.get.return_value = None  # Cache miss
        scope._get_cache_store.return_value = cache_store
        scope._get_cache_config.return_value = MockCacheConfig()
        scope.all_bindings.return_value = []

        provider = MagicMock()
        provider.provider_id = "test"

        ctx = PhaseContext(
            scope=scope,
            provider=provider,
            task_name="TestTask",
            task_meta=MockTaskMeta(),
            task_inputs={"input_val": "test"},
        )

        # Run cache check (miss)
        ctx = await check_phase.execute(ctx)
        assert ctx.execution_key != ""
        assert not ctx.cache_hit

        # Simulate execution
        result = ExecutionResult(output_text='{"output_val": "new_result"}')
        ctx = ctx.with_result(result)

        # Run cache store
        await store_phase.execute(ctx)

        # Verify store was called with the same key
        cache_store.put.assert_called_once()
        stored_key = cache_store.put.call_args[0][0]
        assert stored_key == ctx.execution_key


# Import mock for assertion helper
from unittest import mock
