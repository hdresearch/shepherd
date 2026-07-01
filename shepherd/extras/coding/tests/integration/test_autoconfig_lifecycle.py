"""Integration tests for config resolution with autoconfig system."""

from pathlib import Path

from shepherd.autoconfig import discover_config, persist_config, resolve_config
from shepherd_coding.workflows.pr_review.config import PRReviewConfig


class TestDiscoverConfigDelegation:
    def test_defaults_when_no_yaml(self) -> None:
        """resolve_config falls through to defaults when no YAML exists."""
        config = resolve_config(PRReviewConfig, persist=False)
        assert isinstance(config, PRReviewConfig)
        assert config.guidelines == ""

    def test_discovers_persisted_config(self, config_dir: Path, sample_config: PRReviewConfig) -> None:
        """discover_config finds config written by persist_config."""
        persist_config(sample_config, config_dir=str(config_dir))
        found = discover_config(PRReviewConfig, config_dir=str(config_dir))
        assert found is not None
        assert found.guidelines == sample_config.guidelines
        assert found.focus_areas == sample_config.focus_areas


class TestPersistRoundTrip:
    def test_persist_writes_yaml(self, tmp_path: Path) -> None:
        """persist_config writes a discoverable YAML file."""
        config = PRReviewConfig(
            guidelines="Follow PEP 8. Write tests.",
            focus_areas=["correctness", "security"],
        )
        persist_config(config, config_dir=str(tmp_path))
        assert (tmp_path / "pr_review.yaml").exists()

        loaded = discover_config(PRReviewConfig, config_dir=str(tmp_path))
        assert loaded is not None
        assert loaded.guidelines == "Follow PEP 8. Write tests."

    def test_persisted_yaml_excludes_infrastructure(self, tmp_path: Path) -> None:
        """Infrastructure fields (repo, github_token, clone_url) are not in YAML."""
        config = PRReviewConfig(
            guidelines="test",
            repo="owner/repo",
            github_token="secret",
        )
        path = persist_config(config, config_dir=str(tmp_path))
        yaml_text = path.read_text()
        assert "repo" not in yaml_text.split("\n# ")[1]  # skip header
        assert "github_token" not in yaml_text
        assert "clone_url" not in yaml_text


class TestResolveConfigIntegration:
    def test_cached_takes_precedence(self, tmp_path: Path) -> None:
        """Cached YAML is returned without LLM inference."""
        cached = PRReviewConfig(guidelines="cached value")
        persist_config(cached, config_dir=str(tmp_path))

        result = resolve_config(PRReviewConfig, config_dir=str(tmp_path))
        assert result.guidelines == "cached value"

    def test_partial_overrides(self, tmp_path: Path) -> None:
        """Explicit partial overrides win over cached values."""
        cached = PRReviewConfig(guidelines="from cache", focus_areas=["cached"])
        persist_config(cached, config_dir=str(tmp_path))

        partial = PRReviewConfig(guidelines="explicit")
        result = resolve_config(PRReviewConfig, partial, config_dir=str(tmp_path))
        assert result.guidelines == "explicit"
        assert result.focus_areas == ["cached"]

    def test_force_skips_cache(self, tmp_path: Path) -> None:
        """force=True bypasses cached YAML."""
        cached = PRReviewConfig(guidelines="cached")
        persist_config(cached, config_dir=str(tmp_path))

        # No scope → inference fails → defaults
        result = resolve_config(PRReviewConfig, force=True, persist=False, config_dir=str(tmp_path))
        assert result.guidelines == ""  # defaults, not cached
