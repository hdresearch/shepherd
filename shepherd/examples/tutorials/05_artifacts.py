"""Example 05: Artifacts - File-Based Outputs.

Artifacts are files written by the LLM that are automatically collected and
injected into task fields after execution. This is the "write to file, read back"
pattern.

Key concepts:
- Artifact(str, filename="...") - Raw text file (markdown, code, etc.)
- Artifact(dict, filename="...") - JSON file parsed to dict
- Artifact(list, filename="...") - JSON file parsed to list
- required=False for optional artifacts

This example demonstrates:
1. Basic text artifacts (markdown output)
2. JSON artifacts with automatic parsing
3. Optional artifacts that may or may not be created
4. Inspecting artifact effects in the stream

Run with:
    uv run python shepherd/examples/tutorials/05_artifacts.py
"""

import atexit
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

# Add repository root to path for imports
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import shepherd
from shepherd import (
    Artifact,
    ArtifactMissing,
    ArtifactWritten,
    ClaudeProvider,
    Context,
    Input,
    Output,
    VerboseConfig,
    WorkspaceRef,
    scope,
    task,
)
from pydantic import BaseModel, Field

# =============================================================================
# Tasks with Artifacts
# =============================================================================


@task
class GenerateDesignDoc(BaseModel):
    """Generate a design document for a software component.

    Write the design document to .artifacts/design.md using the Write tool.
    The document should include an overview, key decisions, and implementation notes.
    """

    component_name: Input(str)
    requirements: Annotated[Input(str), Field(description="What the component should do")]
    workspace: Context(WorkspaceRef)

    # Artifact: LLM writes to .artifacts/design.md, content read back here
    design_doc: Artifact(str, filename="design.md", required=False)

    # Regular output extracted from response
    summary: Output(str)


@task
class GenerateConfig(BaseModel):
    """Generate a configuration file for an application.

    Write the configuration to .artifacts/config.json using the Write tool.
    The JSON should include all necessary settings based on the requirements.

    Also write .artifacts/features.json as a JSON array of feature flag names
    that should be enabled for this environment (e.g., ["analytics", "caching"]).
    """

    app_name: Input(str)
    environment: Annotated[Input(str), Field(description="e.g., development, staging, production")]
    workspace: Context(WorkspaceRef)

    # JSON artifact: automatically parsed to dict
    config: Artifact(dict, filename="config.json", required=False)

    # List artifact: automatically parsed to list (must be JSON array)
    features: Artifact(list, filename="features.json", required=False)

    # Output field to ensure structured response
    summary: Output(str)


@task
class AnalyzeCode(BaseModel):
    """Analyze code and optionally generate a report if issues are found.

    Always write analysis.json with findings.
    Only write recommendations.md if there are significant issues to address.
    """

    code: Input(str)
    workspace: Context(WorkspaceRef)

    # JSON artifact parsed to dict
    analysis: Artifact(dict, filename="analysis.json", required=False)

    # Optional artifact - no error if missing
    recommendations: Artifact(
        str,
        filename="recommendations.md",
        required=False,
        description="Only created if significant issues found",
    )

    has_issues: Output(bool)


# =============================================================================
# Configuration
# =============================================================================

shepherd.configure(
    provider=ClaudeProvider(
        name="artifact-demo",
        model="claude-sonnet-4-20250514",
        default_permission_mode="acceptEdits",
        max_turns=5,
        verbose=VerboseConfig(enabled=True),
    )
)

# =============================================================================
# Setup: Create temporary workspace with artifacts directory
# =============================================================================

print("=== Artifacts Tutorial ===\n")

# Create temp workspace
workspace_path = Path(tempfile.mkdtemp(prefix="shepherd-artifacts-"))
artifacts_path = workspace_path / ".artifacts"
artifacts_path.mkdir()

# Initialize as git repo (required for WorkspaceRef)
subprocess.run(["git", "init"], check=False, cwd=workspace_path, capture_output=True)
subprocess.run(
    ["git", "config", "user.email", "test@example.com"], check=False, cwd=workspace_path, capture_output=True
)
subprocess.run(["git", "config", "user.name", "Test"], check=False, cwd=workspace_path, capture_output=True)
(workspace_path / ".gitkeep").touch()
subprocess.run(["git", "add", "."], check=False, cwd=workspace_path, capture_output=True)
subprocess.run(["git", "commit", "-m", "init"], check=False, cwd=workspace_path, capture_output=True)


def cleanup():
    """Remove the temporary workspace directory."""
    shutil.rmtree(workspace_path, ignore_errors=True)


atexit.register(cleanup)

