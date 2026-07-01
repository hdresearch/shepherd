"""Git Effect Type Fixtures.

These are test-only effect definitions that match the design spec in
DESIGN-git-operation-effects.md. Production implementations must be
compatible with these interfaces.

Key Design Decisions Encoded:
- D9: All effects have binding_name="workspace" for stable routing
- D5: PendingGitOperation types are separate from file patches
- D12: MaterializationContext handles SHA translation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

# =============================================================================
# Reversibility (from DESIGN-git-operation-effects.md)
# =============================================================================


class ReversibilityLevel(str, Enum):
    """Effect reversibility levels."""

    AUTO = "auto"  # Framework handles (e.g., FilePatch)
    COMPENSABLE = "compensable"  # User provides compensation
    NONE = "none"  # Irrevocable (e.g., GitPushPerformed)


# =============================================================================
# Git Effects (matching design spec)
# =============================================================================


class GitBranchCreated(BaseModel):
    """A git branch was created.

    Design Decision D9: Emit with binding_name="workspace" for proper routing.
    """

    model_config = ConfigDict(frozen=True)

    effect_type: Literal["git_branch_created"] = "git_branch_created"
    branch_name: str
    from_commit: str  # Commit SHA where branch points
    caused_by: str | None = None  # Tool call ID (for attribution)
    binding_name: str = "workspace"  # D9: Required for routing

    # Metadata
    timestamp: float | None = None
    task_name: str | None = None
    context_id: str | None = None


class GitCommitCreated(BaseModel):
    """A git commit was created.

    Design Decision D6: This does NOT clear pending_patches.
    Patches record file changes; commits record structural operations.
    """

    model_config = ConfigDict(frozen=True)

    effect_type: Literal["git_commit_created"] = "git_commit_created"
    sha: str  # Full 40-char SHA
    message: str
    author: str  # "Name <email> timestamp timezone"
    parent_shas: tuple[str, ...] = ()
    caused_by: str | None = None
    binding_name: str = "workspace"

    # Metadata
    timestamp: float | None = None
    task_name: str | None = None
    context_id: str | None = None


class GitCheckoutPerformed(BaseModel):
    """Switched to a different branch or ref."""

    model_config = ConfigDict(frozen=True)

    effect_type: Literal["git_checkout_performed"] = "git_checkout_performed"
    target_ref: str
    previous_ref: str | None = None
    caused_by: str | None = None
    binding_name: str = "workspace"

    # Metadata
    timestamp: float | None = None
    task_name: str | None = None
    context_id: str | None = None


class GitTagCreated(BaseModel):
    """A git tag was created."""

    model_config = ConfigDict(frozen=True)

    effect_type: Literal["git_tag_created"] = "git_tag_created"
    tag_name: str
    at_commit: str
    is_annotated: bool = False
    message: str | None = None
    caused_by: str | None = None
    binding_name: str = "workspace"

    # Metadata
    timestamp: float | None = None
    task_name: str | None = None
    context_id: str | None = None


class GitMergePerformed(BaseModel):
    """A git merge was performed."""

    model_config = ConfigDict(frozen=True)

    effect_type: Literal["git_merge_performed"] = "git_merge_performed"
    source_ref: str
    target_ref: str
    merge_commit_sha: str | None = None  # None if fast-forward
    fast_forward: bool = False
    caused_by: str | None = None
    binding_name: str = "workspace"

    # Metadata
    timestamp: float | None = None
    task_name: str | None = None
    context_id: str | None = None


class GitBranchDeleted(BaseModel):
    """A git branch was deleted."""

    model_config = ConfigDict(frozen=True)

    effect_type: Literal["git_branch_deleted"] = "git_branch_deleted"
    branch_name: str
    was_at_commit: str
    caused_by: str | None = None
    binding_name: str = "workspace"

    # Metadata
    timestamp: float | None = None
    task_name: str | None = None
    context_id: str | None = None


class GitPushPerformed(BaseModel):
    """A git push was performed (escapes containment).

    This effect has reversibility=NONE because pushes are irrevocable.
    """

    model_config = ConfigDict(frozen=True)

    effect_type: Literal["git_push_performed"] = "git_push_performed"
    ref_name: str
    remote: str
    old_sha: str | None = None
    new_sha: str
    forced: bool = False
    caused_by: str | None = None
    binding_name: str = "workspace"

    # Push is irreversible
    reversibility: ReversibilityLevel = ReversibilityLevel.NONE

    # Metadata
    timestamp: float | None = None
    task_name: str | None = None
    context_id: str | None = None


# =============================================================================
# Pending Git Operations (for WorkspaceRef state - Design Decision D5)
# =============================================================================


@dataclass(frozen=True)
class PendingGitOperation:
    """Base class for pending git operations.

    Design Decision D5: pending_git_operations is separate from pending_patches.
    Both are needed for materialization but have different lifecycles.
    """

    op_type: str
    caused_by: str | None = None


@dataclass(frozen=True)
class PendingBranchCreate(PendingGitOperation):
    """Pending branch creation."""

    op_type: str = "branch_create"
    branch_name: str = ""
    from_commit: str = ""


@dataclass(frozen=True)
class PendingCommit(PendingGitOperation):
    """Pending commit.

    Design Decision D6: Pending commits don't clear pending_patches.
    During materialization: apply patches first, then create commit.
    """

    op_type: str = "commit"
    original_sha: str = ""  # SHA from sandbox (will differ after replay)
    message: str = ""
    author: str = ""
    parent_shas: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingCheckout(PendingGitOperation):
    """Pending checkout."""

    op_type: str = "checkout"
    target_ref: str = ""
    is_branch: bool = True


@dataclass(frozen=True)
class PendingMerge(PendingGitOperation):
    """Pending merge."""

    op_type: str = "merge"
    source_ref: str = ""
    target_ref: str = ""
    fast_forward: bool = False


@dataclass(frozen=True)
class PendingBranchDelete(PendingGitOperation):
    """Pending branch deletion."""

    op_type: str = "branch_delete"
    branch_name: str = ""


@dataclass(frozen=True)
class PendingTagCreate(PendingGitOperation):
    """Pending tag creation."""

    op_type: str = "tag_create"
    tag_name: str = ""
    at_commit: str = ""
    is_annotated: bool = False
    message: str | None = None


# =============================================================================
# Materialization Context (Design Decision D12)
# =============================================================================


@dataclass
class MaterializationContext:
    """Context for tracking SHA translation during materialization.

    Design Decision D12: Maintain sha_map during materialization for parent
    SHA translation. Commit parent references must point to materialized SHAs,
    not original (which don't exist in the repo after replay).

    Design Decision D3: Accept that replayed commits have different SHAs.
    We preserve semantic equivalence (message, author, files), not
    byte-identical history.
    """

    sha_map: dict[str, str] = field(default_factory=dict)

    def translate_sha(self, original: str) -> str:
        """Translate original SHA to materialized SHA.

        Unknown SHAs pass through unchanged (for base commits that
        already exist in the repo).
        """
        return self.sha_map.get(original, original)

    def record_commit(self, original: str, materialized: str) -> None:
        """Record a SHA translation after creating a commit."""
        self.sha_map[original] = materialized

    def translate_parents(self, parents: tuple[str, ...]) -> tuple[str, ...]:
        """Translate multiple parent SHAs (for merge commits)."""
        return tuple(self.translate_sha(p) for p in parents)


# =============================================================================
# Serialization helpers (for WorkspaceState - validated by B2)
# =============================================================================


def pending_op_to_dict(op: PendingGitOperation) -> dict[str, Any]:
    """Serialize PendingGitOperation to dict for JSON."""
    if isinstance(op, PendingBranchCreate):
        return {
            "op_type": "branch_create",
            "branch_name": op.branch_name,
            "from_commit": op.from_commit,
            "caused_by": op.caused_by,
        }
    if isinstance(op, PendingCommit):
        return {
            "op_type": "commit",
            "original_sha": op.original_sha,
            "message": op.message,
            "author": op.author,
            "parent_shas": list(op.parent_shas),
            "caused_by": op.caused_by,
        }
    if isinstance(op, PendingCheckout):
        return {
            "op_type": "checkout",
            "target_ref": op.target_ref,
            "is_branch": op.is_branch,
            "caused_by": op.caused_by,
        }
    if isinstance(op, PendingMerge):
        return {
            "op_type": "merge",
            "source_ref": op.source_ref,
            "target_ref": op.target_ref,
            "fast_forward": op.fast_forward,
            "caused_by": op.caused_by,
        }
    if isinstance(op, PendingBranchDelete):
        return {
            "op_type": "branch_delete",
            "branch_name": op.branch_name,
            "caused_by": op.caused_by,
        }
    if isinstance(op, PendingTagCreate):
        return {
            "op_type": "tag_create",
            "tag_name": op.tag_name,
            "at_commit": op.at_commit,
            "is_annotated": op.is_annotated,
            "message": op.message,
            "caused_by": op.caused_by,
        }
    raise ValueError(f"Unknown PendingGitOperation type: {type(op)}")


def pending_op_from_dict(data: dict[str, Any]) -> PendingGitOperation:
    """Deserialize PendingGitOperation from dict."""
    op_type = data.get("op_type", "")
    caused_by = data.get("caused_by")

    if op_type == "branch_create":
        return PendingBranchCreate(
            branch_name=data.get("branch_name", ""),
            from_commit=data.get("from_commit", ""),
            caused_by=caused_by,
        )
    if op_type == "commit":
        return PendingCommit(
            original_sha=data.get("original_sha", ""),
            message=data.get("message", ""),
            author=data.get("author", ""),
            parent_shas=tuple(data.get("parent_shas", [])),
            caused_by=caused_by,
        )
    if op_type == "checkout":
        return PendingCheckout(
            target_ref=data.get("target_ref", ""),
            is_branch=data.get("is_branch", True),
            caused_by=caused_by,
        )
    if op_type == "merge":
        return PendingMerge(
            source_ref=data.get("source_ref", ""),
            target_ref=data.get("target_ref", ""),
            fast_forward=data.get("fast_forward", False),
            caused_by=caused_by,
        )
    if op_type == "branch_delete":
        return PendingBranchDelete(
            branch_name=data.get("branch_name", ""),
            caused_by=caused_by,
        )
    if op_type == "tag_create":
        return PendingTagCreate(
            tag_name=data.get("tag_name", ""),
            at_commit=data.get("at_commit", ""),
            is_annotated=data.get("is_annotated", False),
            message=data.get("message"),
            caused_by=caused_by,
        )
    raise ValueError(f"Unknown op_type: {op_type}")
