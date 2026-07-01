"""ExecutePhase: Execute via provider.

Phase 3 of the lifecycle pipeline. This phase delegates execution to the
configured provider.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shepherd_core.errors import ExecutionError
from shepherd_core.provider import DefaultProviderRuntime

from ._phase_base import PhaseBase

if TYPE_CHECKING:
    from ._phase_context import PhaseContext

logger = logging.getLogger(__name__)


class ExecutePhase(PhaseBase):
    """Phase 3: Execute via provider.

    Reads: prompt, composed_binding, scope, task_name, provider
    Writes: result

    Execution is delegated entirely to the provider.
    """

    @property
    def name(self) -> str:
        return "execute"

    async def execute(self, ctx: PhaseContext) -> PhaseContext:
        # Skip if cache hit (result already set by CacheCheckPhase)
        if ctx.cache_hit:
            logger.debug("Skipping execute phase - cache hit")
            return ctx

        from shepherd_core.types import ExecutionResult

        if ctx.executor is not None:
            if ctx.kernel_v3_canary_spec is not None and ctx.kernel_v3_canary_target is not None:
                from shepherd_runtime.kernel.canary import run_kernel_v3_canary

                report = await run_kernel_v3_canary(
                    target=ctx.kernel_v3_canary_target,
                    executor=ctx.executor,
                    output_fields=getattr(ctx.task_meta, "outputs", {}),
                    spec=ctx.kernel_v3_canary_spec,
                )
                sentinel = ExecutionResult(
                    success=True,
                    output_text="",
                    metadata={
                        "task_name": ctx.task_name,
                        "kernel_v3_canary_mode": report.mode.value,
                        "kernel_v3_canary_authoritative": report.authoritative,
                        "kernel_v3_canary": report.to_metadata(),
                    },
                )
                logger.debug("Programmatic execution complete for '%s' via kernel-v3 canary", ctx.task_name)
                return ctx.with_result(sentinel).with_kernel_v3_canary_report(report)

            # Programmatic path: call the executor
            import asyncio

            if asyncio.iscoroutinefunction(ctx.executor):
                await ctx.executor()
            else:
                # Run sync executors in a thread so they don't block the
                # event loop. This is critical when arun() invokes a
                # programmatic task whose execute() instantiates sub-tasks:
                # those sub-tasks need _execute_sync → run_sync, which
                # detects a running event loop and spawns yet another
                # thread. Running the executor off the event loop thread
                # means run_sync finds no running loop and uses
                # asyncio.run() directly — no thread nesting, no timeout.
                await asyncio.to_thread(ctx.executor)

            # Set sentinel result so downstream phases have a valid ExecutionResult
            sentinel = ExecutionResult(
                success=True,
                output_text="",
                metadata={"task_name": ctx.task_name},
            )
            logger.debug("Programmatic execution complete for '%s'", ctx.task_name)
            return ctx.with_result(sentinel)

        if ctx.provider is None:
            raise ExecutionError("ExecutePhase requires either an executor or a provider. Neither was provided.")

        # LLM path: delegate to provider
        result = await ctx.provider.execute_sdk(
            prompt=ctx.prompt,
            binding=ctx.composed_binding,
            runtime=DefaultProviderRuntime.from_emitter(ctx.scope, task_name=ctx.task_name),
        )

        # Validate result
        if result is None:
            raise ExecutionError(
                f"Provider {ctx.provider.provider_id} returned None from execute_sdk(). "
                f"Providers must return an ExecutionResult."
            )
        if not isinstance(result, ExecutionResult):
            raise ExecutionError(
                f"Provider {ctx.provider.provider_id} returned {type(result).__name__} "
                f"instead of ExecutionResult from execute_sdk()."
            )

        logger.debug(
            "Execution complete: %d chars output",
            len(result.output_text),
        )

        return ctx.with_result(result)


__all__ = ["ExecutePhase"]
