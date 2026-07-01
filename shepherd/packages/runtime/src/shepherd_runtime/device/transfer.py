"""Transfer helpers owned by `shepherd-runtime`."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shepherd_core.errors import BindingNotFoundError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shepherd_core.effects import DiffPatch

    from shepherd_runtime.scope_types import TransferScope

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransferBundle:
    """Everything needed to reconstruct context state on another device."""

    state: Mapping[str, Any] = field(default_factory=dict)
    files: Mapping[str, bytes] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    mounts: Mapping[str, str] = field(default_factory=dict)
    symlinks: Mapping[str, str] = field(default_factory=dict)
    manifest: Mapping[str, str] = field(default_factory=dict)

    @staticmethod
    def compose(bundles: list[TransferBundle]) -> TransferBundle:
        """Compose multiple bundles into a single bundle."""
        if not bundles:
            return TransferBundle()

        if len(bundles) == 1:
            return bundles[0]

        merged_state: dict[str, Any] = {}
        for bundle in bundles:
            merged_state.update(bundle.state)

        merged_files: dict[str, bytes] = {}
        for bundle in bundles:
            for path, content in bundle.files.items():
                if path in merged_files and merged_files[path] != content:
                    raise ValueError(f"Conflicting file content for '{path}' in bundles")
                merged_files[path] = content

        merged_env: dict[str, str] = {}
        for bundle in bundles:
            for var, value in bundle.env.items():
                if var in merged_env and merged_env[var] != value:
                    raise ValueError(f"Conflicting env var '{var}': '{merged_env[var]}' vs '{value}'")
                merged_env[var] = value

        merged_mounts: dict[str, str] = {}
        for bundle in bundles:
            for host_path, container_path in bundle.mounts.items():
                if host_path in merged_mounts and merged_mounts[host_path] != container_path:
                    raise ValueError(
                        f"Conflicting mount for host path '{host_path}': "
                        f"'{merged_mounts[host_path]}' vs '{container_path}'"
                    )
                merged_mounts[host_path] = container_path

        merged_symlinks: dict[str, str] = {}
        for bundle in bundles:
            for link_path, target in bundle.symlinks.items():
                if link_path in merged_symlinks and merged_symlinks[link_path] != target:
                    raise ValueError(
                        f"Conflicting symlink for '{link_path}': '{merged_symlinks[link_path]}' vs '{target}'"
                    )
                merged_symlinks[link_path] = target

        merged_manifest: dict[str, str] = {}
        for bundle in bundles:
            for filename, content_hash in bundle.manifest.items():
                if filename in merged_manifest and merged_manifest[filename] != content_hash:
                    raise ValueError(
                        f"Conflicting manifest entry for '{filename}': "
                        f"'{merged_manifest[filename]}' vs '{content_hash}'"
                    )
                merged_manifest[filename] = content_hash

        return TransferBundle(
            state=merged_state,
            files=merged_files,
            env=merged_env,
            mounts=merged_mounts,
            symlinks=merged_symlinks,
            manifest=merged_manifest,
        )


def collect_visible_patches(
    scope: TransferScope,
    binding_name: str = "workspace",
) -> list[DiffPatch]:
    """Collect visible patches from scope using override semantics."""
    patches_by_file: dict[str, DiffPatch] = {}

    for layer in scope.effects.layers:
        effect = layer.effect
        if effect.effect_type == "workspace_patch_captured":
            effect_binding = getattr(effect, "binding_name", None)
            if (effect_binding is None or effect_binding == binding_name) and effect.patch:
                for filename in effect.patch.files_changed:
                    patches_by_file[filename] = effect.patch

    try:
        ctx = scope.get_context(binding_name)
        if ctx and hasattr(ctx, "pending_patches"):
            for patch in ctx.pending_patches:
                for filename in patch.files_changed:
                    patches_by_file[filename] = patch
    except BindingNotFoundError:
        logger.debug("No binding %r found when collecting visible patches", binding_name)

    seen: set[str] = set()
    result: list[DiffPatch] = []
    for patch in patches_by_file.values():
        patch_id = patch.sha256 or str(id(patch))
        if patch_id not in seen:
            seen.add(patch_id)
            result.append(patch)

    return result


def compute_content_hash(content: bytes) -> str:
    """Compute SHA-256 hash of content for manifest comparison."""
    return hashlib.sha256(content).hexdigest()


__all__ = [
    "TransferBundle",
    "collect_visible_patches",
    "compute_content_hash",
]
