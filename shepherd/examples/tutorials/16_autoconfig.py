"""Example 16: Autoconfig — LLM-Driven Config Inference.

Teach your pipeline to configure itself from workspace analysis.

Key concepts:
1. Infer(T) marks fields for automatic inference
2. Rich Field descriptions are the inference specification
3. resolve_config() handles the full lifecycle: cached YAML -> inferred -> defaults
4. Non-Infer fields are mechanically excluded — never sent to the LLM
5. Persisted configs are human-editable YAML; re-infer with force=True

Run with:
    uv run python shepherd/examples/tutorials/16_autoconfig.py
"""

import shepherd
from shepherd import ClaudeProvider, Infer, resolve_config
from shepherd.autoconfig import config_name
from pydantic import BaseModel, Field

# =============================================================================
# Step 1: Define a config class with Infer-annotated fields
# =============================================================================

# Infer(T) marks a field for LLM inference — consistent with Input(T) / Output(T).
# The Field description tells the LLM *how to derive* the value.


class ProjectConfig(BaseModel):
    """Configuration for a project analysis pipeline."""

    # Inferable fields — descriptions carry derivation rules
    language: Infer(str) = Field(
        default="unknown",
        description=(
            "Primary programming language. Determine from pyproject.toml, "
            "package.json, Cargo.toml, go.mod, or file extensions in src/."
        ),
    )
    test_command: Infer(str) = Field(
        default="",
        description=(
            "Test execution command. Extract the exact command from CI config "
            "(.github/workflows/*.yml, Makefile). If no test command found, "
            "leave empty."
        ),
    )
    description: Infer(str) = Field(
        default="",
        description=(
            "One-sentence project description. Synthesize from README.md "
            "or pyproject.toml [project].description. Keep under 100 chars."
        ),
    )

    # Non-inferable fields — NOT wrapped in Infer(), so they never appear
    # in the inference schema. The LLM can't populate them, and they won't
    # show up in the persisted YAML either.
    api_key: str | None = None
    debug: bool = False


# =============================================================================
# Step 2: Configure and run
# =============================================================================

# Use Haiku for fast, cheap inference
shepherd.configure(provider=ClaudeProvider(name="autoconfig", model="claude-haiku-4-5"))

# resolve_config does the heavy lifting:
#   1. Check .shepherd/project.yaml for a cached config
#   2. If not found, run LLM inference on the workspace
#   3. If inference fails (no provider, no context), fall back to defaults
#
# On first run, it infers from the workspace and persists to YAML.
# On subsequent runs, it loads the cached YAML (fast, no LLM call).
config = resolve_config(ProjectConfig, persist=False)

print(f"Language:     {config.language}")
print(f"Test command: {config.test_command or '(none detected)'}")
print(f"Description:  {config.description or '(none)'}")
print(f"API key:      {config.api_key}  (not inferred — excluded from schema)")
print(f"Debug:        {config.debug}  (not inferred — kept at default)")


# =============================================================================
# Step 3: Override specific fields while inferring the rest
# =============================================================================

# Pass a partial config — fields you set explicitly always win.
partial = ProjectConfig(language="Python")  # override language
config2 = resolve_config(ProjectConfig, partial, persist=False)

print("\nWith override:")
print(f"  Language:     {config2.language}  (explicit override)")
print(f"  Test command: {config2.test_command}  (inferred or default)")


# =============================================================================
# Step 4: Persistence and invalidation
# =============================================================================

# Config name is derived from the class: ProjectConfig -> "project"
print(f"\nConfig file: .shepherd/{config_name(ProjectConfig)}.yaml")

# To manually persist (resolve_config does this automatically):
#   persist_config(config)
#
# Only Infer() fields appear in the YAML — api_key and debug are excluded.
# The YAML is human-editable: change values and they'll be used on next run.
#
# To re-infer (invalidate the cache):
#   config = resolve_config(ProjectConfig, force=True)
#
# Or just delete the YAML file:
#   rm .shepherd/project.yaml


# =============================================================================
# Step 5: Cross-cutting guidance with __infer_guidance__
# =============================================================================

# Most config classes don't need this — rich descriptions on each field
# are usually sufficient. Use __infer_guidance__ only for cross-cutting
# concerns that span multiple fields.


class MonorepoConfig(BaseModel):
    """Configuration for a monorepo with multiple packages."""

    __infer_guidance__ = """\
    This config describes an entire monorepo. If you detect per-package
    CI configurations, aggregate them rather than picking one package.
    """

    packages: Infer(list[str]) = Field(
        default_factory=list,
        description="List of package directories (e.g., ['packages/core', 'packages/cli']).",
    )
    shared_test_command: Infer(str) = Field(
        default="",
        description="Test command that runs all packages (from root Makefile or CI).",
    )


# =============================================================================
# Cleanup
# =============================================================================
shepherd.reset()
