"""Example 07a: Device Stacking and Fork/Discard.

This tutorial demonstrates advanced Device patterns: sequential task stacking
within a single Device block, fork/discard for speculative execution, and
effect inspection via WorkspacePatchCaptured.

Key concepts:
1. Sequential stacking — multiple tasks in one Device() block share filesystem state
2. Fork/discard — create a branch, run risky work, inspect, then discard if needed
3. Effect inspection — query WorkspacePatchCaptured to see exactly what changed

Prerequisites:
- ANTHROPIC_API_KEY in environment or .env file
- For container mode: Podman installed (`podman machine start` on macOS)

Run with:
    uv run python shepherd/examples/tutorials/07a_device_stacking.py             # Local
    uv run python shepherd/examples/tutorials/07a_device_stacking.py --container # Container
"""

import argparse
import atexit
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Add repository root to path for imports
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# Load environment
load_dotenv(_repo_root / ".env")
load_dotenv()

# Verify API key
if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("ERROR: ANTHROPIC_API_KEY not set. Create a .env file with your key.")

# Parse args
parser = argparse.ArgumentParser()
parser.add_argument("--container", action="store_true", help="Use container execution")
args = parser.parse_args()

# Check Podman if needed
if args.container:
    try:
        if subprocess.run(["podman", "version"], check=False, capture_output=True).returncode != 0:
            raise FileNotFoundError
    except FileNotFoundError:
        sys.exit("ERROR: --container requires Podman. Try: podman machine start")

# =============================================================================
# Imports and Configuration
# =============================================================================

import shepherd
from shepherd import (
    Agent,
    ClaudeProvider,
    Context,
    Device,
    Input,
    Output,
    Pipeline,
    ToolCallCompleted,
    VerboseConfig,
    WorkspacePatchCaptured,
    WorkspaceRef,
    task,
)

from examples.utils import cleanup_workspace, create_git_workspace, print_patch_preview

shepherd.configure(
    provider=ClaudeProvider(
        name="coder",
        model="claude-haiku-4-5",
        max_turns=15,
        verbose=VerboseConfig(enabled=True),
    )
)


# =============================================================================
# Tasks
# =============================================================================


@task
class WriteUtils(BaseModel):
    """Write a Python utils.py file with utility functions.

    You MUST use the Write tool to save the code to a file named utils.py
    in the workspace. Include at least two useful utility functions
    (e.g., a string formatter and a list deduplicator).
    """

    description: Annotated[Input(str), Field(description="What utilities to include")]
    workspace: Context(WorkspaceRef)
    code_written: Output(str) = Field(description="The code that was written to utils.py")


@task
class WriteMain(BaseModel):
    """Write a Python main.py file that imports from utils.py.

    First read utils.py to see what functions are available, then write
    a main.py that imports and uses those functions. You MUST use the
    Read tool to read utils.py first, then the Write tool to save main.py.
    """

    requirement: Annotated[Input(str), Field(description="What main.py should do")]
    workspace: Context(WorkspaceRef)
    code_written: Output(str) = Field(description="The code that was written to main.py")


# =============================================================================
# Setup
# =============================================================================

device_name = "container" if args.container else "local"
print(f"\n{'=' * 60}\nExample 07a: Device Stacking and Fork/Discard ({device_name.upper()})\n{'=' * 60}")

# Setup workspace
workspace_path = Path(tempfile.mkdtemp(prefix="shepherd-tutorial-"))
create_git_workspace(workspace_path, {"README.md": "# Stacking Demo\n"})
atexit.register(lambda: cleanup_workspace(workspace_path))
print(f"Workspace: {workspace_path}")

# Bind workspace to global scope
workspace = shepherd.bind("workspace", WorkspaceRef.writable(str(workspace_path)))

# =============================================================================
# Section 1: Sequential Stacking
# =============================================================================
# Two tasks run inside a single Device() block. Task A writes utils.py,
# and Task B reads it and writes main.py that imports from it. Because
# they share the same Device block, Task B can see Task A's filesystem
# changes — this is "overlay stacking".
# =============================================================================

print(f"\n{'=' * 60}\nSection 1: Sequential Stacking\n{'=' * 60}")
print("Task A writes utils.py, Task B reads it and writes main.py.")
print("Both run in the same Device() block so B sees A's changes.\n")

with Device(device_name):
    # -------------------------------------------------------------------------
    # Task A: Write utils.py
    # -------------------------------------------------------------------------

    print(f"\n{'-' * 50}\nTask A: WriteUtils — creating utils.py\n{'-' * 50}")

    result_a = Pipeline(WriteUtils).run(
        description="A format_name(first, last) function that returns 'Last, First' "
        "and a deduplicate(items) function that removes duplicates preserving order"
    )

    print(f"Utils written: {result_a.code_written[:100]}...")
    print(f"Patches so far: {len(workspace.pending_patches)}")

    # -------------------------------------------------------------------------
    # Task B: Write main.py that imports from utils.py
    # -------------------------------------------------------------------------

    print(f"\n{'-' * 50}\nTask B: WriteMain — creating main.py (imports from utils.py)\n{'-' * 50}")

    result_b = Pipeline(WriteMain).run(
        requirement="Import format_name and deduplicate from utils, "
        "demonstrate both functions with example data, and print the results"
    )

    print(f"Main written: {result_b.code_written[:100]}...")
    print(f"Patches after both tasks: {len(workspace.pending_patches)}")

