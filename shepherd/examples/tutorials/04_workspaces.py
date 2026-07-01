"""Example 04: Using WorkspaceRef for Code Changes.

WorkspaceRef provides git-backed workspace management:
- Tracks file changes as patches
- Supports rollback via git
- Enforces capability-based access control (read/write/bash)

This example demonstrates:
1. Basic file operations (create, modify) with automatic patch capture
2. Nested scope semantics for workspace isolation:
   - Inherited binding: changes persist to parent scope
   - Shadowed binding: changes are isolated, lost when child exits

This pattern enables safe "what-if" exploration without affecting the main workspace.

Run with:
    uv run python shepherd/examples/tutorials/04_workspaces.py
"""

import atexit
import sys
import tempfile
from pathlib import Path

# Add repository root to path for imports
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import shepherd
from shepherd import (
    ClaudeProvider,
    Context,
    Input,
    Output,
    ToolCallCompleted,
    VerboseConfig,
    WorkspacePatchCaptured,
    WorkspaceRef,
    scope,
    task,
)
from pydantic import BaseModel

# Import shared utilities
from examples.utils import cleanup_workspace, create_git_workspace, print_patch_preview, print_workspace_state

# =============================================================================
# Tasks
# =============================================================================


@task
class CreateFile(BaseModel):
    """Create a new file with the specified content."""

    filename: Input(str)
    content: Input(str)
    workspace: Context(WorkspaceRef)
    success: Output(bool)


@task
class ModifyFile(BaseModel):
    """Modify an existing file based on instructions."""

    filename: Input(str)
    instructions: Input(str)
    workspace: Context(WorkspaceRef)
    changes_made: Output(str)


# =============================================================================
# Configuration
# =============================================================================

shepherd.configure(
    provider=ClaudeProvider(
        name="coder",
        model="claude-sonnet-4-20250514",
        default_permission_mode="default",
        max_turns=10,
        verbose=VerboseConfig(enabled=True),
    )
)

# =============================================================================
# Setup: Create temporary git workspace
# =============================================================================

print("=== Workspace Example ===\n")

workspace_path = Path(tempfile.mkdtemp(prefix="shepherd-tutorial-"))
create_git_workspace(workspace_path, {"src/placeholder.py": "# placeholder\n"})
atexit.register(lambda: cleanup_workspace(workspace_path))

print(f"Created temp workspace: {workspace_path}\n")

# =============================================================================
# Example: Sequential file operations
# =============================================================================

# Create a writable workspace and bind to scope using fluent syntax
# WorkspaceRef has __binding_name__ = "workspace", so no explicit name needed
# The returned ContextRef always resolves to the current workspace state
workspace = WorkspaceRef.writable(str(workspace_path)).bind(scope)

# Create a file
print("--- Step 1: Create a file ---")
result1 = CreateFile(
    filename="hello.py",
    content="def greet(name):\n    return f'Hello, {name}!'\n",
)
print(f"File created: {result1.success}")

# ContextRef automatically reflects updates - no need to re-fetch
print(f"Pending patches: {len(workspace.pending_patches)}")

# Modify the file
print("\n--- Step 2: Modify the file ---")
result2 = ModifyFile(
    filename="hello.py",
    instructions="Add a docstring to the greet function",
)
print(f"Changes: {result2.changes_made}")

# =============================================================================
# Inspect results
# =============================================================================

# Final workspace state (ContextRef auto-updates)
print("\n--- Final State ---")
print_workspace_state(workspace)

# Check effects
print("\n--- Effects ---")
print(f"Total effects: {len(scope.effects)}")

# Show tool calls (what the agent did)
tool_calls = list(scope.effects.query(ToolCallCompleted))
print(f"Tool calls: {len(tool_calls)}")
for tc in tool_calls:
    print(f"  - {tc.tool_name}: {'success' if tc.success else 'failed'}")

# Show workspace patches (domain changes from context capture)
patches = list(scope.effects.query(WorkspacePatchCaptured))
print(f"Workspace patches captured: {len(patches)}")

# Show patch details
if workspace.pending_patches:
    print("\n--- Patch Details ---")
    for i, patch in enumerate(workspace.pending_patches, 1):
        print_patch_preview(patch, i)

print(f"\nGlobal effects (from all examples): {len(shepherd.effects)}")

# =============================================================================
# Example: Nested Scopes and Workspace Isolation
# =============================================================================

# Workspaces follow lexical scoping rules:
# - Inherited binding: changes persist to parent scope
# - Shadowed binding: changes are isolated, lost when child scope exits
#
# This is useful for "what-if" exploration without affecting the main workspace.

print("\n" + "=" * 60)
print("=== Nested Scope Example: Inherited vs Shadowed Bindings ===")
print("=" * 60)

# Current state: workspace has patches from steps 1 and 2
# (ContextRef already reflects current state)
patches_before = len(workspace.pending_patches)
print(f"\nWorkspace patches before nested scopes: {patches_before}")

# --- Example A: Inherited binding (changes persist) ---
print("\n--- Example A: Inherited Binding ---")
print("Child scope inherits workspace from parent. Changes will persist.")

with scope.new() as child_a:
    # No bind() call - child inherits "workspace" from parent

    result = ModifyFile(
        filename="hello.py",
        instructions="Add a type hint to the name parameter",
    )
    print(f"  Child A made changes: {result.changes_made}")

# After child exits, check parent's workspace (ContextRef auto-updates)
patches_after_a = len(workspace.pending_patches)
print(f"  Patches after child A: {patches_after_a} (was {patches_before})")
print(f"  Changes persisted: {patches_after_a > patches_before}")

# --- Example B: Shadowed binding (changes isolated) ---
print("\n--- Example B: Shadowed Binding ---")
print("Child scope shadows workspace with fresh copy. Changes will be lost.")

# Current workspace state (ContextRef already current)
patches_before_b = len(workspace.pending_patches)

with scope.new() as child_b:
    # Shadow the binding with same workspace path but fresh state
    # This creates isolation - changes won't affect parent
    child_workspace = WorkspaceRef.writable(str(workspace_path)).bind(child_b)

    result = ModifyFile(
        filename="hello.py",
        instructions="Add an emoji to the greeting (just for fun)",
    )
    print(f"  Child B made changes: {result.changes_made}")

    # Check patches in child scope (child_workspace ContextRef auto-updates)
    print(f"  Patches in child B: {len(child_workspace.pending_patches)}")

# After child exits, parent's workspace should be UNCHANGED
# (ContextRef still points to parent scope's binding)
patches_after_b = len(workspace.pending_patches)
print(f"  Patches after child B: {patches_after_b} (was {patches_before_b})")
print(f"  Changes isolated (not in parent): {patches_after_b == patches_before_b}")

# --- Summary ---
print("\n--- Summary ---")
print(f"Final workspace patches: {len(workspace.pending_patches)}")
print("- Inherited binding (Example A): changes persisted to parent")
print("- Shadowed binding (Example B): changes discarded when child exited")
print("\nThis pattern enables safe 'what-if' exploration without side effects.")

# Note: Workspace cleanup happens automatically via atexit

# =============================================================================
# Debugging Tips
# =============================================================================
# If something goes wrong:
#   print(shepherd.debug_summary())  # Shows tool calls and file operations
#   workspace.pending_patches       # Check captured patches
#   Query effects: WorkspacePatchCaptured, ToolCallCompleted
#
# See Tutorial 06 and shepherd/docs/guides/debugging.md for comprehensive troubleshooting.
