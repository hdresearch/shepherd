"""Advanced owner-path Agent wrapper.

The top-level ``shepherd`` facade no longer exports ``Agent``. This module
remains importable as ``shepherd.agent`` for migration experiments around the
older Scope/provider/device stack, but it is not the first-run callable-spine
surface.

Usage:
    from shepherd.agent import Agent

    agent = Agent(model="claude-sonnet-4-6", container="python:3.12")
    result = agent.run("Fix the SSL cert in /app/ssl")
    print(result.output)
    print(result.effects)

Advanced callers can still access the underlying runtime scope:
    with agent.scope() as scope:
        # Owner-path runtime APIs are available here.
        ...
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shepherd_core.provider import DefaultProviderRuntime
from shepherd_core.types import ExecutionResult, ProviderBinding
from shepherd_runtime.device import ContainerDevice
from shepherd_runtime.scope import Scope
from typing_extensions import Self

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass
class Result:
    """Result from an Agent.run() call."""

    output: str = ""
    success: bool = True
    effects: Any = None  # Stream from shepherd_core.scope.stream
    steps: int = 0
    rejected: bool = False
    reason: str | None = None
    _execution_result: ExecutionResult | None = field(default=None, repr=False)

    def to_json(self, path: str | None = None, **kwargs: Any) -> str:
        """Export effects as flat JSON summary (Tier 1)."""
        from shepherd_export import to_json

        stream = self.effects if self.effects is not None else _empty_stream()
        return to_json(stream, path=path, **kwargs)

    def to_atif(self, **kwargs: Any) -> dict[str, Any]:
        """Export effects as ATIF dict (Tier 2, requires Harbor)."""
        from shepherd_export import to_atif

        stream = self.effects if self.effects is not None else _empty_stream()
        return to_atif(stream, **kwargs)

    def to_atif_json(self, **kwargs: Any) -> str:
        """Export effects as ATIF JSON string (Tier 2, requires Harbor)."""
        from shepherd_export import to_atif_json

        stream = self.effects if self.effects is not None else _empty_stream()
        return to_atif_json(stream, **kwargs)

    def to_trajectory(self, output_dir: str, **kwargs: Any) -> Any:
        """Export effects as lossless trajectory directory (Tier 3)."""
        from shepherd_export import to_trajectory

        stream = self.effects if self.effects is not None else _empty_stream()
        return to_trajectory(stream, output_dir, **kwargs)


def _empty_stream() -> Any:
    from shepherd_core.scope.stream import Stream

    return Stream()


class Agent:
    """Advanced wrapper around the legacy Scope/provider/device path.

    Composes Scope (effects/fork/merge) + LiteLLMProvider (LLM calls) +
    optional ContainerDevice (container execution) into a single object. It is
    intentionally owner-path only while the public callable spine is
    ``workspace`` / ``task`` / ``deliver``.

    Usage:
        agent = Agent(model="claude-sonnet-4-6")
        result = agent.run("Tell me a joke about Python")
        print(result.output)
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        container: str | None = None,
        capabilities: list[str] | None = None,
        **model_kwargs: Any,
    ):
        """Create an Agent.

        Args:
            model: Any litellm-compatible model string.
            container: Docker/Podman image for containerized execution. None = local.
            capabilities: Tool capabilities. Default: ["read", "write", "bash"].
            **model_kwargs: Passed to litellm (temperature, api_key, max_tokens, etc.).
        """
        from shepherd_providers.litellm import LiteLLMProvider

        if capabilities is None:
            capabilities = ["read", "write", "bash"]

        self._scope = Scope(root=True)
        self._provider = LiteLLMProvider(model=model, **model_kwargs)
        self._scope.register_provider("default", self._provider, default=True)
        self._capabilities = frozenset(capabilities)
        self._device: Any = None
        self._trajectory: list[dict[str, Any]] = []

        if container:
            self._setup_container(container)

    def _setup_container(self, image: str) -> None:
        """Set up container device for isolated execution."""
        try:
            self._device = ContainerDevice(image=image)
            self._provider.set_device(self._device)
        except ImportError:
            logger.warning("ContainerDevice not available, running locally")

    # ── Core Execution ──

    def run(
        self,
        instruction: str,
        retry: int = 0,
        gate: Callable[..., bool] | None = None,
        timeout: float | None = None,
    ) -> Result:
        """Run the agent on an instruction (sync).

        Args:
            instruction: What the agent should do.
            retry: Number of retry attempts on failure (0 = no retry).
            gate: Optional function (result, effects) -> bool. If False, reject.
            timeout: Max seconds for the entire run.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an existing event loop — can't use asyncio.run()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self.arun(instruction, retry, gate, timeout))
                return future.result()

        return asyncio.run(self.arun(instruction, retry, gate, timeout))

    async def arun(
        self,
        instruction: str,
        retry: int = 0,
        gate: Callable[..., bool] | None = None,
        timeout: float | None = None,
    ) -> Result:
        """Run the agent on an instruction (async).

        Args:
            instruction: What the agent should do.
            retry: Number of retry attempts on failure (0 = no retry).
            gate: Optional function (result, effects) -> bool. If False, reject.
            timeout: Max seconds for the entire run.
        """
        attempts = max(1, retry + 1)

        for attempt in range(attempts):
            child = self._scope.fork()

            try:
                binding = self._build_binding(instruction)

                if timeout:
                    exec_result = await asyncio.wait_for(
                        self._provider.execute_sdk(
                            prompt=instruction,
                            binding=binding,
                            runtime=DefaultProviderRuntime.from_emitter(child),
                        ),
                        timeout=timeout,
                    )
                else:
                    exec_result = await self._provider.execute_sdk(
                        prompt=instruction,
                        binding=binding,
                        runtime=DefaultProviderRuntime.from_emitter(child),
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Attempt {attempt + 1}/{attempts} failed: {e}")
                child.discard()
                if attempt < attempts - 1:
                    continue
                return Result(
                    output="",
                    success=False,
                    effects=child.effects,
                    steps=0,
                    reason=str(e),
                )

            result = Result(
                output=exec_result.output_text,
                success=exec_result.success,
                effects=child.effects,
                steps=exec_result.metadata.get("turns", 0),
                _execution_result=exec_result,
            )

            # Gate check
            if gate:
                try:
                    passed = gate(result, child.effects)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Gate raised exception: {e}")
                    passed = False

                if not passed:
                    child.discard()
                    if attempt < attempts - 1:
                        continue
                    result.rejected = True
                    result.reason = "Gate rejected"
                    return result

            # Success — merge effects to parent
            self._scope.merge_effects(child.effects)
            return result

        # Should not reach here, but just in case
        return Result(output="", success=False, steps=0, reason="All attempts exhausted")

    def chain(self, *instructions: str) -> Result:
        """Run instructions sequentially. Each sees effects from prior ones."""
        last_result = Result()
        for instruction in instructions:
            last_result = self.run(instruction)
            if not last_result.success:
                break
        return last_result

    async def achain(self, *instructions: str) -> Result:
        """Run instructions sequentially (async)."""
        last_result = Result()
        for instruction in instructions:
            last_result = await self.arun(instruction)
            if not last_result.success:
                break
        return last_result

    def parallel(self, instructions: list[str]) -> list[Result]:
        """Run instructions in parallel via fork. Merge if no conflicts."""
        return asyncio.run(self.aparallel(instructions))

    async def aparallel(self, instructions: list[str]) -> list[Result]:
        """Run instructions in parallel (async)."""
        # Fork for each instruction
        agents = [self.fork() for _ in instructions]
        tasks = [agent.arun(instruction) for agent, instruction in zip(agents, instructions, strict=False)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        final_results: list[Result] = []
        for agent, r in zip(agents, results, strict=False):
            if isinstance(r, Exception):
                final_results.append(Result(output="", success=False, reason=str(r)))
            else:
                # Merge successful results
                if r.success and not r.rejected:
                    self._scope.merge_effects(agent._scope.effects)
                final_results.append(r)

        return final_results

    def task(self, task_instance: Any) -> Any:
        """Run an existing @task Pydantic model via the Agent's scope.

        Args:
            task_instance: An instantiated @task class (triggers execution).

        Returns:
            The task instance with outputs populated.
        """
        # @task classes execute on instantiation using the global scope.
        # To route through this Agent's scope, we need to set it as current.
        # For now, just return the instance (it already executed).
        return task_instance

    # ── Speculative Execution ──

    def fork(self) -> Agent:
        """Create an independent branch of this agent.

        The forked agent shares provider and device but has an independent
        scope (independent effects, snapshot of bindings).
        """
        forked = Agent.__new__(Agent)
        forked._scope = self._scope.fork()
        forked._provider = self._provider
        forked._device = self._device
        forked._capabilities = self._capabilities
        forked._trajectory = []
        return forked

    def merge(self, branch: Agent) -> None:
        """Adopt a branch's effects into this agent."""
        self._scope.merge_effects(branch._scope.effects)

    def discard(self, branch: Agent) -> None:
        """Discard a branch — zero side effects."""
        branch._scope.discard()

    # ── Context & Resources ──

    def bind(self, name: str, resource: Any) -> Any:
        """Bind a context resource (WorkspaceRef, SessionState, etc.)."""
        return self._scope.bind(name, resource)

    def grant(self, *caps: str) -> None:
        """Add capabilities mid-session."""
        self._capabilities = self._capabilities | frozenset(caps)

    def revoke(self, *caps: str) -> None:
        """Remove capabilities mid-session."""
        self._capabilities = self._capabilities - frozenset(caps)

    # ── Inspection ──

    @property
    def effects(self) -> Any:
        """Access the effect stream."""
        return self._scope.effects

    @property
    def trajectory(self) -> list[dict[str, Any]]:
        """Command history."""
        return list(self._trajectory)

    # ── Escape Hatch ──

    def scope(self) -> Scope:
        """Access the underlying Scope for full framework control."""
        return self._scope

    # ── Lifecycle ──

    def cleanup(self) -> None:
        """Tear down container and scope."""
        if self._device and hasattr(self._device, "cleanup"):
            try:
                self._device.cleanup()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Device cleanup error: {e}")
        self._device = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()

    # ── Internal ──

    def _build_binding(self, instruction: str) -> ProviderBinding:
        """Build a ProviderBinding from current state."""
        return ProviderBinding(
            context_ids="agent",
            capabilities=self._capabilities,
            system_prompt_additions=[f"You are an AI agent. Solve the following task:\n\n{instruction}"],
        )
