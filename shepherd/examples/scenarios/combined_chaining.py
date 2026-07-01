"""Task chaining with session and workspace continuity.

This scenario is intended to demonstrate chaining tasks using the modern Context API:
- Session continuity (conversation context flows between tasks)
- Workspace continuity (file changes accumulate)
- ContextRef auto-updating (no manual threading of state)

Chain: AnalyzeBug -> FixBug
- AnalyzeBug: Analyzes the bug (read-only), establishes context
- FixBug: Applies the fix, building on the analysis session

Usage:
    uv run python shepherd/examples/scenarios/combined_chaining.py
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
    SessionState,
    ToolCallCompleted,
    VerboseConfig,
    WorkspacePatchCaptured,
    WorkspaceRef,
    task,
)

# =============================================================================
# Task Definitions (Modern API)
# =============================================================================


@task(guidance="You are a senior software engineer analyzing bugs.")
class AnalyzeBug(BaseModel):
    """Analyze a bug based on its description.

    Given a bug description, analyze the codebase to understand the root cause
    and identify the files that need to be modified.
    """

    bug_description: Annotated[
        Input(str),
        Field(description="Description of the bug, including reproduction steps"),
    ]
    workspace: Context(WorkspaceRef)  # Auto-resolved, auto-updates
    session: Context(SessionState)  # Conversation context for continuity

    root_cause: Annotated[
        Output(str),
        Field(description="Analysis of the root cause of the bug"),
    ]
    affected_files: Annotated[
        Output(list[str]),
        Field(description="List of file paths that need to be modified"),
    ]
    proposed_fix: Annotated[
        Output(str),
        Field(description="High-level description of the proposed fix"),
    ]


@task(guidance="You are a senior software engineer. Make minimal, focused fixes.")
class FixBug(BaseModel):
    """Fix a bug in a codebase based on the bug description."""

    bug_description: Annotated[
        Input(str),
        Field(description="Description of the bug or fix instruction"),
    ]
    workspace: Context(WorkspaceRef)  # Same workspace, accumulated patches
    session: Context(SessionState)  # Continues from analysis session

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
    """Run the combined chaining example."""
    print_header("Shepherd Framework - Task Chaining (Session + Workspace)")

    if not require_gitpython("combined_chaining.py"):
        return 1

    # Generate workspace from fixture
    print("\nGenerating workspace from fixture...")
    workspace_path = generate_scenario_workspace("rich-cli/code_review")
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

    # Bind contexts - ContextRefs auto-update as tasks run
    workspace = shepherd.bind(
        "workspace",
        WorkspaceRef.from_path(workspace_path, branch="review/add-quiet-mode"),
    )
    session = shepherd.bind("session", SessionState())

    print("\nInitial state:")
    print(f"  Workspace patches: {len(workspace.pending_patches)}")
    print(f"  Session ID: {session.session_id or '(new)'}")

    # Step 1: AnalyzeBug
    print_section("Step 1: AnalyzeBug")
    print("Analyzing: Hardcoded exit code issue")

    result1 = AnalyzeBug(
        bug_description=(
            "In src/rich_cli/__main__.py, the main() function returns a hardcoded "
            "exit code of 42 instead of 0 for success. Analyze this bug, identify "
            "the root cause, affected files, and propose a fix."
        ),
    )

    root_cause = result1.root_cause
    affected_files = result1.affected_files or []
    proposed_fix = result1.proposed_fix

    print(f"\nRoot cause: {(root_cause or '(no root cause returned)')[:200]}...")
    print(f"Affected files: {affected_files}")
    print(f"Proposed fix: {(proposed_fix or '(no proposed fix returned)')[:200]}...")

    # ContextRefs auto-updated!
    session_after_step1 = session.session_id
    print("\nAfter Step 1:")
    print(f"  Session ID: {session_after_step1[:12] if session_after_step1 else '(none)'}...")
    print(f"  Workspace patches: {len(workspace.pending_patches)}")

    # Step 2: FixBug (session continues automatically)
    print_section("Step 2: FixBug")
    print("Applying fix based on previous analysis (session continues)...")

    result2 = FixBug(
        bug_description=(
            "Apply the fix for the exit code bug that you just analyzed. "
            "You identified the root cause in the previous step - now implement "
            "the fix you proposed."
        ),
    )

    analysis = result2.analysis
    files_modified = result2.files_modified or []
    fix_applied = result2.fix_applied

    print(f"\nAnalysis: {(analysis or '(no analysis returned)')[:200]}...")
    print(f"Files modified: {files_modified}")
    print(f"Fix applied: {fix_applied if fix_applied is not None else 'unknown'}")

    # Final state (ContextRefs reflect all changes)
    print("\nAfter Step 2:")
    print(f"  Session ID: {session.session_id[:12] if session.session_id else '(none)'}...")
    print(f"  Workspace patches: {len(workspace.pending_patches)}")

    # Effect streams
    print_section("Effect Streams")
    print(f"Step 1 effects: {len(result1.effects)}")
    print(f"Step 2 effects: {len(result2.effects)}")
    print(f"Total effects: {len(shepherd.effects)}")

    tool_calls = list(shepherd.effects.query(ToolCallCompleted))
    patches = list(shepherd.effects.query(WorkspacePatchCaptured))
    print(f"Tool calls (total): {len(tool_calls)}")
    print(f"Patches captured (total): {len(patches)}")

    analysis_demonstrated = bool(root_cause) or bool(affected_files) or bool(proposed_fix)
    fix_demonstrated = bool(patches) or (bool(tool_calls) and (fix_applied is True or bool(files_modified)))
    session_continuity = bool(session_after_step1 and session.session_id and session_after_step1 == session.session_id)

    if workspace.pending_patches:
        print_section("Patches")
        for i, patch in enumerate(workspace.pending_patches, 1):
            print_patch_preview(patch, i)

    if session_continuity and fix_demonstrated:
        outcome = "demonstrated"
        summary = "The chaining example demonstrated both session continuity and a follow-on fix."
    elif session_continuity:
        outcome = "partial"
        summary = "The chaining example demonstrated continuity, but this run did not produce an applied fix."
    else:
        outcome = "not_demonstrated"
        summary = "The provider completed, but this run did not clearly demonstrate cross-task continuity."

    print_example_outcome(
        outcome,
        summary,
        [
            (
                "Step 1 analysis outputs",
                analysis_demonstrated,
                "returned" if analysis_demonstrated else "none returned",
            ),
            (
                "Session continuity",
                session_continuity,
                (
                    f"maintained as {session.session_id[:12]}..."
                    if session_continuity
                    else (
                        "session initialized late"
                        if session.session_id
                        else "no shared session established"
                    )
                ),
            ),
            ("Fix outputs", fix_demonstrated, "returned" if fix_demonstrated else "none returned"),
            ("Workspace patches", bool(workspace.pending_patches), f"{len(workspace.pending_patches)} accumulated"),
        ],
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