# =============================================================================
# Section 2: Effect Inspection
# =============================================================================
# Query WorkspacePatchCaptured effects to see exactly what files changed
# and preview the diffs.
# =============================================================================

print(f"\n{'=' * 60}\nSection 2: Effect Inspection — WorkspacePatchCaptured\n{'=' * 60}")

patches = list(shepherd.effects.query(WorkspacePatchCaptured))
print(f"Total WorkspacePatchCaptured effects: {len(patches)}")

for i, patch_effect in enumerate(patches, 1):
    print(f"\n  Patch {i}:")
    print(f"    Source: {patch_effect.patch.source_step or 'unknown'}")
    print(f"    Files:  {patch_effect.files_changed}")
    print(f"    Size:   {patch_effect.patch_size_bytes} bytes")
    # Preview first few lines of the diff
    diff_text = patch_effect.patch.patch
    diff_lines = diff_text.split("\n")
    preview_count = min(8, len(diff_lines))
    print(f"    Preview ({preview_count} lines):")
    for line in diff_lines[:preview_count]:
        print(f"      {line}")
    if len(diff_lines) > preview_count:
        print(f"      ... ({len(diff_lines) - preview_count} more lines)")

tool_calls = list(shepherd.effects.query(ToolCallCompleted))
print(f"\nTotal tool calls across all tasks: {len(tool_calls)}")
for tc in tool_calls:
    print(f"  - {tc.tool_name}")

# =============================================================================
# Section 3: Fork/Discard for Revert
# =============================================================================
# Fork creates an independent branch of execution via Agent.fork(). Run a
# risky task in the branch, inspect its effects, then discard if the result
# is undesirable. The parent's effect stream is unaffected by discard.
#
# Note: Agent uses its own scope (separate from the global Pipeline scope).
# Agent.run() takes free-form instructions and uses LiteLLM for execution.
# =============================================================================

print(f"\n{'=' * 60}\nSection 3: Fork/Discard — Speculative Execution\n{'=' * 60}")
print("Agent.fork() creates an independent branch. We run a risky")
print("refactor in the branch, then discard it.\n")

# Create an Agent with its own scope and bind the same workspace
agent = Agent(model="claude-haiku-4-5")
agent.bind("workspace", WorkspaceRef.writable(str(workspace_path)))

# Run a safe task first to establish baseline effects
print(f"{'-' * 50}\nAgent baseline: writing helpers.py\n{'-' * 50}")
baseline = agent.run(
    f"Use the write_file tool to create {workspace_path}/helpers.py with "
    f"this exact content:\ndef greet(name):\n    return f'Hello, {{name}}!'\n"
)
print(f"Baseline success: {baseline.success}")
parent_effects_before = len(list(agent._scope.effects))
print(f"Parent effects after baseline: {parent_effects_before}")

# Fork: create an independent branch
print(f"\n{'-' * 50}\nForking agent...\n{'-' * 50}")
branch = agent.fork()

# Run risky task in the branch
print("Running risky refactor in branch...")
risky = branch.run(
    f"Use the write_file tool to overwrite {workspace_path}/helpers.py with "
    f"completely different content:\ndef broken():\n    raise Exception('oops')\n"
)
print(f"Branch success: {risky.success}")
print(f"Branch effects: {len(list(branch._scope.effects))}")
print(f"helpers.py on disk: {(workspace_path / 'helpers.py').read_text().strip()[:60]}...")

# Discard the branch — parent effects are untouched
print(f"\n{'-' * 50}\nDiscarding branch...\n{'-' * 50}")
agent.discard(branch)
parent_effects_after = len(list(agent._scope.effects))
print(f"Parent effects after discard: {parent_effects_after} (was {parent_effects_before})")
print(f"Effect stream unchanged: {parent_effects_before == parent_effects_after}")
print()
print("NOTE: discard() reverts the effect stream, not the filesystem.")
print("For filesystem isolation, use Device('container') which provides")
print("OverlayFS-based copy-on-write — discard truly reverts all changes.")

# =============================================================================
# Summary
# =============================================================================

print(f"\n{'=' * 60}\nSummary\n{'=' * 60}")
print(f"Workspace patches: {len(workspace.pending_patches)}")
for i, patch in enumerate(workspace.pending_patches, 1):
    print_patch_preview(patch, i)

all_patches = list(shepherd.effects.query(WorkspacePatchCaptured))
print(f"\nTotal WorkspacePatchCaptured effects: {len(all_patches)}")
print(f"Total effects: {len(shepherd.effects)}")
print(f"Total tool calls: {len(list(shepherd.effects.query(ToolCallCompleted)))}")

print("\nKey takeaways:")
print(f"  1. Sequential tasks in one Device('{device_name}') block share filesystem state")
print("  2. Task B can read files written by Task A (overlay stacking)")
print("  3. WorkspacePatchCaptured effects show exactly what changed per task")
print("  4. Agent.fork() creates an independent branch for speculative work")
print("  5. Agent.discard(branch) reverts — parent state is unaffected")
print("  6. Agent.merge(branch) adopts — branch effects flow to parent")
