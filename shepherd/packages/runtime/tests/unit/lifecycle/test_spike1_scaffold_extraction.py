"""Spike 1: Scaffold extraction from _execute_on_device().

Validates that _execute_on_device() can be factored into a scaffold +
spec-builder callback without behavioral regression for LLM tasks.

Work items:
1. Verify classification — shared vs LLM-specific steps
2. Validate callback signature — context_states keys match binding names
3. Extract the scaffold — mock-based proof that scaffold + callback works
4. Run full test suite — verified externally after this file passes
5. Cleanup correctness — cleanup runs on all failure modes
6. Pipeline bypass — phase_index positions after __aenter__ and _mark_device_phases_completed
7. Empty EffectCollector — extract_effects handles empty/missing data
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from shepherd_core.foundation.protocols.device import (
    DeviceCapabilities,
    EffectBundle,
    ExecutionSpec,
    SandboxConfig,
)
from shepherd_core.types import ExecutionResult, ProviderCapabilities
from shepherd_runtime._lifecycle._phase_context import Attribution, PhaseContext
from shepherd_runtime._lifecycle_impl import ExecutionLifecycle
from shepherd_runtime._phase_cache import CacheStorePhase

# ===========================================================================
# Helpers
# ===========================================================================


def _make_scope(bindings: list[MagicMock] | None = None) -> MagicMock:
    """Create a mock scope with effect tracking."""
    scope = MagicMock()
    scope.emit = MagicMock()
    scope.update_context = MagicMock()
    scope.mark_binding_lifecycle = MagicMock()
    scope.all_bindings = MagicMock(return_value=bindings or [])
    scope.current_device = None
    scope.effects = MagicMock()
    scope._get_cache_store = MagicMock(return_value=None)
    scope._get_cache_config = MagicMock(return_value=None)
    return scope


def _make_provider(provider_id: str = "test-provider") -> MagicMock:
    """Create a mock provider."""
    provider = MagicMock()
    provider.provider_id = provider_id
    provider.capabilities = ProviderCapabilities(provider_type="test")
    provider.validate_binding = MagicMock()
    provider.formatter = MagicMock()
    provider.execute_sdk = AsyncMock(return_value=ExecutionResult(output_text="LLM output"))
    return provider


def _make_device() -> MagicMock:
    """Create a mock device with container isolation."""
    device = MagicMock()
    device.name = "mock-container"
    device.capabilities = DeviceCapabilities(
        isolation_level="container",
        effect_capture="overlay",
    )
    device.create_sandbox = AsyncMock(return_value=MagicMock(sandbox_id="sb-1", device_name="mock"))
    device.execute = AsyncMock(return_value=ExecutionResult(output_text="Device output", metadata={}))
    device.extract_effects = AsyncMock(return_value=EffectBundle(context_effects={}, lifecycle_effects=[]))
    device.cleanup = AsyncMock()
    return device


def _make_binding(name: str, *, with_to_state: bool = True) -> MagicMock:
    """Create a mock binding with optional to_state support."""
    binding = MagicMock()
    binding.name = name
    ctx = MagicMock()
    if with_to_state:
        state = MagicMock()
        state.context_type = name
        ctx.to_state = MagicMock(return_value=state)
    else:
        del ctx.to_state  # ensure hasattr returns False
    ctx.capabilities = {"read", "write"}
    binding.context = ctx
    return binding


# ===========================================================================
# Work Item 1: Verify Classification
# ===========================================================================


@pytest.mark.spike
class TestClassificationVerification:
    """Verify that shared steps don't depend on LLM-specific computed values."""

    def test_sandbox_config_takes_only_context_states(self) -> None:
        """SandboxConfig constructor requires only context_states (no provider info)."""
        sig = inspect.signature(SandboxConfig)
        params = list(sig.parameters.keys())
        # context_states is the only required field (no default)
        required = [name for name, p in sig.parameters.items() if p.default is inspect.Parameter.empty]
        assert required == ["context_states"], (
            f"SandboxConfig required params: {required} — expected only context_states"
        )
        # Verify no provider-related params exist
        provider_related = {"provider_config", "provider_id", "model", "provider"}
        assert provider_related.isdisjoint(set(params)), (
            f"SandboxConfig has provider-related params: {provider_related & set(params)}"
        )

    def test_extract_effects_signature_is_spec_agnostic(self) -> None:
        """DeviceProtocol.extract_effects takes (sandbox, execution_result), not spec."""
        from shepherd_core.foundation.protocols.device import DeviceProtocol

        # Get the extract_effects method from the protocol
        method = DeviceProtocol.extract_effects
        sig = inspect.signature(method)
        param_names = list(sig.parameters.keys())
        # Should be: self, sandbox, execution_result
        assert "spec" not in param_names, "extract_effects should not take spec"
        assert "execution_spec" not in param_names, "extract_effects should not take execution_spec"
        assert param_names == ["self", "sandbox", "execution_result"], f"extract_effects params: {param_names}"

    def test_cache_store_phase_does_not_access_spec_or_prompt(self) -> None:
        """CacheStorePhase.execute reads from PhaseContext, not spec or prompt directly."""
        sig = inspect.signature(CacheStorePhase.execute)
        param_names = list(sig.parameters.keys())
        # Should be: self, ctx
        assert param_names == ["self", "ctx"], f"CacheStorePhase.execute params: {param_names}"

        # Verify the source code doesn't reference 'spec' or access prompt
        source = inspect.getsource(CacheStorePhase.execute)
        assert "spec." not in source, "CacheStorePhase.execute should not access spec"
        assert "ctx.prompt" not in source, "CacheStorePhase.execute should not access ctx.prompt"

    def test_execution_spec_fields_are_llm_specific(self) -> None:
        """ExecutionSpec contains prompt and provider_config (LLM-specific)."""
        sig = inspect.signature(ExecutionSpec)
        params = set(sig.parameters.keys())
        assert "prompt" in params
        assert "provider_config" in params
        # These are LLM-specific; the scaffold should NOT construct them
        assert "tools" in params
        assert "output_format" in params


