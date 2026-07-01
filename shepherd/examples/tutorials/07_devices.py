"""Example 07: Devices and Pipelines.

This tutorial demonstrates Device selection, Pipeline composition, error handling,
and production patterns. Tasks can execute locally or in isolated Podman containers,
with effects and session context flowing back to the host.

Key concepts:
1. Device("local") vs Device("container") - execution environment selection
2. Pipeline(...).retry().gate().run() - fluent task composition
3. result.rejected - unified rejection handling
4. SessionState tracks session_id and transcript_path for conversation continuity
5. TaskExecutionError with .effects for debugging failures
6. Pipeline.recover() for fallback values on error
7. Programmatic tasks (custom execute()) run on devices just like LLM tasks

Container session support:
- Session transcripts (~/.claude/) are accessible in containers via OverlayFS
- CWD path mismatch is handled transparently via symlinks
- Container execution forks sessions for isolation

Prerequisites:
- ANTHROPIC_API_KEY in environment or .env file
- For container mode: Podman installed (`podman machine start` on macOS)

Run with:
    uv run python shepherd/examples/tutorials/07_devices.py             # Local
    uv run python shepherd/examples/tutorials/07_devices.py --container # Container
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
    ClaudeProvider,
    Context,
    Device,
    Input,
    Output,
    Pipeline,
    SessionCreated,
    SessionForked,
    SessionState,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
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
class WriteCode(BaseModel):
    """Write code to implement a feature and save it to disk.

    You MUST use the Write tool to save the code to the specified filename.
    """

    feature: Annotated[Input(str), Field(description="Feature to implement")]
    filename: Annotated[Input(str), Field(description="Output filename")]
    workspace: Context(WorkspaceRef)
    code_written: Output(str) = Field(description="The code that was written to the file")


@task
class ReviewCode(BaseModel):
    """Review code for quality issues.

    First read the file using the Read tool, then review the code quality.
    """

    filename: Annotated[Input(str), Field(description="File to review")]
    workspace: Context(WorkspaceRef)
    is_approved: Output(bool) = Field(description="True if code passes review")
    feedback: Output(str) = Field(description="Review feedback explaining decision")


@task
class ExplainCode(BaseModel):
    """Explain what a piece of code does.

    Read the file and provide a clear explanation of its purpose and logic.
    """

    filename: Annotated[Input(str), Field(description="File to explain")]
    workspace: Context(WorkspaceRef)
    session: Context(SessionState)  # Maintains conversation context
    explanation: Output(str) = Field(description="Clear explanation of the code")


# =============================================================================
# When to Use What
# =============================================================================
# Direct instantiation:
#   result = WriteCode(feature="auth", filename="auth.py")
#   → Simple scripts, notebooks, no combinators needed
#   → Returns task instance with full type information
#
# Pipeline:
#   result = Pipeline(WriteCode).retry(3).gate(check).run(feature="auth", ...)
#   → When you need retry, gate, recover, or other combinators
#   → Returns PipelineResult with .rejected property
#
# Decision tree:
#   Need retry, gate, recover, or timeout? → Use Pipeline
#   Just running a task simply? → Direct instantiation
#
# Both work inside Device() context manager for container execution.

# =============================================================================
# Setup
# =============================================================================

device_name = "container" if args.container else "local"
print(f"\n{'=' * 60}\nExample 07: Container Execution ({device_name.upper()})\n{'=' * 60}")

# Setup workspace
workspace_path = Path(tempfile.mkdtemp(prefix="shepherd-tutorial-"))
create_git_workspace(workspace_path, {"README.md": "# My Project\n"})
atexit.register(lambda: cleanup_workspace(workspace_path))
print(f"Workspace: {workspace_path}")

# Bind workspace to global scope
workspace = shepherd.bind("workspace", WorkspaceRef.writable(str(workspace_path)))

# =============================================================================
# Example A: Simple Pipeline execution
# =============================================================================

print(f"\n{'-' * 50}\nA: Write code with retry\n{'-' * 50}")

with Device(device_name):
    result = Pipeline(WriteCode).run(feature="A function that calculates fibonacci numbers", filename="fibonacci.py")

    print(f"Code written: {result.code_written[:80]}...")

print(f"Workspace patches captured: {len(workspace.pending_patches)}")

# =============================================================================
# Example B: Gated execution
# =============================================================================

print(f"\n{'-' * 50}\nB: Code review with quality gate\n{'-' * 50}")

with Device(device_name):
    review = (
        Pipeline(ReviewCode)
        .gate(lambda r: r.is_approved)  # Only commit if approved
        .run(filename="fibonacci.py")
    )

    if review.rejected:
        print(f"Gate rejected: {review.reason}")
        print(f"Feedback: {review.feedback}")
    else:
        print(f"Approved: {review.is_approved}")
        print(f"Feedback: {review.feedback}")

# =============================================================================
# Example C: Session tracking
# =============================================================================

print(f"\n{'-' * 50}\nC: Session tracking across tasks\n{'-' * 50}")

session = shepherd.bind("session", SessionState())

print(f"Initial session: {session.session_id or '(new)'}")

with Device(device_name):
    explain = Pipeline(ExplainCode).run(filename="fibonacci.py")
    print(f"\nExplanation: {explain.explanation[:100]}...")

print(f"\nSession captured: {session.session_id[:12] if session.session_id else '(none)'}...")
if session.transcript_path:
    print(f"Transcript: {session.transcript_path}")

session_created = list(shepherd.effects.query(SessionCreated))
session_forked = list(shepherd.effects.query(SessionForked))
print(f"Session effects: {len(session_created)} created, {len(session_forked)} forked")

# =============================================================================
# Example D: Error handling patterns
# =============================================================================

print(f"\n{'-' * 50}\nD: Error handling patterns\n{'-' * 50}")

print("\nPattern 1: try/except with TaskExecutionError")
print("  (Simulated - no actual failure)")
print("""
    try:
        result = Pipeline(WriteCode).run(feature="auth", filename="auth.py")
    except TaskExecutionError as e:
        print(f"Task '{e.task_name}' failed during {e.phase} phase")
        print(f"Effects captured: {len(e.effects)}")

        # Query effects for debugging
        for tc in e.effects.query(ToolCallCompleted):
            print(f"  Tool: {tc.tool_name}")

        # Get detailed failure info from TaskFailed effect
        for failed in e.effects.query(TaskFailed):
            print(f"  Last tool: {failed.last_tool_name}")
            if failed.suggestions:
                print(f"  Suggestions: {', '.join(failed.suggestions)}")
