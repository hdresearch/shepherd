"""Tests for scenario library configuration.

These tests validate that the library.yaml registry and all scenario configs
are valid and consistent.

Run with: pytest fixtures/tests/test_library.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fixtures._lib import LibraryConfig, BaseConfig, ScenarioConfig


@pytest.fixture
def library() -> LibraryConfig:
    """Load the library configuration."""
    fixtures_root = Path(__file__).parent.parent
    return LibraryConfig.load(fixtures_root)


class TestLibraryConfig:
    """Tests for LibraryConfig."""

    def test_load_library(self, library: LibraryConfig):
        """Verify library.yaml loads successfully."""
        assert library.version == "1.0"

    def test_list_bases(self, library: LibraryConfig):
        """Verify bases are listed."""
        bases = library.list_bases()
        assert "rich-cli" in bases

    def test_list_templates(self, library: LibraryConfig):
        """Verify templates are listed."""
        templates = library.list_templates()
        assert "tdd_feature" in templates

    def test_get_base(self, library: LibraryConfig):
        """Verify base can be retrieved."""
        base = library.get_base("rich-cli")
        assert base.name == "rich-cli"
        assert base.commit.startswith("36b4229")

    def test_get_base_not_found(self, library: LibraryConfig):
        """Verify KeyError for missing base."""
        with pytest.raises(KeyError) as exc_info:
            library.get_base("nonexistent")
        assert "not found" in str(exc_info.value).lower()

    def test_list_scenarios_all(self, library: LibraryConfig):
        """Verify all scenarios are listed."""
        scenarios = library.list_scenarios()

        # Should have project-specific scenarios
        assert "rich-cli/fix_bug" in scenarios
        assert "rich-cli/implement_feature" in scenarios
        assert "rich-cli/code_review" in scenarios
        assert "rich-cli/refactor" in scenarios

        # Should have template scenarios
        assert "template/tdd_feature" in scenarios

    def test_list_scenarios_filtered(self, library: LibraryConfig):
        """Verify scenario filtering by base."""
        scenarios = library.list_scenarios(base="rich-cli")

        # Should have project-specific scenarios
        assert "rich-cli/fix_bug" in scenarios

        # Should NOT have template scenarios
        assert "template/tdd_feature" not in scenarios

    def test_get_scenario_project_specific(self, library: LibraryConfig):
        """Verify project-specific scenario can be retrieved."""
        scenario = library.get_scenario("rich-cli/fix_bug")
        assert scenario.name == "fix_bug"
        assert scenario.category == "fix_bug"
        assert not scenario.is_template
        assert scenario.branch_name == "bugfix/unicode-handling"

    def test_get_scenario_template(self, library: LibraryConfig):
        """Verify template scenario can be retrieved."""
        scenario = library.get_scenario("template/tdd_feature")
        assert scenario.name == "tdd_feature"
        assert scenario.is_template
        assert "feature_name" in scenario.placeholders

    def test_get_scenario_invalid_format(self, library: LibraryConfig):
        """Verify ValueError for invalid scenario ID format."""
        with pytest.raises(ValueError) as exc_info:
            library.get_scenario("invalid-no-slash")
        assert "Invalid scenario ID" in str(exc_info.value)


class TestBaseConfig:
    """Tests for BaseConfig."""

    @pytest.fixture
    def base_config(self, library: LibraryConfig) -> BaseConfig:
        """Load rich-cli base configuration."""
        return library.get_base("rich-cli")

    def test_base_properties(self, base_config: BaseConfig):
        """Verify base properties are accessible."""
        assert base_config.name == "rich-cli"
        assert "rich" in base_config.description.lower()
        assert base_config.history_depth == 10

    def test_base_dirs_exist(self, base_config: BaseConfig):
        """Verify base directories exist."""
        assert base_config.base_dir.exists()
        assert base_config.history_dir.exists()
        assert base_config.scenarios_dir.exists()

    def test_list_scenarios(self, base_config: BaseConfig):
        """Verify scenarios are listed."""
        scenarios = base_config.list_scenarios()
        assert "fix_bug" in scenarios
        assert "implement_feature" in scenarios
        assert "code_review" in scenarios
        assert "refactor" in scenarios

    def test_get_scenario(self, base_config: BaseConfig):
        """Verify scenario can be retrieved from base."""
        scenario = base_config.get_scenario("fix_bug")
        assert scenario.name == "fix_bug"
        assert scenario.base_config == base_config


class TestScenarioConfig:
    """Tests for ScenarioConfig."""

    @pytest.fixture
    def fix_bug_scenario(self, library: LibraryConfig) -> ScenarioConfig:
        """Load fix_bug scenario configuration."""
        return library.get_scenario("rich-cli/fix_bug")

    @pytest.fixture
    def tdd_template(self, library: LibraryConfig) -> ScenarioConfig:
        """Load tdd_feature template configuration."""
        return library.get_scenario("template/tdd_feature")

    def test_project_specific_scenario(self, fix_bug_scenario: ScenarioConfig):
        """Verify project-specific scenario properties."""
        assert not fix_bug_scenario.is_template
        assert fix_bug_scenario.name == "fix_bug"
        assert fix_bug_scenario.category == "fix_bug"
        assert fix_bug_scenario.branch_name == "bugfix/unicode-handling"
        assert fix_bug_scenario.branch_from == "main"
        assert "description.md" in fix_bug_scenario.docs
        assert "001-add-failing-unicode-test.patch" in fix_bug_scenario.patches

    def test_template_scenario(self, tdd_template: ScenarioConfig):
        """Verify template scenario properties."""
        assert tdd_template.is_template
        assert tdd_template.name == "tdd_feature"
        assert "feature_name" in tdd_template.placeholders
        assert "design-template.md" in tdd_template.docs
        assert len(tdd_template.patches) == 0

    def test_scenario_docs_exist(self, fix_bug_scenario: ScenarioConfig):
        """Verify scenario docs exist on disk."""
        for doc in fix_bug_scenario.docs:
            doc_path = fix_bug_scenario.path / doc
            assert doc_path.exists(), f"Missing doc: {doc}"

    def test_scenario_patches_exist(self, fix_bug_scenario: ScenarioConfig):
        """Verify scenario patches exist on disk."""
        for patch in fix_bug_scenario.patches:
            patch_path = fix_bug_scenario.path / patch
            assert patch_path.exists(), f"Missing patch: {patch}"