# ===========================================================================
# Work Item 2: Validate Callback Signature
# ===========================================================================


@pytest.mark.spike
class TestCallbackSignature:
    """Validate that context_states keys match binding names."""

    def test_context_states_keys_match_binding_names(self) -> None:
        """context_states dict is keyed by binding.name, matching what programmatic
        spec builder would receive as context_fields keys."""
        bindings = [
            _make_binding("workspace"),
            _make_binding("session"),
            _make_binding("no_state", with_to_state=False),
        ]

        # Reproduce the serialization logic from _execute_on_device lines 630-635
        context_states: dict[str, Any] = {}
        for binding in bindings:
            bind_ctx = binding.context
            if hasattr(bind_ctx, "to_state"):
                context_states[binding.name] = bind_ctx.to_state()

        # Verify keys match binding names (only those with to_state)
        assert set(context_states.keys()) == {"workspace", "session"}
        assert "no_state" not in context_states

    def test_llm_spec_builder_ignores_context_states(self) -> None:
        """LLM spec builder can be called with context_states but doesn't use them."""
        provider = _make_provider()
        provider.to_config = MagicMock(return_value={"model": "claude-3"})

        # Simulate the LLM spec builder closure
        def build_llm_spec(context_states: dict[str, Any]) -> ExecutionSpec:
            provider_config = {}
            if hasattr(provider, "to_config"):
                provider_config = provider.to_config()
            return ExecutionSpec(
                prompt="Fix the bug",
                provider_config=provider_config,
                output_format=None,
                tools=None,
            )

        # Call with context_states — should work fine, ignored
        spec = build_llm_spec({"workspace": MagicMock()})
        assert spec.prompt == "Fix the bug"
        assert spec.provider_config == {"model": "claude-3"}

    def test_programmatic_spec_builder_uses_context_states_keys(self) -> None:
        """Programmatic spec builder uses context_states keys for binding names."""
        context_states = {
            "workspace": MagicMock(),
            "session": MagicMock(),
        }

        # Simulate the programmatic spec builder
        def build_programmatic_spec(ctx_states: dict[str, Any]) -> ExecutionSpec:
            binding_names = list(ctx_states.keys())
            return ExecutionSpec(
                prompt="",  # no prompt for programmatic
                provider_config={},
                tools=None,
                output_format=None,
            )

        spec = build_programmatic_spec(context_states)
        # Verify it can derive binding names from keys
        assert list(context_states.keys()) == ["workspace", "session"]


