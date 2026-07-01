"""Runtime-owned LocalDevice implementation for in-process execution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shepherd_core.foundation.protocols.device import (
    DeviceCapabilities,
    EffectBundle,
    ExecutionResult,
    ExecutionSpec,
    SandboxConfig,
    SandboxHandle,
)

if TYPE_CHECKING:
    from shepherd_core.foundation.protocols.scope import ScopeProtocol


@dataclass(frozen=True)
class LocalSandboxHandle:
    """Handle to a local sandbox with no real isolation."""

    sandbox_id: str
    device_name: str = "local"


@dataclass
class LocalDevice:
    """Device that runs tasks in the current process."""

    name: str = "local"
    capabilities: DeviceCapabilities = field(
        default_factory=lambda: DeviceCapabilities(
            isolation_level="none",
            effect_capture="git",
            supports_checkpoint=False,
            supports_restore=False,
            supports_dmtcp=False,
            supports_parallel=True,
        )
    )

    async def create_sandbox(
        self,
        scope: ScopeProtocol,
        config: SandboxConfig,
    ) -> LocalSandboxHandle:
        del scope, config
        return LocalSandboxHandle(sandbox_id=str(uuid.uuid4()))

    async def execute(
        self,
        sandbox: SandboxHandle,
        spec: ExecutionSpec,
    ) -> ExecutionResult:
        del spec
        return ExecutionResult(
            success=True,
            output_text="",
            metadata={
                "device": "local",
                "sandbox_id": sandbox.sandbox_id,
                "note": "Execution delegated to in-process path",
            },
        )

    async def extract_effects(
        self,
        sandbox: SandboxHandle,
        execution_result: ExecutionResult,
    ) -> EffectBundle:
        del execution_result
        return EffectBundle(
            context_effects={},
            lifecycle_effects=[],
            execution_metadata={
                "device": "local",
                "sandbox_id": sandbox.sandbox_id,
            },
        )

    async def cleanup(
        self,
        sandbox: SandboxHandle,
    ) -> None:
        del sandbox


__all__ = ["LocalDevice", "LocalSandboxHandle"]
