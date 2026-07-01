"""Reusable contract helpers for substrate SPI and internal built-in behavior."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vcs_core._substrate_runtime import BuiltInRuntimeBinding, ContainmentSubstrate
from vcs_core.recording import RecordingPipeline
from vcs_core.spi import CommandRequest, DriverContext, SubstrateStoreIdentity
from vcs_core.types import ScopeInfo

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vcs_core.store import Store
    from vcs_core.types import EffectRecord


@dataclass(frozen=True)
class DriverCommandScenario:
    """A concrete built-in typed driver command example."""

    command: str
    params: dict[str, Any]
    expected_effect_types: tuple[str, ...]


@dataclass(frozen=True)
class BuiltInContainmentScenario:
    """A concrete built-in containment example for runtime-bound substrates."""

    exercise: Callable[[ContainmentSubstrate, ScopeInfo], None]
    expected_effect_types: tuple[str, ...]
    hints: dict[str, Any] | None = None


def ground_scope(store: Store) -> ScopeInfo:
    return ScopeInfo(
        name="ground",
        ref=store.GROUND_REF,
        instance_id="ground",
        creation_oid="",
    )


def fresh_scope(store: Store, prefix: str) -> ScopeInfo:
    return store.fork(store.GROUND_REF, f"{prefix}-{uuid.uuid4().hex[:8]}")


def bind_driver_runtime(substrate: object, store: Store) -> RecordingPipeline:
    pipeline = RecordingPipeline(store)
    substrate.bind_runtime(  # type: ignore[attr-defined]
        BuiltInRuntimeBinding(
            pipeline=pipeline,
            is_scope_or_ancestor_isolated=lambda _scope: False,
            overlay_base_scope_name=lambda _scope: "ground",
            working_directory_for_scope=lambda _scope: Path.cwd().resolve(),
        )
    )
    return pipeline


def bind_contain_runtime(
    substrate: ContainmentSubstrate,
    store: Store,
    *,
    isolated_scope_name: str,
) -> RecordingPipeline:
    pipeline = RecordingPipeline(store)
    substrate.bind_runtime(  # type: ignore[attr-defined]
        BuiltInRuntimeBinding(
            pipeline=pipeline,
            is_scope_or_ancestor_isolated=lambda scope: scope.name != "ground",
            overlay_base_scope_name=lambda scope: "ground" if scope.name == "ground" else isolated_scope_name,
            working_directory_for_scope=lambda _scope: Path.cwd().resolve(),
        )
    )
    return pipeline


def assert_driver_command_effects(
    substrate: object,
    store: Store,
    scenario: DriverCommandScenario,
) -> Sequence[EffectRecord]:
    assert substrate.commands  # type: ignore[attr-defined]
    assert scenario.command in substrate.commands  # type: ignore[attr-defined]

    pipeline = bind_driver_runtime(substrate, store)
    substrate_name = substrate.name  # type: ignore[attr-defined]
    binding_name = getattr(substrate, "binding", substrate_name)
    scope = fresh_scope(store, f"spi-driver-{substrate_name}")
    pipeline.set_scope(scope)

    outcome = substrate.prepare(
        DriverContext(
            operation_id=f"test-{uuid.uuid4().hex[:8]}",
            binding=binding_name,
            role=getattr(substrate, "role", binding_name),
            store_identity=SubstrateStoreIdentity(
                store_id=f"store_{binding_name}",
                kind=getattr(substrate, "driver_id", binding_name),
                resource_id=f"binding:{binding_name}",
            ),
        ),
        CommandRequest(command=scenario.command, params=scenario.params),
    )
    effects = outcome.effects

    assert tuple(effect.effect_type for effect in effects) == scenario.expected_effect_types

    oids = pipeline.record(effects, substrate=substrate_name, scope=scope)
    assert len(oids) == len(scenario.expected_effect_types)
    return effects


def assert_built_in_containment_conforms(
    substrate: ContainmentSubstrate,
    store: Store,
    scenario: BuiltInContainmentScenario,
) -> Sequence[EffectRecord]:
    assert isinstance(substrate, ContainmentSubstrate)
    assert substrate.commands

    parent = ground_scope(store)
    scope = fresh_scope(store, f"spi-contain-{substrate.name}")
    pipeline = bind_contain_runtime(substrate, store, isolated_scope_name=scope.name)
    pipeline.set_scope(scope)

    substrate.branch(scope.name, parent_scope=parent, hints=scenario.hints or {"isolated": True})
    scenario.exercise(substrate, scope)

    prepared_once = tuple(substrate.prepare_merge(scope, parent))
    prepared_twice = tuple(substrate.prepare_merge(scope, parent))

    assert tuple(effect.effect_type for effect in prepared_once) == scenario.expected_effect_types
    assert prepared_twice == prepared_once

    pipeline.record(prepared_once, substrate=substrate.name, scope=scope)
    substrate.commit_merge(scope.name, parent_scope=parent)
    return prepared_once