# ===========================================================================
# Work Item 3: Extract the Scaffold
# ===========================================================================


@pytest.mark.spike
class TestScaffoldExtraction:
    """Prove that scaffold + callback pattern works via mock-based test."""

    async def test_llm_specific_steps_are_in_build_llm_spec_closure(self) -> None:
        """After scaffold extraction, LLM-specific steps (provider_config,
        tools, ExecutionSpec) live inside the build_llm_spec closure within
        _execute_on_device, and the scaffold is in _execute_on_device_scaffold."""
        # Verify _execute_on_device delegates to _execute_on_device_scaffold
        source = inspect.getsource(ExecutionLifecycle._execute_on_device)
        assert "_execute_on_device_scaffold" in source, (
            "_execute_on_device should delegate to _execute_on_device_scaffold"
        )

        # Verify LLM-specific steps are in the build_llm_spec closure
        assert "build_llm_spec" in source, "_execute_on_device should define build_llm_spec closure"
        assert "provider_config" in source, "build_llm_spec closure should reference provider_config"
        assert "ExecutionSpec(" in source, "build_llm_spec closure should construct ExecutionSpec"

        # Verify the scaffold contains shared steps
        scaffold_source = inspect.getsource(ExecutionLifecycle._execute_on_device_scaffold)
        assert "create_sandbox" in scaffold_source, "Scaffold should contain create_sandbox"
        assert "device.execute" in scaffold_source, "Scaffold should contain device.execute"
        assert "extract_effects" in scaffold_source, "Scaffold should contain extract_effects"
        assert "cleanup" in scaffold_source, "Scaffold should contain cleanup"
        assert "build_spec(context_states)" in scaffold_source, (
            "Scaffold should call build_spec callback with context_states"
        )

    async def test_scaffold_with_callback_pattern(self) -> None:
        """Prove that a scaffold + spec-builder callback works end-to-end."""
        device = _make_device()
        bindings = [_make_binding("workspace")]
        scope = _make_scope(bindings)

        # Track call order
        call_order: list[str] = []

        # Simulated scaffold (extracted from _execute_on_device)
        async def scaffold(
            device: Any,
            build_spec: Any,
            lifecycle: Any,
        ) -> ExecutionResult:
            # Step 0: Cache check (shared) — skipped in this test
            call_order.append("cache_check")

            # Step 1: Serialize context states (shared)
            context_states: dict[str, Any] = {}
            for binding in lifecycle._bindings:
                bind_ctx = binding.context
                if hasattr(bind_ctx, "to_state"):
                    context_states[binding.name] = bind_ctx.to_state()
            call_order.append("serialize_context")

            # Step 2: Create sandbox (shared)
            config = SandboxConfig(context_states=context_states)
            sandbox = await device.create_sandbox(scope, config)
            call_order.append("create_sandbox")

            try:
                # Step 3: Build spec (callback — LLM or programmatic)
                spec = build_spec(context_states)
                call_order.append("build_spec")

                # Step 4: Execute on device (shared)
                result = await device.execute(sandbox, spec)
                call_order.append("device_execute")

                # Step 5: Extract effects (shared)
                bundle = await device.extract_effects(sandbox, result)
                call_order.append("extract_effects")

                # Step 6: Apply effects (shared)
                call_order.append("apply_effects")

                # Step 7: Session merge — gated on LLM (not in programmatic)
                if spec.prompt and hasattr(device, "merge_session_to_host"):
                    call_order.append("merge_session")

                # Step 8: Cache store (shared)
                call_order.append("cache_store")

                return result

            finally:
                # Step 9: Cleanup (shared, always runs)
                await device.cleanup(sandbox, preserve_overlays=True)
                call_order.append("cleanup")

        # --- Test with LLM spec builder ---
        provider = _make_provider()
        provider.to_config = MagicMock(return_value={"model": "claude-3"})

        lifecycle = MagicMock()
        lifecycle._bindings = bindings

        def llm_spec_builder(context_states: dict[str, Any]) -> ExecutionSpec:
            provider_config = provider.to_config()
            return ExecutionSpec(
                prompt="Fix the bug",
                provider_config=provider_config,
            )

        result = await scaffold(device, llm_spec_builder, lifecycle)
        assert result.output_text == "Device output"
        assert call_order == [
            "cache_check",
            "serialize_context",
            "create_sandbox",
            "build_spec",
            "device_execute",
            "extract_effects",
            "apply_effects",
            "merge_session",
            "cache_store",
            "cleanup",
        ]

        # Verify device was called correctly
        device.create_sandbox.assert_called_once()
        device.execute.assert_called_once()
        device.extract_effects.assert_called_once()
        device.cleanup.assert_called_once()

    async def test_scaffold_with_programmatic_spec_builder(self) -> None:
        """Scaffold works with programmatic spec builder (no prompt, no merge)."""
        device = _make_device()
        bindings = [_make_binding("workspace")]
        scope = _make_scope(bindings)
        call_order: list[str] = []

        async def scaffold(
            device: Any,
            build_spec: Any,
            bindings: list[Any],
        ) -> ExecutionResult:
            context_states: dict[str, Any] = {}
            for binding in bindings:
                bind_ctx = binding.context
                if hasattr(bind_ctx, "to_state"):
                    context_states[binding.name] = bind_ctx.to_state()

            config = SandboxConfig(context_states=context_states)
            sandbox = await device.create_sandbox(scope, config)

            try:
                spec = build_spec(context_states)
                call_order.append("build_spec")

                result = await device.execute(sandbox, spec)

                bundle = await device.extract_effects(sandbox, result)

                # Session merge gated: programmatic has empty prompt
                if spec.prompt:
                    call_order.append("merge_session")

                return result
            finally:
                await device.cleanup(sandbox, preserve_overlays=True)
                call_order.append("cleanup")

        def programmatic_spec_builder(context_states: dict[str, Any]) -> ExecutionSpec:
            return ExecutionSpec(
                prompt="",
                provider_config={},
            )

        result = await scaffold(device, programmatic_spec_builder, bindings)
        assert result.output_text == "Device output"
        # No merge_session for programmatic
        assert "merge_session" not in call_order
        assert "cleanup" in call_order


