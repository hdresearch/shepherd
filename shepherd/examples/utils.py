"""Utilities for shepherd example scripts.

Provides:
- Temporary workspace helpers (temp_git_workspace, create_git_workspace)
- Fixture generation (generate_scenario_workspace)
- Display helpers (print_header, print_workspace_summary, etc.)
- Verification utilities (verify_patch_count, verify_files_modified, etc.)
- Example runner utilities (workspace_example, run_example)

For simple examples, use temp_git_workspace():
    with temp_git_workspace({"main.py": "print('hello')"}) as path:
        workspace = WorkspaceRef.writable(str(path))
        ...

For realistic scenarios, use generate_scenario_workspace():
    workspace_path = generate_scenario_workspace("rich-cli/fix_bug")
    workspace = WorkspaceRef.from_path(workspace_path)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Generator

if TYPE_CHECKING:
    from shepherd_contexts import DiffPatch
    from shepherd_contexts.session import SessionState

    from shepherd import Stream, WorkspaceRef


# =============================================================================
# Temporary Workspace Helpers
# =============================================================================


@contextmanager
def temp_git_workspace(
    initial_files: dict[str, str] | None = None,
    prefix: str = "shepherd-workspace-",
) -> Generator[Path, None, None]:
    """Create a temporary git-initialized workspace.

    Creates a temp directory with:
    - Git initialized
    - Git user configured (for commits)
    - Initial commit with README.md
    - Optional additional files

    The workspace is automatically cleaned up when the context manager exits.

    Usage from the `shepherd/` project root:
        cd shepherd
        uv run python

        from examples.utils import temp_git_workspace
        from shepherd import WorkspaceRef

        with temp_git_workspace({"hello.py": "print('hi')"}) as path:
            workspace = WorkspaceRef.writable(str(path))
            # ... use workspace ...
        # Cleanup happens automatically

    Args:
        initial_files: Dict of {filename: content} to create before initial commit.
            Supports nested paths like "src/main.py".
        prefix: Prefix for the temporary directory name.

    Yields:
        Path to the temporary workspace directory.
    """
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        path = Path(tmp)

        # Initialize git
        subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "example@shepherd.dev"],
            cwd=path,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Shepherd Example"],
            cwd=path,
            capture_output=True,
            check=True,
        )

        # Create README
        (path / "README.md").write_text("# Example Workspace\n\nCreated by shepherd.\n")

        # Create additional initial files
        if initial_files:
            for filename, content in initial_files.items():
                filepath = path / filename
                filepath.parent.mkdir(parents=True, exist_ok=True)
                filepath.write_text(content)

        # Initial commit
        subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=path,
            capture_output=True,
            check=True,
        )

        yield path


def create_git_workspace(
    target_dir: Path | str,
    initial_files: dict[str, str] | None = None,
) -> Path:
    """Create a git-initialized workspace at a specific location.

    Unlike temp_git_workspace, this creates a persistent workspace
    that is NOT automatically cleaned up.

    Args:
        target_dir: Directory to create the workspace in.
        initial_files: Dict of {filename: content} to create.

    Returns:
        Path to the created workspace.

    Raises:
        FileExistsError: If target_dir already exists and is not empty.
    """
    path = Path(target_dir)

    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"Directory not empty: {path}")

    path.mkdir(parents=True, exist_ok=True)

    # Initialize git
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "example@shepherd.dev"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Shepherd Example"],
        cwd=path,
        capture_output=True,
        check=True,
    )

    # Create README
    (path / "README.md").write_text("# Workspace\n")

    # Create additional files
    if initial_files:
        for filename, content in initial_files.items():
            filepath = path / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(content)

    # Initial commit
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=path,
        capture_output=True,
        check=True,
    )

    return path


def cleanup_workspace(workspace_path: Path) -> None:
    """Remove a generated workspace directory."""
    if workspace_path.exists():
        shutil.rmtree(workspace_path)


def require_gitpython(example_name: str) -> bool:
    """Return False after printing a clear setup message when GitPython is missing."""
    from shepherd_runtime.context.sandbox import GITPYTHON_AVAILABLE

    if GITPYTHON_AVAILABLE:
        return True

    print(
        f"{example_name} requires GitPython for git-backed workspace sandboxes.\n"
        "Install it with `pip install gitpython` or `uv sync --all-packages`.",
        file=sys.stderr,
    )
    return False


def print_example_outcome(
    status: Literal["demonstrated", "partial", "not_demonstrated"],
    summary: str,
    checks: list[tuple[str, bool, str]],
) -> None:
    """Print a compact result block for example scripts."""
    print_section("Outcome")

    headings = {
        "demonstrated": "[PASS] Intended workflow demonstrated",
        "partial": "[WARN] Intended workflow partially demonstrated",
        "not_demonstrated": "[WARN] Provider completed without demonstrating the intended workflow",
    }
    print(headings[status])
    print(summary)

    for label, ok, detail in checks:
        marker = "[PASS]" if ok else "[WARN]"
        print(f"{marker} {label}: {detail}")


# =============================================================================
# Display Utilities
# =============================================================================


def print_header(title: str, char: str = "=") -> None:
    """Print a formatted header."""
    print(char * 60)
    print(title)
    print(char * 60)


def print_section(title: str, char: str = "-") -> None:
    """Print a section divider."""
    print(f"\n{char * 60}")
    print(title)
    print(char * 60)


def print_session_summary(session: SessionState | None, label: str = "Session") -> None:
    """Display session state summary."""
    if session is None:
        print(f"\n{label}: None")
        return

    print(f"\n{label}:")
    print(f"  Session ID: {session.session_id[:8]}...")
    print(f"  Has transcript: {session.has_transcript}")
    if session.transcript_path:
        print(f"  Transcript path: {session.transcript_path}")


def print_workspace_summary(ws: WorkspaceRef | None, label: str = "Workspace") -> None:
    """Display workspace state summary."""
    if ws is None:
        print(f"\n{label}: None")
        return

    print(f"\n{label}:")
    print(f"  Path: {ws.path}")
    print(f"  Base commit: {ws.base_commit[:8]}")
    print(f"  Pending patches: {len(ws.pending_patches)}")

    if ws.pending_patches:
        total_files = set()
        for patch in ws.pending_patches:
            total_files.update(patch.files_changed)
        print(f"  Total files changed: {len(total_files)}")


def print_patch_details(patch: DiffPatch, index: int, preview_lines: int = 10) -> None:
    """Display single patch details with content preview."""
    print(f"\n### Patch {index}")
    print(f"  Source step: {patch.source_step or 'unknown'}")
    print(f"  Files changed: {', '.join(patch.files_changed)}")
    print(f"  Patch size: {len(patch.patch)} bytes")
    if patch.sha256:
        print(f"  SHA256: {patch.sha256[:16]}...")

    print(f"\n  Content preview ({preview_lines} lines):")
    print("  " + "-" * 40)

    lines = patch.patch.split("\n")
    for line in lines[:preview_lines]:
        print(f"  {line}")
    if len(lines) > preview_lines:
        print(f"  ... ({len(lines) - preview_lines} more lines)")


def print_stream_summary(stream: Stream, label: str = "Effect Stream") -> None:
    """Display effect stream summary."""
    print(f"\n{label} ({len(stream)} effects):")

    # Group by effect type
    type_counts: dict[str, int] = {}
    for layer in stream:
        effect_type = type(layer.effect).__name__
        type_counts[effect_type] = type_counts.get(effect_type, 0) + 1

    for effect_type, count in sorted(type_counts.items()):
        print(f"  {effect_type}: {count}")


def print_effect_details(stream: Stream, max_effects: int = 20) -> None:
    """Print details of effects in the stream."""
    print(f"\nEffect details (first {max_effects}):")
    for layer in list(stream)[:max_effects]:
        effect = layer.effect
        effect_type = type(effect).__name__

        # Format based on effect type
        if effect_type == "FileRead":
            print(f"  [{layer.sequence}] {effect_type}: {effect.path}")
        elif effect_type == "FileCreate":
            print(f"  [{layer.sequence}] {effect_type}: {effect.path} ({len(effect.content)} chars)")
        elif effect_type == "FilePatch":
            print(f"  [{layer.sequence}] {effect_type}: {effect.path}")
        elif effect_type == "BashCommand":
            cmd_preview = effect.command[:50] + "..." if len(effect.command) > 50 else effect.command
            print(f"  [{layer.sequence}] {effect_type}: {cmd_preview}")
        elif effect_type == "AgentThinking":
            preview = effect.content[:60] + "..." if len(effect.content) > 60 else effect.content
            print(f"  [{layer.sequence}] {effect_type}: {preview}")
        elif effect_type == "ToolCallStarted":
            print(f"  [{layer.sequence}] {effect_type}: {effect.tool_name}")
        else:
            print(f"  [{layer.sequence}] {effect_type}")

    if len(stream) > max_effects:
        print(f"  ... ({len(stream) - max_effects} more effects)")


# =============================================================================
# Verification Utilities
# =============================================================================


def verify_patch_count(ws: WorkspaceRef | None, expected: int) -> bool:
    """Assert patch count matches expected."""
    if ws is None:
        actual = 0
    else:
        actual = len(ws.pending_patches)

    if actual == expected:
        print(f"\n[PASS] Patch count: {actual} == {expected}")
        return True
    print(f"\n[FAIL] Patch count: {actual} != {expected}")
    return False


def verify_files_modified(stream: Stream, expected_files: list[str]) -> bool:
    """Verify that expected files were modified during execution."""
    from shepherd import FileCreate, FilePatch

    modified_files: set[str] = set()
    for layer in stream:
        effect = layer.effect
        if isinstance(effect, (FileCreate, FilePatch)):
            path = getattr(effect, "path", None)
            if path:
                modified_files.add(path)

    expected_set = set(expected_files)
    missing = expected_set - modified_files

    if not missing:
        print(f"\n[PASS] All expected files modified: {expected_files}")
        return True
    print(f"\n[FAIL] Missing files: {missing}")
    print(f"       Modified: {modified_files}")
    return False


def verify_effect_count(stream: Stream, effect_type: str, min_count: int = 1) -> bool:
    """Verify that at least min_count effects of a type exist."""
    count = sum(1 for layer in stream if type(layer.effect).__name__ == effect_type)

    if count >= min_count:
        print(f"\n[PASS] {effect_type}: {count} >= {min_count}")
        return True
    print(f"\n[FAIL] {effect_type}: {count} < {min_count}")
    return False


def verify_session_changed(session1: SessionState | None, session2: SessionState | None) -> bool:
    """Verify that two sessions have different IDs (session was forked)."""
    if session1 is None or session2 is None:
        print("\n[FAIL] One or both sessions are None")
        return False

    if session1.session_id != session2.session_id:
        print(f"\n[PASS] Sessions forked: {session1.session_id[:8]}... -> {session2.session_id[:8]}...")
        return True
    print("\n[FAIL] Sessions not forked (same ID)")
    return False


def print_all_patches(ws: WorkspaceRef | None, preview_lines: int = 10) -> None:
    """Display all patches in a workspace."""
    if ws is None or not ws.pending_patches:
        print("\n## Patches: None")
        return

    print(f"\n## All Patches ({len(ws.pending_patches)} total)")
    for i, patch in enumerate(ws.pending_patches, 1):
        print_patch_details(patch, i, preview_lines)


def print_context_lifecycle(stream: Stream, label: str = "Context Lifecycle") -> None:
    """Print context lifecycle effects (ContextPrepared/ContextCaptured) from a stream."""
    from shepherd import ContextCaptured, ContextPrepared

    print(f"\n## {label}:")
    found_any = False
    for layer in stream:
        if isinstance(layer.effect, ContextPrepared):
            found_any = True
            print(f"  [{layer.sequence}] ContextPrepared: {layer.effect.context_type}")
            print(f"       field: {layer.effect.context_field}")
            print(f"       summary: {layer.effect.context_summary}")
        elif isinstance(layer.effect, ContextCaptured):
            found_any = True
            print(f"  [{layer.sequence}] ContextCaptured: {layer.effect.context_type}")
            print(f"       field: {layer.effect.context_field}")
            print(f"       changes: {layer.effect.changes_summary}")

    if not found_any:
        print("  (no context effects)")


# Aliases for compatibility with tutorials
print_workspace_state = print_workspace_summary
print_patch_preview = print_patch_details
print_effect_summary = print_stream_summary


# =============================================================================
# Eval Fixture Generation
# =============================================================================

# Path to the eval directory (relative to the Shepherd project root)
# This file is at: shepherd/examples/utils.py
# Go up 1 level to reach shepherd/, then into eval/
EVAL_DIR = Path(__file__).parent.parent / "eval"

# Backwards compatibility alias
FIXTURES_DIR = EVAL_DIR


def generate_scenario_workspace(
    scenario: str,
    target_dir: Path | str | None = None,
    force: bool = True,
) -> Path:
    """Generate a fresh workspace from an evaluation scenario.

    Uses the shepherd/eval/generate.py CLI to create a reproducible test workspace.

    Args:
        scenario: Scenario identifier (e.g., "rich-cli/code_review").
        target_dir: Where to create the workspace. If None, uses a temp directory.
        force: If True, overwrite existing workspace.

    Returns:
        Path to the generated workspace.

    Raises:
        FileNotFoundError: If eval directory not found.
        subprocess.CalledProcessError: If generation fails.

    Example:
        >>> workspace = generate_scenario_workspace("rich-cli/code_review")
        >>> workspace_ref = WorkspaceRef.from_path(workspace, branch="review/add-quiet-mode")
    """
    if not EVAL_DIR.exists():
        raise FileNotFoundError(
            f"Eval directory not found at {EVAL_DIR}. Make sure you're running from the repository root."
        )

    if target_dir is None:
        target_dir = Path(tempfile.mkdtemp(prefix="shepherd-eval-"))
    else:
        target_dir = Path(target_dir)

    # Build command
    cmd = [
        "python",
        str(EVAL_DIR / "generate.py"),
        "scenario",
        scenario,
        str(target_dir),
    ]
    if force:
        cmd.append("--force")

    # Run generator
    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        cwd=EVAL_DIR.parent,  # Run from repo root
    )

    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            cmd,
            output=result.stdout,
            stderr=result.stderr,
        )

    return target_dir


# =============================================================================
# Example Runner Utilities
# =============================================================================


@contextmanager
def workspace_example(
    title: str,
    scenario: str = "rich-cli/code_review",
    branch: str = "review/add-quiet-mode",
    model: str = "claude-sonnet-4-20250514",
    permission_mode: str = "acceptEdits",
    timeout_seconds: float = 180.0,
    mock: bool = False,
) -> Generator[WorkspaceRef, None, None]:
    """Context manager for workspace-based examples.

    Handles the complete lifecycle:
    1. Print header
    2. Generate workspace from fixture
    3. Create WorkspaceRef
    4. Configure provider
    5. Yield workspace for example code
    6. Cleanup workspace in finally block

    Usage:
        with workspace_example("My Example") as workspace:
            result = MyTask(workspace=workspace, ...)
            print(result.output)

    Args:
        title: Header title for the example
        scenario: Fixture scenario (default: "rich-cli/code_review")
        branch: Git branch to checkout (default: "review/add-quiet-mode")
        model: Claude model to use
        permission_mode: SDK permission mode
        timeout_seconds: Execution timeout
        mock: If True, use mock provider (no API calls)

    Yields:
        WorkspaceRef configured for the example
    """
    import shepherd
    from shepherd import ClaudeProvider, WorkspaceRef

    print_header(title)

    # Generate workspace
    print("\nGenerating fresh workspace from fixture...")
    try:
        workspace_path = generate_scenario_workspace(scenario)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise
    except Exception as e:
        print(f"Error generating workspace: {e}", file=sys.stderr)
        raise

    print(f"Workspace: {workspace_path}")

    try:
        # Setup workspace ref
        workspace_ref = WorkspaceRef.from_path(workspace_path, branch=branch)
        print(f"WorkspaceRef: {workspace_ref!r}")

        # Configure provider
        print_section("Configuration")
        provider = ClaudeProvider(
            name="example",
            model=model,
            default_permission_mode=permission_mode,
        )
        shepherd.configure(provider=provider)

        print(f"Model: {provider.model}")
        print(f"Permission mode: {provider.default_permission_mode}")
        if mock:
            print("Mode: MOCK (no API calls)")

        yield workspace_ref

    finally:
        print_section("Cleanup")
        print(f"Removing workspace: {workspace_path}")
        cleanup_workspace(workspace_path)
        shepherd.reset()  # Clean up global scope
        print("Done.")


def run_example(main_func) -> int:
    """Wrapper to run an example's main function with proper exit handling.

    Usage:
        if __name__ == "__main__":
            run_example(main)

    Or for examples that don't return an int:
        if __name__ == "__main__":
            run_example(lambda: (main(), 0)[1])
    """
    try:
        return main_func()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1


__all__ = [
    "EVAL_DIR",
    "FIXTURES_DIR",
    "cleanup_workspace",
    "create_git_workspace",
    "generate_scenario_workspace",
    "print_all_patches",
    "print_context_lifecycle",
    "print_effect_details",
    "print_effect_summary",
    "print_example_outcome",
    "print_header",
    "print_patch_details",
    "print_patch_preview",
    "print_section",
    "print_session_summary",
    "print_stream_summary",
    "print_workspace_state",
    "print_workspace_summary",
    "require_gitpython",
    "run_example",
    "temp_git_workspace",
    "verify_effect_count",
    "verify_files_modified",
    "verify_patch_count",
    "verify_session_changed",
    "workspace_example",
]
