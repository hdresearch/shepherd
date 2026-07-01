"""Regression coverage for explicit effect contributors and runtime composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pytest
from shepherd_banking.contexts.effects import BalanceQueried
from shepherd_banking.contexts.effects import get_effect_types as get_banking_effect_types
from shepherd_coding.contexts.effects import PRMerged
from shepherd_coding.contexts.effects import get_effect_types as get_coding_effect_types
from shepherd_contexts.appstore.effects import AppStoreAPICall
from shepherd_contexts.appstore.effects import get_effect_types as get_appstore_effect_types
from shepherd_contexts.database.effects import QueryExecuted
from shepherd_contexts.database.effects import get_effect_types as get_database_effect_types
from shepherd_contexts.kvstore.effects import KeySet
from shepherd_contexts.kvstore.effects import get_effect_types as get_kvstore_effect_types
from shepherd_contexts.mcp.effects import MCPServerConnected, MCPToolCalled
from shepherd_contexts.mcp.effects import get_effect_types as get_mcp_effect_types
from shepherd_contexts.session.effects import SessionCreated
from shepherd_contexts.session.effects import get_effect_types as get_session_effect_types
from shepherd_contexts.simple_workspace import effects as simple_workspace_effects
from shepherd_contexts.simple_workspace.effects import (
    SimpleWorkspaceMaterialized,
)
from shepherd_contexts.simple_workspace.effects import (
    get_effect_types as get_simple_workspace_effect_types,
)
from shepherd_contexts.workspace.effects import BashCommand, WorkspacePatchCaptured
from shepherd_contexts.workspace.effects import get_effect_types as get_workspace_effect_types
from shepherd_core.effects import Effect
from shepherd_core.effects import contributors as core_effect_contributors
from shepherd_core.effects import discover_effects as discover_core_effects
from shepherd_runtime import effects as runtime_effects
from shepherd_runtime.cache import CacheHit, CacheStored


@dataclass
class _FakeEntryPoint:
    name: str
    contributor: object
    value: str = ""

    def load(self) -> object:
        return self.contributor


def test_workspace_effect_contributors_expose_explicit_mappings() -> None:
    """Contributor modules should expose deterministic runtime decode mappings."""
    assert get_simple_workspace_effect_types()["simple_workspace_materialized"] is SimpleWorkspaceMaterialized
    assert get_kvstore_effect_types()["key_set"] is KeySet
    assert get_workspace_effect_types()["workspace_patch_captured"] is WorkspacePatchCaptured
    assert get_workspace_effect_types()["bash_command"] is BashCommand
    assert get_session_effect_types()["session_created"] is SessionCreated
    assert get_mcp_effect_types()["mcp_server_connected"] is MCPServerConnected
    assert get_mcp_effect_types()["mcp_tool_called"] is MCPToolCalled
    assert get_appstore_effect_types()["appstore_api_call"] is AppStoreAPICall
    assert get_database_effect_types()["query_executed"] is QueryExecuted
    assert get_banking_effect_types()["balance_queried"] is BalanceQueried
    assert get_coding_effect_types()["pr_merged"] is PRMerged


def test_compose_effect_registry_discovers_workspace_contributors() -> None:
    """Runtime registry composition should discover installed workspace contributors."""
    registry = runtime_effects.compose_effect_registry()

    assert registry["cache_hit"] is CacheHit
    assert registry["cache_stored"] is CacheStored
    assert registry["simple_workspace_materialized"] is SimpleWorkspaceMaterialized
    assert registry["key_set"] is KeySet
    assert registry["workspace_patch_captured"] is WorkspacePatchCaptured
    assert registry["bash_command"] is BashCommand
    assert registry["session_created"] is SessionCreated
    assert registry["mcp_server_connected"] is MCPServerConnected
    assert registry["mcp_tool_called"] is MCPToolCalled
    assert registry["appstore_api_call"] is AppStoreAPICall
    assert registry["query_executed"] is QueryExecuted
    assert registry["balance_queried"] is BalanceQueried
    assert registry["pr_merged"] is PRMerged


def test_compose_effect_registry_rejects_duplicate_plugin_effect_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime composition should fail closed on duplicate plugin effect_type keys."""

    class DuplicateEffectA(Effect):
        effect_type: Literal["duplicate_plugin_effect"] = "duplicate_plugin_effect"

    class DuplicateEffectB(Effect):
        effect_type: Literal["duplicate_plugin_effect"] = "duplicate_plugin_effect"

    monkeypatch.setattr(
        core_effect_contributors,
        "_iter_entry_points",
        lambda group: (
            _FakeEntryPoint("dup_a", DuplicateEffectA, "pkg_a:DuplicateEffectA"),
            _FakeEntryPoint("dup_b", DuplicateEffectB, "pkg_b:DuplicateEffectB"),
        ),
    )

    with pytest.raises(runtime_effects.EffectContributorConflictError, match="duplicate_plugin_effect"):
        runtime_effects.compose_effect_registry()