# ===========================================================================
# Work Item 5: Cleanup Correctness Under Failures
# ===========================================================================


@pytest.mark.spike
class TestCleanupCorrectness:
    """Verify cleanup runs when various steps fail."""

    async def test_cleanup_runs_when_spec_builder_raises(self) -> None:
        """device.cleanup() is called even if spec builder raises."""
        device = _make_device()
        scope = _make_scope([_make_binding("ws")])

        def failing_spec_builder(context_states: dict[str, Any]) -> ExecutionSpec:
            raise RuntimeError("to_config failed")

        cleanup_called = False

        async def _run() -> None:
            nonlocal cleanup_called
            config = SandboxConfig(context_states={})
            sandbox = await device.create_sandbox(scope, config)
            try:
                _ = failing_spec_builder({})
                await device.execute(sandbox, ExecutionSpec(prompt="", provider_config={}))
            finally:
                await device.cleanup(sandbox, preserve_overlays=True)
                cleanup_called = True

        with pytest.raises(RuntimeError, match="to_config failed"):
            await _run()
        assert cleanup_called

    async def test_cleanup_runs_when_device_execute_raises(self) -> None:
        """device.cleanup() is called even if device.execute() raises."""
        device = _make_device()
        device.execute = AsyncMock(side_effect=RuntimeError("Container crashed"))
        scope = _make_scope()

        cleanup_called = False

        async def _run() -> None:
            nonlocal cleanup_called
            config = SandboxConfig(context_states={})
            sandbox = await device.create_sandbox(scope, config)
            try:
                spec = ExecutionSpec(prompt="test", provider_config={})
                await device.execute(sandbox, spec)
            finally:
                await device.cleanup(sandbox, preserve_overlays=True)
                cleanup_called = True

        with pytest.raises(RuntimeError, match="Container crashed"):
            await _run()
        assert cleanup_called

    async def test_cleanup_runs_when_extract_effects_raises(self) -> None:
        """device.cleanup() is called even if extract_effects() raises."""
        device = _make_device()
        device.extract_effects = AsyncMock(side_effect=RuntimeError("Extraction failed"))
        scope = _make_scope()

        cleanup_called = False

        async def _run() -> None:
            nonlocal cleanup_called
            config = SandboxConfig(context_states={})
            sandbox = await device.create_sandbox(scope, config)
            try:
                spec = ExecutionSpec(prompt="test", provider_config={})
                result = await device.execute(sandbox, spec)
                await device.extract_effects(sandbox, result)
            finally:
                await device.cleanup(sandbox, preserve_overlays=True)
                cleanup_called = True

        with pytest.raises(RuntimeError, match="Extraction failed"):
            await _run()
        assert cleanup_called

    async def test_actual_execute_on_device_cleanup_on_failure(self) -> None:
        """The real _execute_on_device cleans up when device.execute raises."""
        device = _make_device()
        device.execute = AsyncMock(side_effect=RuntimeError("Boom"))
        bindings = [_make_binding("workspace")]
        scope = _make_scope(bindings)
        scope.current_device = device
        provider = _make_provider()

        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)
        lifecycle._entered = True
        lifecycle._bindings = bindings

        # Set up pipeline with a mock
        pipeline = MagicMock()
        pipeline.current_context = PhaseContext(
            scope=scope,
            provider=provider,
            task_name="test",
            attribution=Attribution(task_name="test", provider_id="test", source="llm"),
        )
        pipeline.update_context = MagicMock()
        pipeline._get_phase_index = MagicMock(return_value=None)
        lifecycle._pipeline = pipeline

        with pytest.raises(RuntimeError, match="Boom"):
            await lifecycle._execute_on_device(device, "test prompt")

        # Verify cleanup was called despite the failure
        device.cleanup.assert_awaited_once()


