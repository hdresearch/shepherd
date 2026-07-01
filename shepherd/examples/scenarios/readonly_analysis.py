"""Read-only Codebase Analysis.

This scenario demonstrates using a read-only workspace for safe code analysis.
The workspace cannot modify any files, making it suitable for:
- Code review tasks
- Architecture analysis
- Security audits
- Documentation generation

Key concepts demonstrated:
1. WorkspaceRef.readonly() creates a read-only context
2. Context(WorkspaceRef) auto-resolves from scope
3. Capability modifiers for fine-grained control

Usage:
    uv run python shepherd/examples/scenarios/readonly_analysis.py
"""

from typing import Annotated

from shepherd import Context, Input, Output, WorkspaceRef, task
from pydantic import BaseModel, Field

# =============================================================================
# Task Definition (Modern API)
# =============================================================================


@task
class AnalyzeCodebase(BaseModel):
    """Analyze a codebase without making any changes.

    This task uses a read-only workspace to ensure safety.
    The agent can read files but cannot modify the codebase.
    """

    question: Annotated[
        Input(str),
        Field(description="What would you like to know about the codebase?"),
    ]
    workspace: Context(WorkspaceRef)  # Auto-resolved from scope (should be readonly)

    analysis: Annotated[
        Output(str),
        Field(description="Analysis findings"),
    ]
    files_examined: Annotated[
        Output(list[str]),
        Field(description="List of files that were examined"),
    ]


# =============================================================================
# API Demonstrations
# =============================================================================


def demonstrate_readonly_workspace():
    """Show how read-only workspaces work with the modern API."""
    print("=" * 60)
    print("Read-Only Workspace with Modern API")
    print("=" * 60)

    print("\n1. Bind a read-only workspace:")
    print("   import shepherd")
    print("   workspace = shepherd.bind('workspace', WorkspaceRef.readonly('/path/to/repo'))")
    print()
    print("   # Or with explicit capabilities:")
    print("   workspace = shepherd.bind('workspace', WorkspaceRef('/path', capabilities={'read'}))")

    print("\n2. Task automatically resolves workspace from scope:")
    print("   @task")
    print("   class AnalyzeCodebase(BaseModel):")
    print("       workspace: Context(WorkspaceRef)  # Auto-resolved!")
    print("       ...")
    print()
    print("   result = AnalyzeCodebase(question='What does main.py do?')")

    print("\n3. Capability properties:")
    print("   workspace.can_read  -> True")
    print("   workspace.can_write -> False")
    print("   workspace.can_bash  -> False")

    print("\n4. Allowed operations:")
    print("   - Read tool calls work normally")
    print("   - Glob/Grep for file discovery work")

    print("\n5. Blocked operations:")
    print("   - Write raises CapabilityError")
    print("   - Edit raises CapabilityError")
    print("   - Bash raises CapabilityError")


def demonstrate_capability_modifiers():
    """Show how to upgrade/downgrade capabilities."""
    print("\n" + "=" * 60)
    print("Capability Modifiers")
    print("=" * 60)

    print("\n1. Start with read-only:")
    print("   workspace = WorkspaceRef.readonly(path)")
    print("   # capabilities: {'read'}")

    print("\n2. Upgrade to writable:")
    print("   workspace = workspace.with_capabilities('write')")
    print("   # capabilities: {'read', 'write'}")

    print("\n3. Add bash capability:")
    print("   workspace = workspace.with_bash()")
    print("   # capabilities: {'read', 'write', 'bash'}")

    print("\n4. Downgrade - remove bash:")
    print("   workspace = workspace.without_bash()")
    print("   # capabilities: {'read', 'write'}")

    print("\n5. Downgrade - remove write:")
    print("   workspace = workspace.without_capabilities('write')")
    print("   # capabilities: {'read'}")


def demonstrate_use_cases():
    """Show practical use cases for capability restrictions."""
    print("\n" + "=" * 60)
    print("Practical Use Cases")
    print("=" * 60)

    print("\n1. CODE REVIEW (read-only):")
    print("   workspace = shepherd.bind('workspace', WorkspaceRef.readonly(repo_path))")
    print("   # Agent can read and analyze but cannot modify")
    print("   # Safe for automated PR reviews")

    print("\n2. CODE GENERATION (write, no bash):")
    print("   workspace = shepherd.bind('workspace', WorkspaceRef.writable(repo_path))")
    print("   # Agent can create/edit files")
    print("   # Cannot execute arbitrary commands")
    print("   # Safe for code generation tasks")

    print("\n3. BUILD/TEST (full access):")
    print("   workspace = shepherd.bind('workspace', WorkspaceRef.writable(repo_path).with_bash())")
    print("   # Agent has full access")
    print("   # Can run build commands, tests, etc.")
    print("   # Use with caution!")

    print("\n4. MULTI-PHASE WORKFLOW:")
    print("   # Phase 1: Analysis (readonly)")
    print("   shepherd.bind('workspace', WorkspaceRef.readonly(path))")
    print("   analysis = AnalyzeTask(question='What needs fixing?')")
    print()
    print("   # Phase 2: Modification (rebind as writable)")
    print("   shepherd.bind('workspace', WorkspaceRef.writable(path))")
    print("   modified = FixTask(instructions=analysis.fix_plan)")
    print()
    print("   # Phase 3: Verification (rebind with bash)")
    print("   shepherd.bind('workspace', WorkspaceRef.writable(path).with_bash())")
    print("   VerifyTask()")


# =============================================================================
# Main
# =============================================================================


if __name__ == "__main__":
    demonstrate_readonly_workspace()
    demonstrate_capability_modifiers()
    demonstrate_use_cases()

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print("""
Modern API patterns:
  - Use Context(WorkspaceRef) in task definitions
  - Bind workspace with shepherd.bind('workspace', WorkspaceRef.readonly(...))
  - ContextRef auto-updates as effects are applied
  - No need for workspace_out: Output(WorkspaceRef)

Capability modifiers provide fine-grained control over
what operations are allowed in a workspace context.
""")
