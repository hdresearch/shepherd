"""Tests for scenario generation.

These tests validate that scenarios are generated correctly:
- All scenario branches are created
- Patches apply cleanly
- Main branch is clean
- Scenario documentation is staged properly
- Generation is deterministic

Run with: pytest fixtures/tests/test_generation.py -v
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fixtures._lib import LibraryConfig, GIT_ENV


# Expected branches when generating rich-cli base
EXPECTED_BRANCHES = [
    "main",
    "feature/add-csv-export",
    "bugfix/unicode-handling",
    "review/add-quiet-mode",
    "refactor/split-formatters",
]

# Scenario documentation files
SCENARIO_DOCS = {
    "feature/add-csv-export": ["design.md"],
    "bugfix/unicode-handling": ["description.md"],
    "review/add-quiet-mode": ["expected_issues.md"],
    "refactor/split-formatters": ["instructions.md"],
}


def run_git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in the given directory."""
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def get_branches(path: Path) -> list[str]:
    """Get list of branch names in a git repo."""
    result = run_git(path, "branch", "--format=%(refname:short)")
    return [b.strip() for b in result.stdout.strip().split("\n") if b.strip()]


@pytest.fixture
def library() -> LibraryConfig:
    """Load the library configuration."""
    fixtures_root = Path(__file__).parent.parent
    return LibraryConfig.load(fixtures_root)


class TestBaseGeneration:
    """Tests for base project generation."""

    def test_generate_base_only(self, tmp_path: Path, library: LibraryConfig):
        """Verify base-only generation creates main branch."""
        target = tmp_path / "workspace"

        base_config = library.get_base("rich-cli")
        base_config.generate(target, verbose=False)

        # Should have main branch
        branches = get_branches(target)
        assert "main" in branches

        # Should have base files
        assert (target / "src" / "rich_cli" / "__main__.py").exists()
        assert (target / "pyproject.toml").exists()

    def test_generate_base_is_clean(self, tmp_path: Path, library: LibraryConfig):
        """Verify generated base has no uncommitted changes."""
        target = tmp_path / "workspace"

        base_config = library.get_base("rich-cli")
        base_config.generate(target, verbose=False)

        result = run_git(target, "status", "--porcelain")
        assert result.stdout.strip() == "", "Base has uncommitted changes"


class TestScenarioGeneration:
    """Tests for scenario generation."""

    @pytest.fixture
    def scenario_workspace(self, tmp_path: Path, library: LibraryConfig) -> Path:
        """Generate a workspace with fix_bug scenario."""
        target = tmp_path / "workspace"

        base_config = library.get_base("rich-cli")
        base_config.generate(target, verbose=False)

        scenario_config = base_config.get_scenario("fix_bug")
        scenario_config.apply(target, verbose=False)

        # Return to main
        run_git(target, "checkout", "main")

        return target

    def test_scenario_creates_branch(self, scenario_workspace: Path):
        """Verify scenario creates the correct branch."""
        branches = get_branches(scenario_workspace)
        assert "bugfix/unicode-handling" in branches

    def test_scenario_stages_documentation(self, scenario_workspace: Path):
        """Verify scenario documentation is staged."""
        run_git(scenario_workspace, "checkout", "bugfix/unicode-handling")

        scenario_dir = scenario_workspace / ".ai-runner" / "scenario"
        assert scenario_dir.exists()
        assert (scenario_dir / "description.md").exists()

    def test_scenario_preserves_main(self, scenario_workspace: Path):
        """Verify main branch is unchanged."""
        run_git(scenario_workspace, "checkout", "main")

        # Main should not have .ai-runner/scenario
        scenario_dir = scenario_workspace / ".ai-runner" / "scenario"
        assert not scenario_dir.exists()