# ===========================================================================
# Work Item 6: Pipeline Bypass
# ===========================================================================


@pytest.mark.spike
class TestPipelineBypass:
    """Verify phase_index positions and _mark_device_phases_completed behavior."""

    async def test_phase_index_after_aenter(self) -> None:
        """After __aenter__ runs configure + prepare, _phase_index is at 2 (cache_check)."""
        scope = _make_scope()
        provider = _make_provider()

        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)

        async with lifecycle:
            pipeline = lifecycle._pipeline
            assert pipeline is not None

            # After configure (index 0) + prepare (index 1), index should be 2
            assert pipeline._phase_index == 2, f"Expected phase_index=2 (cache_check), got {pipeline._phase_index}"

            # Verify phase at index 2 is cache_check
            phase_names = [p.name for p in pipeline.phases]
            assert phase_names[2] == "cache_check", f"Phase at index 2 is '{phase_names[2]}', expected 'cache_check'"

    async def test_mark_device_phases_completed_advances_to_cleanup(self) -> None:
        """_mark_device_phases_completed advances _phase_index to cleanup."""
        scope = _make_scope()
        provider = _make_provider()

        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)

        async with lifecycle:
            pipeline = lifecycle._pipeline
            assert pipeline is not None

            # Get phase names and expected positions
            phase_names = [p.name for p in pipeline.phases]
            cleanup_index = phase_names.index("cleanup")

            # Call _mark_device_phases_completed
            lifecycle._mark_device_phases_completed()

            # Verify phase_index advanced to cleanup
            assert pipeline._phase_index == cleanup_index, (
                f"Expected phase_index={cleanup_index} (cleanup), got {pipeline._phase_index}"
            )

            # Verify device phases are in completed list
            completed_names = [p.name for p in pipeline._completed_phases]
            for device_phase in ["execute", "artifact", "extract", "apply"]:
                assert device_phase in completed_names, f"'{device_phase}' should be in completed phases"

    async def test_pipeline_phase_order(self) -> None:
        """Verify the 9-phase pipeline order is as expected."""
        scope = _make_scope()
        provider = _make_provider()

        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)

        async with lifecycle:
            phase_names = [p.name for p in lifecycle._pipeline.phases]
            expected = [
                "configure",
                "prepare",
                "cache_check",
                "execute",
                "artifact",
                "extract",
                "apply",
                "cache_store",
                "cleanup",
            ]
            assert phase_names == expected, f"Phase order mismatch: {phase_names} != {expected}"

    async def test_aexit_only_runs_cleanup_after_device_phases_marked(self) -> None:
        """After _mark_device_phases_completed, __aexit__ runs only cleanup."""
        scope = _make_scope()
        provider = _make_provider()

        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)

        async with lifecycle:
            pipeline = lifecycle._pipeline
            assert pipeline is not None

            # Simulate what device execution does
            lifecycle._mark_device_phases_completed()

            # Record phase_index before __aexit__
            pre_exit_index = pipeline._phase_index
            phase_names = [p.name for p in pipeline.phases]
            assert phase_names[pre_exit_index] == "cleanup"

        # After __aexit__, cleanup should have run
        # (verified by the fact that __aexit__ completed without error
        # and didn't try to re-run execute/extract/apply)


