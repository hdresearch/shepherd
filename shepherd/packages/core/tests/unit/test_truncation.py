"""Unit tests for smart_truncate utility."""

from shepherd_core.text import smart_truncate


class TestSmartTruncate:
    """Tests for smart_truncate function."""

    def test_short_text_unchanged(self):
        """Text shorter than max_len is returned unchanged."""
        text = "Hello, world!"
        assert smart_truncate(text, max_len=500) == text

    def test_exact_length_unchanged(self):
        """Text exactly at max_len is returned unchanged."""
        text = "x" * 100
        assert smart_truncate(text, max_len=100) == text

    def test_long_text_truncated(self):
        """Text longer than max_len is truncated."""
        text = "x" * 1000
        result = smart_truncate(text, max_len=100)
        assert len(result) <= 100

    def test_includes_marker(self):
        """Truncated text includes character count marker."""
        text = "x" * 1000
        result = smart_truncate(text, max_len=100)
        assert "...[" in result
        assert "chars]..." in result

    def test_preserves_head_and_tail(self):
        """Truncation preserves both head and tail of text."""
        head = "HEAD" * 10
        middle = "M" * 500
        tail = "TAIL" * 10
        text = head + middle + tail
        result = smart_truncate(text, max_len=100)

        # Head and tail patterns should be visible
        assert result.startswith("HEADHEAD")
        assert result.endswith("TAILTAIL")

    def test_short_limit_head_only(self):
        """Very short limits use head-only truncation."""
        text = "x" * 100
        result = smart_truncate(text, max_len=50, min_for_split=80)
        assert result.endswith("...")
        assert "...[" not in result  # No marker, just ellipsis

    def test_tail_ratio_affects_distribution(self):
        """tail_ratio parameter affects head/tail distribution."""
        text = "H" * 500 + "T" * 500
        result = smart_truncate(text, max_len=100, tail_ratio=0.5)

        # With 50% tail ratio, should have roughly equal head and tail
        h_count = result.count("H")
        t_count = result.count("T")
        # They should be roughly similar (within marker overhead)
        assert abs(h_count - t_count) < 20

    def test_empty_text(self):
        """Empty text returns empty string."""
        assert smart_truncate("", max_len=100) == ""

    def test_whitespace_only(self):
        """Whitespace-only text shorter than limit is unchanged."""
        text = "   "
        assert smart_truncate(text, max_len=100) == text

    def test_marker_shows_correct_count(self):
        """Marker shows correct number of omitted characters."""
        text = "x" * 1000
        result = smart_truncate(text, max_len=100)

        # Extract the number from the marker
        import re

        match = re.search(r"\.\.\.\[(\d+) chars\]\.\.\.", result)
        assert match is not None
        omitted = int(match.group(1))

        # The omitted count plus the visible text should equal original
        visible_text = result.replace(f"...[{omitted} chars]...", "")
        assert len(visible_text) + omitted == len(text)

    def test_unicode_handling(self):
        """Unicode characters are handled correctly."""
        text = "Hello " + "😀" * 100 + " World"
        result = smart_truncate(text, max_len=50)
        # Should not raise and should truncate
        assert len(result) <= 50

    def test_newlines_preserved(self):
        """Newlines in text are preserved when not truncated."""
        text = "line1\nline2\nline3"
        assert smart_truncate(text, max_len=100) == text

    def test_default_parameters(self):
        """Default parameters work correctly."""
        text = "x" * 1000
        result = smart_truncate(text)  # Uses defaults: max_len=500, tail_ratio=0.3
        assert len(result) <= 500
        assert "...[" in result


class TestSmartTruncateEdgeCases:
    """Edge case tests for smart_truncate."""

    def test_very_small_max_len(self):
        """Very small max_len still produces valid output."""
        text = "x" * 100
        result = smart_truncate(text, max_len=10, min_for_split=80)
        assert len(result) == 10
        assert result.endswith("...")

    def test_max_len_very_small(self):
        """Very small max_len produces output at or below the limit."""
        text = "hello"
        result = smart_truncate(text, max_len=4)
        # For very short limits, we get truncated output
        assert len(result) <= 4

    def test_single_char_text_long_limit(self):
        """Single character text with long limit is unchanged."""
        assert smart_truncate("x", max_len=500) == "x"
