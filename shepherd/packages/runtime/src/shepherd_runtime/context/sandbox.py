"""Runtime-owned git worktree sandbox implementation."""

from __future__ import annotations

import atexit
import contextlib
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from typing_extensions import Self

try:
    from git import GitCommandError, Repo  # type: ignore[import-not-found,unused-ignore]
    from git.exc import InvalidGitRepositoryError, NoSuchPathError  # type: ignore[import-not-found,unused-ignore]

    GITPYTHON_AVAILABLE = True
except ImportError:
    GITPYTHON_AVAILABLE = False
    Repo = None  # type: ignore[assignment,misc,unused-ignore]
    GitCommandError = Exception  # type: ignore[assignment,misc,unused-ignore]
    InvalidGitRepositoryError = Exception  # type: ignore[assignment,misc,unused-ignore]
    NoSuchPathError = Exception  # type: ignore[assignment,misc,unused-ignore]

from shepherd_core.effects import DiffPatch

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import types
    from collections.abc import Sequence

    from shepherd_core.context.kernel import ExecutionContext


def _require_gitpython() -> None:
    """Raise ImportError if GitPython is not available."""
    if not GITPYTHON_AVAILABLE:
        raise ImportError("GitPython is required for GitWorktreeSandbox. Install it with: pip install gitpython")


class OrphanedWorktreeRegistry:
    """Registry for tracking worktrees to cleanup on process exit."""

    _instance: OrphanedWorktreeRegistry | None = None
    _registered_atexit: bool = False

    def __init__(self) -> None:
        self._worktrees: dict[str, str] = {}

    @classmethod
    def get(cls) -> OrphanedWorktreeRegistry:
        if cls._instance is None:
            cls._instance = cls()
            if not cls._registered_atexit:
                atexit.register(cls._cleanup_all)
                cls._registered_atexit = True
        return cls._instance

    def register(self, worktree_path: str, source_repo: str) -> None:
        self._worktrees[worktree_path] = source_repo

    def unregister(self, worktree_path: str) -> None:
        self._worktrees.pop(worktree_path, None)

    @classmethod
    def _cleanup_all(cls) -> None:
        if cls._instance is None:
            return

        for wt_path, repo_path in list(cls._instance._worktrees.items()):
            try:
                repo = Repo(repo_path)
                repo.git.worktree("remove", "--force", wt_path)
            except (GitCommandError, InvalidGitRepositoryError, NoSuchPathError, OSError) as e:
                logger.debug("Failed to cleanup orphaned worktree %s: %s", wt_path, e)
                shutil.rmtree(wt_path, ignore_errors=True)

    @classmethod
    def prune_stale(cls, repo_path: str) -> None:
        _require_gitpython()
        try:
            repo = Repo(repo_path)
            repo.git.worktree("prune")
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to prune stale worktrees in %s: %s", repo_path, e)


@dataclass
class GitWorktreeSandbox:
    """Git worktree-based sandbox for isolated agent execution."""

    source_repo: str | Path
    base_commit: str = "HEAD"
    pending_patches: tuple[DiffPatch, ...] = ()
    _worktree_path: Path | None = field(default=None, repr=False)
    _setup_time_ms: float | None = field(default=None, repr=False)
    _source_repo_obj: Any = field(default=None, repr=False)
    _worktree_repo_obj: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_gitpython()

    @property
    def path(self) -> Path:
        if self._worktree_path is None:
            raise RuntimeError("Sandbox not set up. Call setup() first.")
        return self._worktree_path

    def _get_source_repo(self) -> Repo:
        if self._source_repo_obj is None:
            try:
                self._source_repo_obj = Repo(str(self.source_repo))
            except (InvalidGitRepositoryError, NoSuchPathError) as e:
                raise RuntimeError(f"Not a valid git repository: {self.source_repo}") from e
        return self._source_repo_obj  # type: ignore[no-any-return]

    def setup(self, context: ExecutionContext | None = None) -> None:
        start = time.perf_counter()

        source_repo = self._get_source_repo()
        self._worktree_path = Path(tempfile.mkdtemp(prefix="shepherd-sandbox-"))
        resolved_commit = self._resolve_commit(self.base_commit)

        try:
            source_repo.git.worktree(
                "add",
                "--detach",
                str(self._worktree_path),
                resolved_commit,
            )
        except GitCommandError as e:
            shutil.rmtree(self._worktree_path, ignore_errors=True)
            self._worktree_path = None
            raise RuntimeError(f"Failed to create worktree: {e.stderr}") from e

        self._worktree_repo_obj = Repo(self._worktree_path)
        OrphanedWorktreeRegistry.get().register(str(self._worktree_path), str(self.source_repo))

        if self.pending_patches:
            self._apply_pending_patches()

        self._setup_time_ms = (time.perf_counter() - start) * 1000

    def _apply_pending_patches(self) -> None:
        wt_repo = self._worktree_repo_obj

        for i, patch in enumerate(self.pending_patches):
            if not patch.patch.strip():
                continue

            try:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
                    f.write(patch.patch)
                    patch_file = f.name

                try:
                    with open(patch_file) as patch_stream:
                        wt_repo.git.apply("--index", istream=patch_stream)
                finally:
                    Path(patch_file).unlink(missing_ok=True)

            except GitCommandError as e:
                self.discard()
                raise RuntimeError(f"Failed to apply patch {i + 1}/{len(self.pending_patches)}: {e.stderr}") from e

        with contextlib.suppress(GitCommandError):
            wt_repo.git.commit("-m", "shepherd: checkpoint from pending patches", "--allow-empty")

    def _resolve_commit(self, commit: str) -> str:
        source_repo = self._get_source_repo()
        try:
            return source_repo.git.rev_parse(commit)  # type: ignore[no-any-return]
        except GitCommandError as e:
            raise RuntimeError(f"Failed to resolve commit '{commit}': {e.stderr}") from e

    def git_diff(self) -> str:
        if self._worktree_repo_obj is None:
            raise RuntimeError("Sandbox not set up")

        wt_repo = self._worktree_repo_obj
        wt_repo.git.add("-A")
        return wt_repo.git.diff("--binary", "--cached", "HEAD")  # type: ignore[no-any-return]

    def changed_files(self) -> Sequence[str]:
        if self._worktree_repo_obj is None:
            raise RuntimeError("Sandbox not set up")

        wt_repo = self._worktree_repo_obj
        wt_repo.git.add("-A")
        output = wt_repo.git.diff("--name-only", "--cached", "HEAD")
        files = output.strip().split("\n") if output.strip() else []
        return [file_path for file_path in files if file_path]

    def discard(self) -> None:
        if self._worktree_path is None:
            return

        wt_path = str(self._worktree_path)
        OrphanedWorktreeRegistry.get().unregister(wt_path)
        self._worktree_repo_obj = None

        try:
            source_repo = self._get_source_repo()
            source_repo.git.worktree("remove", "--force", wt_path)
        except GitCommandError:
            if self._worktree_path.exists():
                shutil.rmtree(self._worktree_path, ignore_errors=True)

        self._worktree_path = None

    def extract_patch(self, source_step: str | None = None) -> DiffPatch | None:
        diff = self.git_diff()
        if not diff.strip():
            return None

        files = tuple(self.changed_files())
        return DiffPatch.from_diff(diff, files, source_step)

    @property
    def setup_time_ms(self) -> float | None:
        return self._setup_time_ms

    def __enter__(self) -> Self:
        self.setup()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        self.discard()


__all__ = [
    "GITPYTHON_AVAILABLE",
    "GitWorktreeSandbox",
    "OrphanedWorktreeRegistry",
]