# ===========================================================================
# Work Item 7: Empty EffectCollector
# ===========================================================================


@pytest.mark.spike
class TestEmptyEffectCollector:
    """Verify extract_effects handles empty/missing collector data gracefully."""

    def test_apply_effect_bundle_empty_effects(self) -> None:
        """_apply_effect_bundle handles empty effect lists without error."""
        scope = _make_scope()
        provider = _make_provider()
        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)

        # Create emitter
        emitter = MagicMock()
        lifecycle._emitter = emitter

        # Empty bundle (what programmatic tasks would produce)
        bundle = EffectBundle(
            context_effects={},
            lifecycle_effects=[],
        )

        # Should not raise
        lifecycle._apply_effect_bundle(bundle)

        # Emitter should not have been called
        emitter.emit.assert_not_called()

    def test_apply_effect_bundle_empty_per_context(self) -> None:
        """_apply_effect_bundle handles contexts with empty effect lists."""
        scope = _make_scope()
        provider = _make_provider()
        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)

        emitter = MagicMock()
        lifecycle._emitter = emitter

        # Bundle with context key but empty effects
        bundle = EffectBundle(
            context_effects={"workspace": []},
            lifecycle_effects=[],
        )

        lifecycle._apply_effect_bundle(bundle)

        # Still no emit calls since lists are empty
        emitter.emit.assert_not_called()

    def test_apply_effect_bundle_with_lifecycle_effects(self) -> None:
        """_apply_effect_bundle emits lifecycle effects correctly."""
        scope = _make_scope()
        provider = _make_provider()
        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)

        emitter = MagicMock()
        lifecycle._emitter = emitter

        effect1 = MagicMock()
        effect2 = MagicMock()

        bundle = EffectBundle(
            context_effects={},
            lifecycle_effects=[effect1, effect2],
        )

        lifecycle._apply_effect_bundle(bundle)

        assert emitter.emit.call_count == 2

    def test_apply_effect_bundle_with_context_effects(self) -> None:
        """_apply_effect_bundle emits context effects with binding attribution."""
        scope = _make_scope()
        provider = _make_provider()
        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)

        emitter = MagicMock()
        lifecycle._emitter = emitter

        effect = MagicMock()
        effect.binding_name = None
        effect.with_binding = MagicMock(return_value=effect)

        bundle = EffectBundle(
            context_effects={"workspace": [effect]},
            lifecycle_effects=[],
        )

        lifecycle._apply_effect_bundle(bundle)

        effect.with_binding.assert_called_once_with("workspace")
        emitter.emit.assert_called_once()

    async def test_full_device_path_with_empty_effects(self) -> None:
        """Full _execute_on_device path works with empty effects from device."""
        device = _make_device()
        device.extract_effects = AsyncMock(return_value=EffectBundle(context_effects={}, lifecycle_effects=[]))

        bindings = [_make_binding("workspace")]
        scope = _make_scope(bindings)
        provider = _make_provider()

        lifecycle = ExecutionLifecycle(scope=scope, provider=provider)
        lifecycle._entered = True
        lifecycle._bindings = bindings

        # Set up pipeline
        pipeline = MagicMock()
        pipeline.current_context = PhaseContext(
            scope=scope,
            provider=provider,
            task_name="test",
            attribution=Attribution(task_name="test", provider_id="test", source="llm"),
        )
        pipeline.update_context = MagicMock()
        pipeline._get_phase_index = MagicMock(return_value=None)
        pipeline._phase_index = 2
        pipeline._completed_phases = []
        pipeline.phases = []
        lifecycle._pipeline = pipeline
        lifecycle._emitter = MagicMock()

        result = await lifecycle._execute_on_device(device, "test prompt")

        assert result.output_text == "Device output"
        device.extract_effects.assert_awaited_once()
        device.cleanup.assert_awaited_once()