""")

print("Pattern 2: Pipeline.retry() - automatic retry on transient failures")
print("  result = Pipeline(WriteCode).retry(max_attempts=3).run(...)")
print("  # Retries up to 3 times before failing")

print("\nPattern 3: Pipeline.recover() - provide fallback on failure")
print("  result = Pipeline(SearchTask).recover(lambda e: default_results).run(...)")
print("  # Returns default_results instead of raising")

print("\nPattern 4: Composing retry + recover")
print("  Pipeline(FlakyTask).retry(3).recover(lambda e: fallback).run(...)")
print("  # Try 3 times, then use fallback value")

print("\nEffect inspection (from successful execution):")
failed_tasks = list(shepherd.effects.query(TaskFailed))
if failed_tasks:
    for failed in failed_tasks:
        print(f"  Failed: {failed.task_name} at phase {failed.phase}")
        print(f"    Error: {failed.error}")
else:
    print("  No failed tasks in this run (all succeeded!)")

# =============================================================================
# Example E: Session continuity across chained tasks
# =============================================================================

print(f"\n{'-' * 50}\nE: Session continuity across tasks\n{'-' * 50}")


@task
class AnalyzeFile(BaseModel):
    """Analyze a file and describe what it does.

    Read the file and explain its purpose. Remember your analysis for follow-up.
    """

    filename: Annotated[Input(str), Field(description="File to analyze")]
    workspace: Context(WorkspaceRef)
    session: Context(SessionState)  # Establishes session for continuity
    analysis: Output(str) = Field(description="Analysis of the file")


@task
class SuggestImprovement(BaseModel):
    """Suggest an improvement to the file you just analyzed.

    Based on your previous analysis, suggest one concrete improvement.
    Do NOT use any tools — just respond with your suggestion.
    """

    workspace: Context(WorkspaceRef)
    session: Context(SessionState)  # Continues from analysis session
    suggestion: Output(str) = Field(description="Concrete improvement suggestion")


# Task 1: Analyze the file (creates a session)
with Device(device_name):
    analysis = Pipeline(AnalyzeFile).run(filename="fibonacci.py")
    print(f"Analysis: {analysis.analysis[:120]}...")
    print(f"Session established: {analysis.session.session_id[:12] if analysis.session.session_id else '(none)'}...")

# Task 2: Suggest improvement (forks from the analysis session)
# This works because the session transcript is linked across sandbox CWDs
with Device(device_name):
    improvement = Pipeline(SuggestImprovement).run()
    print(f"Suggestion: {improvement.suggestion[:120]}...")
    print(f"Session forked: {improvement.session.session_id[:12] if improvement.session.session_id else '(none)'}...")

session_forked = list(shepherd.effects.query(SessionForked))
print(f"Session forked effects: {len(session_forked)} (confirms conversation continuity)")

# =============================================================================
# Example F: Programmatic tasks on devices
# =============================================================================
# Tasks with a custom execute() method (no LLM needed) can also run inside
# Device() blocks. The framework serializes the task's source code, inputs,
# and context bindings into a TaskSpec, ships them to the container, reconstructs
# and executes the task there, then returns the serialized outputs.
#
# This means you can mix programmatic and LLM tasks freely in the same Device()
# block — both get the same lifecycle effects (TaskStarted, TaskCompleted),
# sandbox isolation, and effect capture.
# =============================================================================

print(f"\n{'-' * 50}\nF: Programmatic tasks on devices\n{'-' * 50}")


@task
class CountLines(BaseModel):
    """Count lines, words, and characters in a file.

    This task has a custom execute() — no LLM round-trip needed.
    It reads the file from the workspace and computes statistics.
    """

    filename: Annotated[Input(str), Field(description="File to analyze")]
    workspace: Context(WorkspaceRef)
    line_count: Output(int) = Field(description="Number of lines")
    word_count: Output(int) = Field(description="Number of words")
    char_count: Output(int) = Field(description="Number of characters")

    def execute(self) -> None:
        file_path = Path(self.workspace.path) / self.filename
        content = file_path.read_text()
        self.line_count = len(content.splitlines())
        self.word_count = len(content.split())
        self.char_count = len(content)


# Snapshot effect counts before the programmatic task
effects_before = len(shepherd.effects)
started_before = len(list(shepherd.effects.query(TaskStarted)))
completed_before = len(list(shepherd.effects.query(TaskCompleted)))

# Run the programmatic task inside a Device block — same as LLM tasks
with Device(device_name):
    stats = CountLines(filename="fibonacci.py")

print("  File: fibonacci.py")
print(f"  Lines: {stats.line_count}")
print(f"  Words: {stats.word_count}")
print(f"  Chars: {stats.char_count}")

# Verify that lifecycle effects were emitted — programmatic tasks
# go through the same lifecycle pipeline as LLM tasks
started_after = len(list(shepherd.effects.query(TaskStarted)))
completed_after = len(list(shepherd.effects.query(TaskCompleted)))

print("\n  Lifecycle effects:")
print(f"    TaskStarted emitted:   {started_after > started_before}")
print(f"    TaskCompleted emitted: {completed_after > completed_before}")
print(f"    New effects from this task: {len(shepherd.effects) - effects_before}")

# Programmatic tasks also compose with Pipeline combinators on devices
print("\n  Pipeline composition with programmatic task:")
with Device(device_name):
    pipeline_stats = Pipeline(CountLines).run(filename="fibonacci.py")
    print(f"    Pipeline result — lines: {pipeline_stats.line_count}, words: {pipeline_stats.word_count}")


# A mixed workflow: LLM task writes code, programmatic task analyzes it
@task
class AnalyzeComplexity(BaseModel):
    """Analyze code complexity by counting functions and classes.

    Programmatic task — scans source code with string matching.
    """

    filename: Annotated[Input(str), Field(description="File to analyze")]
    workspace: Context(WorkspaceRef)
    function_count: Output(int) = Field(description="Number of function definitions")
    class_count: Output(int) = Field(description="Number of class definitions")
    total_lines: Output(int) = Field(description="Total lines of code")

    def execute(self) -> None:
        file_path = Path(self.workspace.path) / self.filename
        content = file_path.read_text()
        lines = content.splitlines()
        self.function_count = sum(1 for line in lines if line.strip().startswith("def "))
        self.class_count = sum(1 for line in lines if line.strip().startswith("class "))
        self.total_lines = len(lines)


print("\n  Mixed workflow: LLM writes code, then programmatic task analyzes it")
with Device(device_name):
    # Step 1: LLM writes a new file
    write_result = Pipeline(WriteCode).run(
        feature="A simple stack data structure with push, pop, peek, and is_empty methods",
        filename="stack.py",
    )
    print(f"    LLM wrote stack.py: {write_result.code_written[:60]}...")

    # Step 2: Programmatic task analyzes the file the LLM just wrote
    # This works because both tasks share the same Device block (overlay stacking)
    complexity = AnalyzeComplexity(filename="stack.py")
    print(
        f"    Programmatic analysis — functions: {complexity.function_count}, "
        f"classes: {complexity.class_count}, lines: {complexity.total_lines}"
    )

# =============================================================================
# Summary
# =============================================================================

print(f"\n{'=' * 60}\nSummary\n{'=' * 60}")
print(f"Workspace patches: {len(workspace.pending_patches)}")
for i, patch in enumerate(workspace.pending_patches, 1):
    print_patch_preview(patch, i)

print(f"\nEffects: {len(shepherd.effects)}")
print(f"Tool calls: {len(list(shepherd.effects.query(ToolCallCompleted)))}")
print(f"File patches: {len(list(shepherd.effects.query(WorkspacePatchCaptured)))}")
print(f"Session created: {len(list(shepherd.effects.query(SessionCreated)))}")
print(f"Session forked: {len(list(shepherd.effects.query(SessionForked)))}")

print("\nKey takeaways:")
print(f"  1. Device('{device_name}') selects execution environment")
print("  2. Pipeline().retry().gate().run() composes tasks fluently")
print("  3. result.rejected provides unified rejection handling")
print("  4. SessionState tracks session_id and transcript_path for conversation continuity")
print("  5. TaskExecutionError captures effects for debugging on failure")
print("  6. Pipeline.recover() provides fallback values instead of raising")
print("  7. Programmatic tasks (custom execute()) run on devices with the same lifecycle")

# =============================================================================
# Debugging Tips
# =============================================================================
# If something goes wrong:
#   print(shepherd.debug_summary())  # Execution timeline
#   try/except TaskExecutionError to inspect e.effects
#   Query effects: TaskFailed, ToolCallCompleted
#
# See Tutorial 06 and shepherd/docs/guides/debugging.md for comprehensive troubleshooting.
