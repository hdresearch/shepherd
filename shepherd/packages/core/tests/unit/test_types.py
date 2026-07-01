"""Tests for shepherd_core.types module."""

from shepherd_core.types import compute_transcript_path


class TestComputeTranscriptPath:
    """Tests for compute_transcript_path()."""

    def test_basic_path(self):
        """Basic path conversion with slashes."""
        result = compute_transcript_path("/Users/alice/project", "abc123")

        assert result.endswith("abc123.jsonl")
        assert "-Users-alice-project" in result

    def test_underscores_replaced_with_dashes(self):
        """Verify underscores are replaced with dashes (Spike A confirmed)."""
        result = compute_transcript_path("/Users/alice/my_project", "abc123")

        # Should contain -my-project not -my_project
        assert "-my-project" in result
        assert "_" not in result.split("/")[-2]  # project folder has no underscores
        assert result.endswith("abc123.jsonl")

    def test_multiple_underscores(self):
        """Multiple underscores should all be replaced."""
        result = compute_transcript_path("/Users/alice_bob/my_cool_project", "xyz")

        assert "-alice-bob-" in result
        assert "-my-cool-project" in result
        assert "_" not in result.split("/")[-2]

    def test_mixed_slashes_and_underscores(self):
        """Both slashes and underscores converted to dashes."""
        result = compute_transcript_path("/a_b/c_d/e_f", "sess")

        # All separators become dashes
        assert "-a-b-c-d-e-f" in result
