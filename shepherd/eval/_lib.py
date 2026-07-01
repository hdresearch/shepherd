"""Scenario Library - Shared generation logic.

This module provides classes and functions for managing the scenario library,
including base project configuration, scenario definitions, and workspace generation.

Classes:
    LibraryConfig: Parse library.yaml and lookup bases/scenarios
    BaseConfig: Parse base.yaml and generate base projects
    ScenarioConfig: Parse scenario.yaml and apply patches/docs

Core Functions:
    init_from_local_base(): Copy base + apply history patches
    create_scenario_branch(): Apply scenario patches + docs to a branch
    apply_template(): Fill placeholders in template docs
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Deterministic commit metadata for generated commits
GIT_ENV = {
    "GIT_AUTHOR_NAME": "ai-runner fixture",
    "GIT_AUTHOR_EMAIL": "fixture@ai-runner.local",
    "GIT_COMMITTER_NAME": "ai-runner fixture",
    "GIT_COMMITTER_EMAIL": "fixture@ai-runner.local",
    "GIT_AUTHOR_DATE": "2024-01-15T12:00:00+00:00",
    "GIT_COMMITTER_DATE": "2024-01-15T12:00:00+00:00",
}


def run_git(
    target: Path,
    *args: str,
    check: bool = True,
    capture: bool = False,
    env_override: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a git command in the target directory."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)

    result = subprocess.run(
        ["git", "-C", str(target), *args],
        check=check,
        capture_output=capture,
        text=True,
        env=env,
    )
    return result


@dataclass
class LibraryConfig:
    """Parsed scenario library configuration from library.yaml."""

    root: Path
    _data: dict[str, Any] = field(repr=False)

    @classmethod
    def load(cls, root: Path | None = None) -> "LibraryConfig":
        """Load library configuration from fixtures/library.yaml.

        Args:
            root: Root directory containing library.yaml.
                  Defaults to the fixtures/ directory.
        """
        if root is None:
            root = Path(__file__).parent

        library_path = root / "library.yaml"
        if not library_path.exists():
            raise FileNotFoundError(f"Library config not found: {library_path}")

        data = yaml.safe_load(library_path.read_text())
        return cls(root=root, _data=data)

    @property
    def version(self) -> str:
        """Library schema version."""
        return self._data.get("version", "1.0")

    def list_bases(self) -> list[str]:
        """List all available base project names."""
        return list(self._data.get("bases", {}).keys())

    def list_templates(self) -> list[str]:
        """List all available template scenario names."""
        return list(self._data.get("templates", []))

    def get_base(self, name: str) -> "BaseConfig":
        """Get configuration for a base project.

        Args:
            name: Base project name (e.g., "rich-cli")

        Raises:
            KeyError: If base not found
        """
        bases = self._data.get("bases", {})
        if name not in bases:
            available = ", ".join(bases.keys()) or "(none)"
            raise KeyError(f"Base '{name}' not found. Available: {available}")

        base_info = bases[name]
        base_path = self.root / base_info["path"]
        return BaseConfig.load(base_path)

    def list_scenarios(self, base: str | None = None) -> list[str]:
        """List all scenarios, optionally filtered by base.

        Args:
            base: If provided, only list scenarios for this base.
                  If None, list all scenarios across all bases.

        Returns:
            List of scenario IDs in format "base/scenario" or "template/scenario"
        """
        scenarios = []

        # Project-specific scenarios
        if base:
            # List scenarios for a specific base only
            try:
                base_config = self.get_base(base)
                for name in base_config.list_scenarios():
                    scenarios.append(f"{base}/{name}")
            except (KeyError, FileNotFoundError):
                pass
        else:
            # List scenarios for all bases
            for base_name in self.list_bases():
                try:
                    base_config = self.get_base(base_name)
                    for name in base_config.list_scenarios():
                        scenarios.append(f"{base_name}/{name}")
                except (KeyError, FileNotFoundError):
                    pass

            # Template scenarios (only when not filtering by base)
            for template_name in self.list_templates():
                scenarios.append(f"template/{template_name}")

        return scenarios

    def get_scenario(self, scenario_id: str) -> "ScenarioConfig":
        """Get configuration for a scenario.

        Args:
            scenario_id: Scenario ID in format "base/scenario" or "template/scenario"

        Raises:
            ValueError: If scenario_id format is invalid
            KeyError: If scenario not found
        """
        if "/" not in scenario_id:
            raise ValueError(
                f"Invalid scenario ID: {scenario_id}. "
                f"Expected format: 'base/scenario' or 'template/scenario'"
            )

        base_name, scenario_name = scenario_id.split("/", 1)

        if base_name == "template":
            # Template scenario
            template_path = self.root / "templates" / scenario_name
            if not template_path.exists():
                available = ", ".join(self.list_templates()) or "(none)"
                raise KeyError(
                    f"Template '{scenario_name}' not found. Available: {available}"
                )
            return ScenarioConfig.load(template_path)
        else:
            # Project-specific scenario
            base_config = self.get_base(base_name)
            return base_config.get_scenario(scenario_name)


@dataclass
class BaseConfig:
    """Configuration for a base project from base.yaml."""

    path: Path
    _data: dict[str, Any] = field(repr=False)

    @classmethod
    def load(cls, path: Path) -> "BaseConfig":
        """Load base configuration from a directory containing base.yaml."""
        config_path = path / "base.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"Base config not found: {config_path}")

        data = yaml.safe_load(config_path.read_text())
        return cls(path=path, _data=data)

    @property
    def name(self) -> str:
        """Base project name."""
        return self._data["name"]

    @property
    def repo(self) -> str:
        """Upstream repository URL."""
        return self._data["repo"]

    @property
    def commit(self) -> str:
        """Pinned commit hash."""
        return self._data["commit"]

    @property
    def description(self) -> str:
        """Project description."""
        return self._data.get("description", "")

    @property
    def history_depth(self) -> int:
        """Number of commits to replay from history."""
        return self._data.get("history_depth", 10)

    @property
    def include_patterns(self) -> list[str]:
        """File patterns to include."""
        return self._data.get("include", [])

    @property
    def exclude_patterns(self) -> list[str]:
        """File patterns to exclude."""
        return self._data.get("exclude", [])

    @property
    def base_dir(self) -> Path:
        """Directory containing base source files."""
        return self.path / "base"

    @property
    def history_dir(self) -> Path:
        """Directory containing history patches."""
        return self.path / "history"

    @property
    def scenarios_dir(self) -> Path:
        """Directory containing scenario definitions."""
        return self.path / "scenarios"

    def list_scenarios(self) -> list[str]:
        """List all scenarios for this base."""
        if not self.scenarios_dir.exists():
            return []

        scenarios = []
        for item in self.scenarios_dir.iterdir():
            if item.is_dir() and (item / "scenario.yaml").exists():
                scenarios.append(item.name)
        return sorted(scenarios)

    def get_scenario(self, name: str) -> "ScenarioConfig":
        """Get configuration for a scenario.

        Args:
            name: Scenario name (e.g., "fix_bug")

        Raises:
            KeyError: If scenario not found
        """
        scenario_path = self.scenarios_dir / name
        if not scenario_path.exists() or not (scenario_path / "scenario.yaml").exists():
            available = ", ".join(self.list_scenarios()) or "(none)"
            raise KeyError(f"Scenario '{name}' not found. Available: {available}")

        return ScenarioConfig.load(scenario_path, base_config=self)

    def generate(self, target: Path, verbose: bool = True) -> None:
        """Generate this base project at target directory.

        Creates a git repository with:
        - Initial commit from base/ files
        - History commits from history/ patches
        - Main branch ready for scenario branches

        Args:
            target: Target directory (must not exist)
            verbose: Print progress messages
        """
        init_from_local_base(
            base_dir=self.base_dir,
            history_dir=self.history_dir,
            target=target,
            verbose=verbose,
        )


@dataclass
class ScenarioConfig:
    """Configuration for a scenario from scenario.yaml."""

    path: Path
    _data: dict[str, Any] = field(repr=False)
    base_config: BaseConfig | None = None

    @classmethod
    def load(
        cls, path: Path, base_config: BaseConfig | None = None
    ) -> "ScenarioConfig":
        """Load scenario configuration from a directory containing scenario.yaml."""
        config_path = path / "scenario.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"Scenario config not found: {config_path}")

        data = yaml.safe_load(config_path.read_text())
        return cls(path=path, _data=data, base_config=base_config)

    @property
    def is_template(self) -> bool:
        """Whether this is a template scenario (no base project)."""
        return self._data.get("type") == "template"

    @property
    def name(self) -> str:
        """Scenario name."""
        return self._data.get("scenario", {}).get("name", self.path.name)

    @property
    def category(self) -> str:
        """Scenario category (e.g., fix_bug, implement_feature)."""
        return self._data.get("scenario", {}).get("category", "")

    @property
    def pipeline(self) -> str:
        """Suggested pipeline for this scenario."""
        return self._data.get("scenario", {}).get("pipeline", "")

    @property
    def branch_name(self) -> str:
        """Git branch name for this scenario."""
        return self._data.get("branch", {}).get("name", f"scenario/{self.name}")

    @property
    def branch_from(self) -> str:
        """Base branch to create scenario branch from."""
        return self._data.get("branch", {}).get("from", "main")

    @property
    def docs(self) -> list[str]:
        """Documentation files to stage."""
        return self._data.get("scenario", {}).get("docs", [])

    @property
    def patches(self) -> list[str]:
        """Patch files to apply."""
        return self._data.get("scenario", {}).get("patches", [])

    @property
    def success_criteria(self) -> list[str]:
        """Expected success criteria for evaluation."""
        return self._data.get("scenario", {}).get("success_criteria", [])

    @property
    def placeholders(self) -> dict[str, str]:
        """Placeholders for template scenarios."""
        return self._data.get("scenario", {}).get("placeholders", {})

    def apply(
        self,
        target: Path,
        params: dict[str, str] | None = None,
        verbose: bool = True,
    ) -> None:
        """Apply this scenario to a generated base.

        For project-specific scenarios:
        - Creates branch from base branch
        - Applies patches
        - Stages documentation

        For templates:
        - Fills placeholders in docs
        - Stages documentation

        Args:
            target: Target git repository
            params: Placeholder values for templates
            verbose: Print progress messages
        """
        if self.is_template:
            apply_template(
                scenario=self,
                target=target,
                params=params or {},
                verbose=verbose,
            )
        else:
            create_scenario_branch(
                scenario=self,
                target=target,
                verbose=verbose,
            )


# =============================================================================
# Core Generation Functions
# =============================================================================


def matches_patterns(path: str, patterns: list[str]) -> bool:
    """Check if path matches any of the glob patterns."""
    for pattern in patterns:
        # Handle directory patterns like "src/" or "tests/"
        if pattern.endswith("/"):
            if path.startswith(pattern) or path == pattern.rstrip("/"):
                return True
        # Handle exact matches and glob patterns
        elif fnmatch.fnmatch(path, pattern) or path == pattern:
            return True
        # Handle prefix matches for directories
        elif path.startswith(pattern + "/"):
            return True
    return False


def init_from_local_base(
    base_dir: Path,
    history_dir: Path,
    target: Path,
    verbose: bool = True,
) -> None:
    """Initialize repo from local base files and apply history patches.

    This creates a git repo with realistic commit history without network access.
    The base_dir contains source files, and history_dir contains patches that
    replay commits to reach the final state.

    Args:
        base_dir: Directory containing base source files
        history_dir: Directory containing history patches
        target: Target directory for the new repo (must not exist)
        verbose: Print progress messages
    """
    if not base_dir.exists():
        raise FileNotFoundError(
            f"Base directory not found: {base_dir}\n"
            f"Run 'python refresh.py' to populate base/ and history/ from upstream."
        )

    # 1. Create target directory and init git repo
    target.mkdir(parents=True)
    run_git(target, "init", "--quiet", "--initial-branch=main")

    # 2. Copy base files
    if verbose:
        print("  Copying base files...")
    for item in base_dir.iterdir():
        if item.name.startswith("."):
            continue
        dst = target / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)

    # 3. Initial commit
    run_git(target, "add", "-A")
    run_git(
        target,
        "commit",
        "-m",
        "Initial state (base snapshot)",
        "--allow-empty",
        env_override=GIT_ENV,
    )

    # 4. Apply history patches
    if history_dir.exists():
        patch_files = sorted(history_dir.glob("*.patch"))
        if patch_files:
            if verbose:
                print(f"  Applying {len(patch_files)} history patches...")
            for patch_file in patch_files:
                result = run_git(
                    target,
                    "am",
                    "--quiet",
                    str(patch_file.resolve()),
                    check=False,
                    env_override=GIT_ENV,
                )
                if result.returncode != 0:
                    # Try with 3-way merge
                    run_git(target, "am", "--abort", check=False)
                    result = run_git(
                        target,
                        "am",
                        "--quiet",
                        "--3way",
                        str(patch_file.resolve()),
                        check=False,
                        env_override=GIT_ENV,
                    )
                    if result.returncode != 0:
                        if verbose:
                            print(f"    Warning: Could not apply {patch_file.name}")

    # 5. Get commit count for summary
    if verbose:
        result = run_git(target, "rev-list", "--count", "HEAD", capture=True)
        commit_count = result.stdout.strip()
        print(f"  Created repository with {commit_count} commits")


def create_scenario_branch(
    scenario: ScenarioConfig,
    target: Path,
    verbose: bool = True,
) -> None:
    """Create a scenario branch with patches and documentation applied.

    Args:
        scenario: Scenario configuration
        target: Target git repository
        verbose: Print progress messages
    """
    branch_name = scenario.branch_name
    base_branch = scenario.branch_from

    if verbose:
        print(f"  Creating branch: {branch_name}")

    # Checkout base branch first
    run_git(target, "checkout", base_branch)

    # Create and checkout the scenario branch
    run_git(target, "checkout", "-b", branch_name, base_branch)

    # Copy scenario documentation files to .ai-runner/scenario/
    doc_files = []
    for doc_name in scenario.docs:
        doc_path = scenario.path / doc_name
        if doc_path.exists():
            doc_files.append(doc_path)

    # Also include any .md or .txt files not explicitly listed
    for pattern in ["*.md", "*.txt"]:
        for f in scenario.path.glob(pattern):
            if f not in doc_files and f.name != "scenario.yaml":
                doc_files.append(f)

    if doc_files:
        scenario_dest = target / ".ai-runner" / "scenario"
        scenario_dest.mkdir(parents=True, exist_ok=True)

        for doc_file in doc_files:
            shutil.copy(doc_file, scenario_dest / doc_file.name)
            if verbose:
                print(f"    Added: {doc_file.name}")

    # Apply any patches
    patches_applied = 0
    for patch_name in scenario.patches:
        patch_file = scenario.path / patch_name
        if not patch_file.exists():
            if verbose:
                print(f"    Warning: Patch not found: {patch_name}")
            continue

        if verbose:
            print(f"    Applying patch: {patch_name}")

        # Try git am first (for patches with commit metadata)
        result = run_git(
            target,
            "am",
            "--quiet",
            str(patch_file.resolve()),
            check=False,
            env_override=GIT_ENV,
        )
        if result.returncode == 0:
            patches_applied += 1
            continue

        # Abort failed am and try git apply
        run_git(target, "am", "--abort", check=False)
        result = run_git(
            target,
            "apply",
            "--ignore-whitespace",
            str(patch_file.resolve()),
            check=False,
        )
        if result.returncode == 0:
            patches_applied += 1
            run_git(target, "add", "-A")
            run_git(
                target,
                "commit",
                "-m",
                f"Apply {patch_name}",
                env_override=GIT_ENV,
            )
        else:
            if verbose:
                print(f"    Warning: Could not apply {patch_name} (skipped)")

    # Commit documentation if any was added
    result = run_git(target, "status", "--porcelain", capture=True)
    if result.stdout.strip():
        run_git(target, "add", "-A")
        run_git(
            target,
            "commit",
            "-m",
            f"Add scenario documentation for {branch_name}",
            "--allow-empty",
            env_override=GIT_ENV,
        )


def apply_template(
    scenario: ScenarioConfig,
    target: Path,
    params: dict[str, str],
    verbose: bool = True,
) -> None:
    """Apply a template scenario to an existing project.

    Fills placeholders in template docs and stages them in .ai-runner/scenario/.

    Args:
        scenario: Template scenario configuration
        target: Target directory (existing project)
        params: Placeholder values (e.g., {"feature_name": "Add caching"})
        verbose: Print progress messages
    """
    # Validate required placeholders
    missing = []
    for key, description in scenario.placeholders.items():
        if key not in params:
            missing.append(f"  {key}: {description}")

    if missing:
        raise ValueError(
            f"Missing required placeholders:\n" + "\n".join(missing)
        )

    # Create scenario destination
    scenario_dest = target / ".ai-runner" / "scenario"
    scenario_dest.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"  Applying template: {scenario.name}")

    # Process documentation files
    for doc_name in scenario.docs:
        doc_path = scenario.path / doc_name
        if not doc_path.exists():
            if verbose:
                print(f"    Warning: Doc not found: {doc_name}")
            continue

        # Read and fill placeholders
        content = doc_path.read_text()
        for key, value in params.items():
            placeholder = "{{" + key + "}}"
            content = content.replace(placeholder, value)

        # Check for unfilled placeholders
        remaining = re.findall(r"\{\{(\w+)\}\}", content)
        if remaining:
            if verbose:
                print(f"    Warning: Unfilled placeholders in {doc_name}: {remaining}")

        # Write filled content
        dest_name = doc_name.replace("-template", "")
        dest_path = scenario_dest / dest_name
        dest_path.write_text(content)

        if verbose:
            print(f"    Created: {dest_name}")

    if verbose:
        print(f"  Template applied to: {scenario_dest}")