def test_compose_effect_registry_rejects_kernel_effect_type_collisions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime composition should reject plugin collisions with kernel effect types."""

    class ConflictingTaskStarted(Effect):
        effect_type: Literal["task_started"] = "task_started"

    monkeypatch.setattr(
        core_effect_contributors,
        "_iter_entry_points",
        lambda group: (_FakeEntryPoint("conflict", ConflictingTaskStarted, "pkg:ConflictingTaskStarted"),),
    )

    with pytest.raises(runtime_effects.EffectContributorConflictError, match="task_started"):
        runtime_effects.compose_effect_registry()


def test_compose_effect_registry_rejects_runtime_effect_type_collisions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime composition should reject plugin collisions with runtime-owned cache effects."""

    class ConflictingCacheHit(Effect):
        effect_type: Literal["cache_hit"] = "cache_hit"

    monkeypatch.setattr(
        core_effect_contributors,
        "_iter_entry_points",
        lambda group: (_FakeEntryPoint("conflict", ConflictingCacheHit, "pkg:ConflictingCacheHit"),),
    )

    with pytest.raises(runtime_effects.EffectContributorConflictError, match="cache_hit"):
        runtime_effects.compose_effect_registry()


def test_compose_effect_registry_rejects_duplicate_entry_point_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime composition should fail closed on duplicate contributor names."""

    class PluginEffectA(Effect):
        effect_type: Literal["plugin_effect_a"] = "plugin_effect_a"

    class PluginEffectB(Effect):
        effect_type: Literal["plugin_effect_b"] = "plugin_effect_b"

    monkeypatch.setattr(
        core_effect_contributors,
        "_iter_entry_points",
        lambda group: (
            _FakeEntryPoint("duplicate_name", PluginEffectA, "pkg_a:PluginEffectA"),
            _FakeEntryPoint("duplicate_name", PluginEffectB, "pkg_b:PluginEffectB"),
        ),
    )

    with pytest.raises(runtime_effects.EffectContributorNameConflictError, match="duplicate_name"):
        runtime_effects.compose_effect_registry()


def test_compose_effect_registry_rejects_invalid_contributor_mappings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime composition should validate explicit contributor mappings eagerly."""

    class InvalidContributor:
        @staticmethod
        def get_effect_types() -> dict[str, object]:
            return {"mismatched_key": BalanceQueried}

    monkeypatch.setattr(
        core_effect_contributors,
        "_iter_entry_points",
        lambda group: (_FakeEntryPoint("invalid_mapping", InvalidContributor(), "pkg:InvalidContributor"),),
    )

    with pytest.raises(runtime_effects.EffectContributorValidationError, match="mismatched_key"):
        runtime_effects.compose_effect_registry()


def test_core_discover_effects_normalizes_module_contributors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Core discovery should reflect the same class-or-module contributor contract."""
    monkeypatch.setattr(
        core_effect_contributors,
        "_iter_entry_points",
        lambda group: (
            _FakeEntryPoint("simple_workspace", simple_workspace_effects, "shepherd_contexts.simple_workspace.effects"),
        ),
    )

    discovered = discover_core_effects()

    assert discovered["simple_workspace_materialized"] is SimpleWorkspaceMaterialized
