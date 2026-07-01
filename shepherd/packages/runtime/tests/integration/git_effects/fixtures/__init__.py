"""Test fixtures for git effects validation.

These fixtures provide prototype implementations that match the design spec.
Production implementations should pass the same tests.

Contents:
- Git effect types (GitBranchCreated, etc.)
- Pending operation types (PendingBranchCreate, etc.)
- GitStateReader for direct .git reading
- LocalSimulatedDevice for container-free testing
- Materialization types (MaterializationContext, MaterializationAttempt, etc.)
"""

from .effects import (
    GitBranchCreated,
    GitBranchDeleted,
    GitCheckoutPerformed,
    GitCommitCreated,
    GitMergePerformed,
    GitPushPerformed,
    GitTagCreated,
    MaterializationContext,
    PendingBranchCreate,
    PendingBranchDelete,
    PendingCheckout,
    PendingCommit,
    PendingGitOperation,
    PendingMerge,
    PendingTagCreate,
    ReversibilityLevel,
    pending_op_from_dict,
    pending_op_to_dict,
)
from .git_state_reader import GitStateReader, GitStateSnapshot
from .mock_device import (
    LocalSimulatedDevice,
    MaterializationAttempt,
    PartialMaterializationResult,
)

__all__ = [
    # Effects
    "GitBranchCreated",
    "GitBranchDeleted",
    "GitCheckoutPerformed",
    "GitCommitCreated",
    "GitMergePerformed",
    "GitPushPerformed",
    # State reading
    "GitStateReader",
    "GitStateSnapshot",
    "GitTagCreated",
    # Devices
    "LocalSimulatedDevice",
    "MaterializationAttempt",
    # Materialization
    "MaterializationContext",
    "PartialMaterializationResult",
    "PendingBranchCreate",
    "PendingBranchDelete",
    "PendingCheckout",
    "PendingCommit",
    # Pending operations
    "PendingGitOperation",
    "PendingMerge",
    "PendingTagCreate",
    "ReversibilityLevel",
    "pending_op_from_dict",
    # Serialization
    "pending_op_to_dict",
]
