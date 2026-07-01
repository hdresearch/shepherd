"""Fix a bug in a codebase.

This scenario is intended to demonstrate:
1. WorkspaceRef as Context (auto-updating via ContextRef)
2. Structured outputs for analysis
3. Effect stream inspection

Usage:
    uv run python shepherd/examples/scenarios/fix_bug.py
"""

from __future__ import annotations

import atexit
import sys
from pathlib import Path
from typing import Annotated

# Add Shepherd project root to path for example helper imports
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from examples.utils import (
    cleanup_workspace,
    generate_scenario_workspace,
    print_example_outcome,
    print_header,
    print_patch_preview,
    print_section,
    require_gitpython,
)
from pydantic import BaseModel, Field

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
    task,
)

# =============================================================================
# Task Definition
# =============================================================================


@task(guidance="You are a senior software engineer. Make minimal, focused fixes.")
class FixBug(BaseModel):
    """Fix a bug in a codebase based on the bug description.

    Analyze the bug, identify the root cause, and apply the fix.
    Make minimal changes - only fix what's necessary.
    """

    bug_description: Annotated[
        Input(str),
        Field(description="Description of the bug, including reproduction steps and hints"),
    ]
    workspace: Context(WorkspaceRef)  # Auto-resolved from scope

    analysis: Annotated[
        Output(str),
        Field(description="Analysis of the bug and how it was fixed"),
    ]
    files_modified: Annotated[
        Output(list[str]),
        Field(description="List of files that were modified to fix the bug"),
    ]
    fix_applied: Annotated[
        Output(bool),
        Field(description="Whether the fix was successfully applied"),
    ]


# =============================================================================
# Main
# =============================================================================


def main() -> int:
    """Run the bug fix and show workspace changes."""
    print_header("Shepherd Framework - FixBug Scenario")

    if not require_gitpython("fix_bug.py"):
        return 1

    # Generate workspace from fixture
    print("\nGenerating workspace from fixture...")
    workspace_path = generate_scenario_workspace("rich-cli/fix_bug")
    atexit.register(lambda: cleanup_workspace(workspace_path))
    print(f"Workspace: {workspace_path}")

    # Configure provider
    shepherd.configure(
        provider=ClaudeProvider(
            name="default",
            model="claude-sonnet-4-20250514",
            default_permission_mode="acceptEdits",
            verbose=VerboseConfig(enabled=True),
        )
    )

    # Bind workspace to scope - returns auto-updating ContextRef
    workspace = shepherd.bind(
        "workspace",
        WorkspaceRef.from_path(workspace_path, branch="bugfix/unicode-handling"),
    )

    # Run the fix
    print_section("Running FixBug")
    print("Target: Fix unicode file output encoding")

    result = FixBug(
        bug_description=(
            "When rich-cli writes output to a file via the --output option, "
            "unicode characters can be corrupted or trigger encoding errors on "
            "systems that do not default to UTF-8. Look in src/rich_cli/__main__.py "
            "for the file output handling and make the minimal fix so output files "
            "are written with UTF-8 encoding."
        ),
    )

    analysis = result.analysis
    files_modified = result.files_modified or []
    fix_applied = result.fix_applied

    # Print results
    print_section("Results")
    print(f"\nAnalysis:\n{analysis or '(no analysis returned)'}")
    print(f"\nFix Applied: {fix_applied if fix_applied is not None else 'unknown'}")
    print(f"Files Modified: {files_modified}")

    # Show workspace state (ContextRef auto-updates!)
    print_section("Workspace State")
    print(f"Path: {workspace.path}")
    print(f"Pending patches: {len(workspace.pending_patches)}")

    if workspace.pending_patches:
        print("\nPatch details:")
        for i, patch in enumerate(workspace.pending_patches, 1):
            print_patch_preview(patch, i)

    # Show effect stream
    print_section("Effect Stream")
    print(f"Total effects: {len(shepherd.effects)}")

    tool_calls = list(shepherd.effects.query(ToolCallCompleted))
    patches = list(shepherd.effects.query(WorkspacePatchCaptured))

    print(f"Tool calls: {len(tool_calls)}")
    for tc in tool_calls:
        status = "success" if tc.effect.success else "failed"
        print(f"  - {tc.effect.tool_name}: {status}")

    print(f"Patches captured: {len(patches)}")

    fix_demonstrated = bool(patches) or (bool(tool_calls) and (fix_applied is True or bool(files_modified)))
    has_structured_outputs = bool(analysis) or bool(files_modified) or fix_applied is not None

    print_example_outcome(
        "demonstrated" if fix_demonstrated else "not_demonstrated",
        (
            "The provider completed and produced edit evidence for the fix workflow."
            if fix_demonstrated
            else "The provider completed, but this run did not demonstrate an applied fix."
        ),
        [
            ("Structured outputs", has_structured_outputs, "returned" if has_structured_outputs else "none returned"),
            ("Files modified", bool(files_modified), f"{len(files_modified)} reported"),
            ("Workspace patches", bool(patches), f"{len(patches)} captured"),
            ("Tool calls", bool(tool_calls), f"{len(tool_calls)} recorded"),
        ],
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