class TestFullGeneration:
    """Tests for generating all scenarios."""

    @pytest.fixture
    def full_workspace(self, tmp_path: Path, library: LibraryConfig) -> Path:
        """Generate workspace with all rich-cli scenarios."""
        target = tmp_path / "workspace"

        base_config = library.get_base("rich-cli")
        base_config.generate(target, verbose=False)

        # Apply all scenarios
        for scenario_name in base_config.list_scenarios():
            scenario = base_config.get_scenario(scenario_name)
            scenario.apply(target, verbose=False)

        # Return to main
        run_git(target, "checkout", "main")

        return target

    def test_all_branches_created(self, full_workspace: Path):
        """Verify all expected branches exist."""
        branches = get_branches(full_workspace)

        for expected in EXPECTED_BRANCHES:
            assert expected in branches, f"Missing branch: {expected}"

    def test_all_scenarios_have_docs(self, full_workspace: Path):
        """Verify each scenario has its documentation."""
        for branch, expected_docs in SCENARIO_DOCS.items():
            run_git(full_workspace, "checkout", branch)

            scenario_dir = full_workspace / ".ai-runner" / "scenario"
            assert scenario_dir.exists(), f"Missing .ai-runner/scenario/ on {branch}"

            for doc in expected_docs:
                doc_path = scenario_dir / doc
                assert doc_path.exists(), f"Missing {doc} on {branch}"
                assert doc_path.stat().st_size > 0, f"Empty {doc} on {branch}"

        # Return to main
        run_git(full_workspace, "checkout", "main")

    def test_generation_is_deterministic(self, tmp_path: Path, library: LibraryConfig):
        """Verify generating twice produces identical commits."""
        base_config = library.get_base("rich-cli")

        # Generate first workspace
        ws1 = tmp_path / "workspace1"
        base_config.generate(ws1, verbose=False)
        for scenario_name in base_config.list_scenarios():
            scenario = base_config.get_scenario(scenario_name)
            scenario.apply(ws1, verbose=False)

        # Generate second workspace
        ws2 = tmp_path / "workspace2"
        base_config.generate(ws2, verbose=False)
        for scenario_name in base_config.list_scenarios():
            scenario = base_config.get_scenario(scenario_name)
            scenario.apply(ws2, verbose=False)

        # Compare HEAD commit on each branch
        for branch in EXPECTED_BRANCHES:
            run_git(ws1, "checkout", branch)
            run_git(ws2, "checkout", branch)

            commit1 = run_git(ws1, "rev-parse", "HEAD").stdout.strip()
            commit2 = run_git(ws2, "rev-parse", "HEAD").stdout.strip()

            assert commit1 == commit2, (
                f"Branch {branch} has different commits:\n"
                f"  workspace1: {commit1}\n"
                f"  workspace2: {commit2}"
            )


class TestTemplateGeneration:
    """Tests for template scenario application."""

    @pytest.fixture
    def template_workspace(self, tmp_path: Path, library: LibraryConfig) -> Path:
        """Create a minimal project to apply template to."""
        target = tmp_path / "project"
        target.mkdir()

        # Create minimal project structure
        (target / "src").mkdir()
        (target / "src" / "main.py").write_text("# Main module\n")
        (target / "tests").mkdir()

        return target

    def test_template_creates_scenario_dir(
        self, template_workspace: Path, library: LibraryConfig
    ):
        """Verify template creates .ai-runner/scenario/ directory."""
        scenario = library.get_scenario("template/tdd_feature")

        params = {
            "feature_name": "Test Feature",
            "feature_description": "A test feature description",
            "acceptance_criteria": "- Criterion 1\n- Criterion 2",
        }

        scenario.apply(template_workspace, params=params, verbose=False)

        scenario_dir = template_workspace / ".ai-runner" / "scenario"
        assert scenario_dir.exists()
        assert (scenario_dir / "design.md").exists()

    def test_template_fills_placeholders(
        self, template_workspace: Path, library: LibraryConfig
    ):
        """Verify template placeholders are replaced."""
        scenario = library.get_scenario("template/tdd_feature")

        params = {
            "feature_name": "My Cool Feature",
            "feature_description": "Does something cool",
            "acceptance_criteria": "- It works",
        }

        scenario.apply(template_workspace, params=params, verbose=False)

        design_content = (
            template_workspace / ".ai-runner" / "scenario" / "design.md"
        ).read_text()

        assert "My Cool Feature" in design_content
        assert "Does something cool" in design_content
        assert "It works" in design_content

        # Placeholders should be gone
        assert "{{feature_name}}" not in design_content

    def test_template_missing_params_raises(
        self, template_workspace: Path, library: LibraryConfig
    ):
        """Verify missing placeholders raise ValueError."""
        scenario = library.get_scenario("template/tdd_feature")

        with pytest.raises(ValueError) as exc_info:
            scenario.apply(template_workspace, params={}, verbose=False)

        assert "Missing required placeholders" in str(exc_info.value)
