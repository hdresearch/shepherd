"""Scope and pipeline helpers for substrate tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vcs_core._substrate_runtime import BuiltInRuntimeBinding


def set_scope(substrate: Any, scope: Any) -> None:
    """Set the scope on a substrate's internal pipeline."""
    substrate._pipeline.set_scope(scope)


def scope_runtime(pipeline: Any, *, isolated: bool = False, overlay_base: str = "ground") -> BuiltInRuntimeBinding:
    """Build simple internal runtime bindings for built-in substrate tests."""
    return BuiltInRuntimeBinding(
        pipeline=pipeline,
        is_scope_or_ancestor_isolated=lambda _scope: isolated,
        overlay_base_scope_name=lambda _scope: overlay_base,
        working_directory_for_scope=lambda _scope: Path.cwd().resolve(),
        nearest_carrier_scope=lambda _substrate, _target_id, _scope: None,
        can_create_carrier=lambda _substrate, _target_id, _scope: True,
        register_carrier=lambda _substrate, _target_id, _scope: None,
        lookup_claim=lambda _path: None,
        register_claim=lambda _substrate, _target_id, _path, _policy: None,
    )