print(f"Created temp workspace: {workspace_path}")
print(f"Artifacts directory: {artifacts_path}\n")

# =============================================================================
# Example 1: Text Artifact (Markdown)
# =============================================================================

print("=" * 60)
print("=== Example 1: Text Artifact (Markdown) ===")
print("=" * 60)

# Bind workspace using fluent syntax
workspace = WorkspaceRef.writable(str(workspace_path)).bind(scope)

result1 = GenerateDesignDoc(
    component_name="UserAuthService",
    requirements="Handle user login, logout, and session management with JWT tokens",
)

print(f"\nSummary: {result1.summary}")
print(f"\nDesign doc artifact collected: {result1.design_doc is not None}")
if result1.design_doc:
    preview = result1.design_doc[:200] + "..." if len(result1.design_doc) > 200 else result1.design_doc
    print(f"Preview:\n{preview}")

# =============================================================================
# Example 2: JSON Artifacts (dict and list)
# =============================================================================

print("\n" + "=" * 60)
print("=== Example 2: JSON Artifacts (dict and list) ===")
print("=" * 60)

result2 = GenerateConfig(
    app_name="MyWebApp",
    environment="production",
)

print(f"\nConfig artifact: {type(result2.config).__name__}")
if result2.config:
    if isinstance(result2.config, dict):
        print(f"  Keys: {list(result2.config.keys())}")
        print(f"  Preview: {json.dumps(result2.config, indent=2)[:300]}...")
    else:
        print(f"  Content preview: {str(result2.config)[:200]}...")

print(f"\nFeatures artifact (list): {type(result2.features).__name__}")
if result2.features:
    if isinstance(result2.features, list):
        print(f"  Count: {len(result2.features)} features")
        print(f"  Items: {result2.features[:3]}{'...' if len(result2.features) > 3 else ''}")
    else:
        print(f"  Note: Expected list, got {type(result2.features).__name__}")
        print(f"  Content: {result2.features}")

# =============================================================================
# Example 3: Optional Artifacts
# =============================================================================

print("\n" + "=" * 60)
print("=== Example 3: Optional Artifacts ===")
print("=" * 60)

# Analyze some simple code (likely no major issues)
simple_code = '''
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b
'''

result3 = AnalyzeCode(code=simple_code)

print(f"\nAnalysis (required): {result3.analysis is not None}")
if result3.analysis:
    print(f"  Findings: {result3.analysis}")

print(f"Recommendations (optional): {result3.recommendations is not None}")
print(f"Has issues: {result3.has_issues}")

if result3.recommendations:
    print(f"Recommendations preview: {result3.recommendations[:200]}...")
else:
    print("No recommendations artifact created (code was clean)")

# =============================================================================
# Inspecting Artifact Effects
# =============================================================================

print("\n" + "=" * 60)
print("=== Artifact Effects in Stream ===")
print("=" * 60)

# Query for artifact effects
written = list(scope.effects.query(ArtifactWritten))
missing = list(scope.effects.query(ArtifactMissing))

print(f"\nArtifactWritten effects: {len(written)}")
for effect in written:
    print(f"  - {effect.filename} ({effect.content_type}, {effect.size_bytes} bytes)")
    print(f"    Field: {effect.field_name}, Path: {effect.path}")

print(f"\nArtifactMissing effects: {len(missing)}")
for effect in missing:
    status = "required (would have raised)" if effect.required else "optional (OK)"
    print(f"  - {effect.filename} ({status})")
    print(f"    Field: {effect.field_name}")

# =============================================================================
# Summary
# =============================================================================

print("\n" + "=" * 60)
print("=== Summary ===")
print("=" * 60)

print("""
Artifacts provide a "write to file, read back" pattern:

1. Define artifact fields with Artifact(type, filename="...")
2. LLM writes to .artifacts/{filename} using the Write tool
3. After execution, content is read and injected into the field
4. Type controls parsing:
   - str: raw text content
   - dict: JSON parsed to dictionary
   - list: JSON parsed to list

Key options:
- required=True (default): raises ArtifactNotFoundError if missing
- required=False: field is None if artifact wasn't created

Effects for observability:
- ArtifactWritten: artifact was successfully collected
- ArtifactMissing: artifact was not found (with required flag)
""")

print(f"Total effects captured: {len(scope.effects)}")

# =============================================================================
# Debugging Tips
# =============================================================================
# If something goes wrong:
#   print(shepherd.debug_summary())  # Check execution timeline
#   Query effects: ArtifactWritten, ArtifactMissing
#   Verify LLM wrote to .artifacts/{filename}
#
# See Tutorial 06 and shepherd/docs/guides/debugging.md for comprehensive troubleshooting.
