"""Runtime-owned built-in effect materializers."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ._effect_materialization_impl import (
    MaterializationResult,
    ReversalError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from shepherd_contexts.workspace.effects import WorkspacePatchCaptured

    from shepherd_runtime.scope import ScopeProxy

logger = logging.getLogger(__name__)


class GitWorkspacePatchMaterializer:
    """Materializes ``WorkspacePatchCaptured`` effects via ``git apply``."""

    def __init__(
        self,
        get_workspace_path: Callable[[str], Path | None],
    ):
        self._get_workspace_path = get_workspace_path

    @property
    def effect_type(self) -> type:
        from shepherd_contexts.workspace.effects import WorkspacePatchCaptured

        return WorkspacePatchCaptured

    def _resolve_workspace_path(self, effect: WorkspacePatchCaptured) -> Path | None:
        binding_name = getattr(effect, "binding_name", None)
        if binding_name:
            path = self._get_workspace_path(binding_name)
            if path:
                return path

        context_id = getattr(effect, "context_id", None)
        if context_id and context_id.startswith("workspace:"):
            parts = context_id.split(":")
            if len(parts) >= 2:
                return Path(parts[1])

        return None

    def materialize(self, effect: WorkspacePatchCaptured) -> MaterializationResult:
        workspace_path = self._resolve_workspace_path(effect)
        if workspace_path is None:
            return MaterializationResult.fail(
                "Cannot resolve workspace path from effect. "
                "Ensure binding_name is set or context_id follows format 'workspace:/path:commit'"
            )

        if not (workspace_path / ".git").exists():
            return MaterializationResult.fail(f"Not a git repository: {workspace_path}")

        patch_content = ""
        if hasattr(effect, "patch"):
            patch_obj = effect.patch
            if hasattr(patch_obj, "patch"):
                patch_content = patch_obj.patch
            elif isinstance(patch_obj, str):
                patch_content = patch_obj

        if not patch_content or patch_content.isspace():
            logger.debug("Empty patch, no-op materialization")
            return MaterializationResult.ok(
                paths_affected=(),
                workspace=str(workspace_path),
                empty_patch=True,
            )

        try:
            subprocess.run(
                ["git", "apply", "--index", "-"],
                cwd=workspace_path,
                input=patch_content,
                capture_output=True,
                text=True,
                check=True,
            )
            files_changed = getattr(effect, "files_changed", ())
            return MaterializationResult.ok(
                paths_affected=tuple(files_changed),
                workspace=str(workspace_path),
            )
        except subprocess.CalledProcessError as e:
            return MaterializationResult.fail(f"git apply failed: {e.stderr.strip() or e.stdout.strip() or str(e)}")

    def can_reverse(self, effect: WorkspacePatchCaptured) -> bool:
        workspace_path = self._resolve_workspace_path(effect)
        if workspace_path is None or not (workspace_path / ".git").exists():
            return False

        patch_content = ""
        if hasattr(effect, "patch"):
            patch_obj = effect.patch
            if hasattr(patch_obj, "patch"):
                patch_content = patch_obj.patch
            elif isinstance(patch_obj, str):
                patch_content = patch_obj

        return not (not patch_content or patch_content.isspace())

    def reverse(self, effect: WorkspacePatchCaptured) -> None:
        workspace_path = self._resolve_workspace_path(effect)
        if workspace_path is None:
            raise ReversalError(effect, "Cannot resolve workspace path")

        patch_content = ""
        if hasattr(effect, "patch"):
            patch_obj = effect.patch
            if hasattr(patch_obj, "patch"):
                patch_content = patch_obj.patch
            elif isinstance(patch_obj, str):
                patch_content = patch_obj

        if not patch_content or patch_content.isspace():
            return

        try:
            subprocess.run(
                ["git", "apply", "--reverse", "--index", "-"],
                cwd=workspace_path,
                input=patch_content,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            message = e.stderr.strip() or e.stdout.strip() or str(e)
            raise ReversalError(effect, f"git apply --reverse failed: {message}") from e


def create_workspace_materializer(scope: ScopeProxy) -> GitWorkspacePatchMaterializer:
    """Bind the workspace materializer to one runtime scope."""

    def get_workspace_path(binding_name: str) -> Path | None:
        from shepherd_core.errors import BindingNotFoundError

        try:
            context = scope.get_context(binding_name)
            if hasattr(context, "path"):
                return Path(context.path)
        except BindingNotFoundError:
            logger.debug("No binding %r found when resolving workspace path", binding_name)
        return None

    return GitWorkspacePatchMaterializer(get_workspace_path)


__all__ = ["GitWorkspacePatchMaterializer", "create_workspace_materializer"]
