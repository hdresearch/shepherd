"""Runtime-owned cache check and store phases for ExecutionLifecycle."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from shepherd_core.types import ExecutionResult

from shepherd_runtime._lifecycle._phase_base import PhaseBase

from .cache import CachedOutputs, CacheHit, CacheMode, CachePolicy, CacheStored, ExecutionKey
from .task.output import extract_outputs, rehydrate_cached_outputs, serialize_outputs_for_cache

if TYPE_CHECKING:
    from shepherd_runtime._lifecycle._phase_context import PhaseContext

logger = logging.getLogger(__name__)


class CacheCheckPhase(PhaseBase):
    @property
    def name(self) -> str:
        return "cache_check"

    async def execute(self, ctx: PhaseContext) -> PhaseContext:
        if ctx.task_meta is None:
            return ctx
        if not getattr(ctx.task_meta, "cacheable", True):
            logger.debug("Task %s has cacheable=False, skipping cache", ctx.task_name)
            return ctx

        cache_store = ctx.scope._get_cache_store()
        if cache_store is None:
            return ctx

        cache_config = ctx.scope._get_cache_config()
        policy = CachePolicy(cache_config.cache_policy)
        if policy == CachePolicy.DISABLED:
            return ctx

        execution_key = ExecutionKey.compute(ctx.task_meta, ctx.task_inputs, ctx.scope, policy)
        cached = cache_store.get(execution_key.key)
        if cached is None:
            logger.debug("Cache miss for %s (key=%s...)", ctx.task_name, execution_key.key[:8])
            return replace(ctx, execution_key=execution_key.key)

        try:
            cached_outputs = rehydrate_cached_outputs(
                ctx.task_meta,
                cached.outputs,
                taskref_policy=ctx.taskref_policy,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to rehydrate cached outputs for %s: %s", ctx.task_name, e)
            return replace(ctx, execution_key=execution_key.key)

        age_seconds = self._calculate_age(cached.created_at)
        ctx.scope.emit(
            CacheHit(
                task_name=ctx.task_name,
                execution_key=execution_key.key,
                cache_mode=cache_config.cache_mode,
                created_at=cached.created_at,
                age_seconds=age_seconds,
            )
        )

        cached_result = ExecutionResult(
            output_text="[cached]",
            metadata={"cache_hit": True},
        )

        return ctx.with_cache_hit(
            outputs=cached_outputs,
            execution_key=execution_key.key,
        ).with_result(cached_result)

    def _calculate_age(self, created_at: str | None) -> float:
        if not created_at:
            return 0.0
        try:
            created = datetime.fromisoformat(created_at)
            now = datetime.now(timezone.utc)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return (now - created).total_seconds()
        except (ValueError, TypeError):
            return 0.0


class CacheStorePhase(PhaseBase):
    @property
    def name(self) -> str:
        return "cache_store"

    async def execute(self, ctx: PhaseContext) -> PhaseContext:
        if ctx.cache_hit or ctx.task_meta is None or ctx.result is None:
            return ctx
        if not getattr(ctx.task_meta, "cacheable", True):
            return ctx

        cache_store = ctx.scope._get_cache_store()
        if cache_store is None:
            return ctx

        cache_config = ctx.scope._get_cache_config()
        policy = CachePolicy(cache_config.cache_policy)
        if policy == CachePolicy.DISABLED:
            return ctx

        execution_key = ctx.execution_key
        if not execution_key:
            key_obj = ExecutionKey.compute(ctx.task_meta, ctx.task_inputs, ctx.scope, policy)
            execution_key = key_obj.key

        try:
            outputs = extract_outputs(ctx.task_meta, ctx.result, taskref_policy=ctx.taskref_policy)
            cached_payload = serialize_outputs_for_cache(ctx.task_meta, outputs)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to extract outputs for caching: %s", e)
            return ctx

        if not cached_payload:
            logger.debug("No outputs to cache for %s", ctx.task_name)
            return ctx

        cached_outputs = CachedOutputs(
            outputs=cached_payload,
            task_name=ctx.task_name,
            execution_key=execution_key,
        )
        cache_mode = CacheMode(cache_config.cache_mode)
        cache_store.put(execution_key, cached_outputs, mode=cache_mode)

        ctx.scope.emit(
            CacheStored(
                task_name=ctx.task_name,
                execution_key=execution_key,
                cache_mode=cache_mode.value,
                size_bytes=len(str(cached_payload)),
            )
        )

        logger.debug("Cached %s (key=%s...)", ctx.task_name, execution_key[:8])
        return ctx


__all__ = ["CacheCheckPhase", "CacheStorePhase"]
