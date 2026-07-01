"""Unit tests for Check marker and builtin check factories."""

from pathlib import Path
from typing import Annotated

import pytest
from shepherd_runtime.task.authoring import Check, FileExists, Input, InRange, Matches, MaxLength, NonEmpty, Output

# =============================================================================
# Check base class
# =============================================================================


class TestCheck:
    """Tests for the Check marker class."""

    def test_call_returns_predicate_result(self):
        check = Check(predicate=lambda x: x > 0)
        assert check(5) is True
        assert check(-1) is False

    def test_format_message_with_interpolation(self):
        check = Check(predicate=lambda x: x > 0, message="Bad value {value} for {field}")
        msg = check.format_message(value=-1, field_name="score")
        assert msg == "Bad value -1 for score"

    def test_format_message_repr_format(self):
        check = Check(predicate=lambda x: True, message="Got {value!r}")
        msg = check.format_message(value="hello")
        assert msg == "Got 'hello'"

    def test_format_message_default_when_no_message(self):
        check = Check(predicate=lambda x: True)
        msg = check.format_message(value=42, field_name="count")
        assert "count" in msg
        assert "42" in msg

    def test_format_message_default_no_field_name(self):
        check = Check(predicate=lambda x: True)
        msg = check.format_message(value=42)
        assert "field" in msg
        assert "42" in msg

    def test_format_message_fallback_on_bad_template(self):
        check = Check(predicate=lambda x: True, message="Has {unknown_key}")
        msg = check.format_message(value=42)
        assert msg == "Has {unknown_key}"

    def test_frozen(self):
        check = Check(predicate=lambda x: True)
        with pytest.raises(AttributeError):
            check.message = "changed"  # type: ignore[misc]

    def test_isinstance(self):
        check = Check(predicate=lambda x: True)
        assert isinstance(check, Check)


# =============================================================================
# FileExists
# =============================================================================


class TestFileExists:
    """Tests for the FileExists builtin check."""

    def test_existing_file_passes(self, tmp_path: Path):
        f = tmp_path / "data.csv"
        f.write_text("a,b,c")
        check = FileExists()
        assert check(f) is True
        assert check(str(f)) is True

    def test_missing_file_fails(self, tmp_path: Path):
        check = FileExists()
        assert check(tmp_path / "nope.txt") is False

    def test_directory_passes(self, tmp_path: Path):
        check = FileExists()
        assert check(tmp_path) is True

    def test_returns_check_instance(self):
        assert isinstance(FileExists(), Check)

    def test_default_message_interpolation(self, tmp_path: Path):
        check = FileExists()
        missing = tmp_path / "nope.txt"
        msg = check.format_message(value=missing)
        assert str(missing) in msg

    def test_custom_message(self):
        check = FileExists(message="Need file: {value}")
        msg = check.format_message(value="/tmp/x")
        assert msg == "Need file: /tmp/x"


# =============================================================================
# NonEmpty
# =============================================================================


class TestNonEmpty:
    """Tests for the NonEmpty builtin check."""

    def test_non_empty_string_passes(self):
        check = NonEmpty()
        assert check("hello") is True

    def test_empty_string_fails(self):
        check = NonEmpty()
        assert check("") is False

    def test_whitespace_only_fails(self):
        check = NonEmpty()
        assert check("   ") is False
        assert check("\t\n") is False

    def test_none_fails(self):
        check = NonEmpty()
        assert check(None) is False

    def test_non_empty_list_passes(self):
        check = NonEmpty()
        assert check([1, 2]) is True

    def test_empty_list_fails(self):
        check = NonEmpty()
        assert check([]) is False

    def test_empty_dict_fails(self):
        check = NonEmpty()
        assert check({}) is False

    def test_non_empty_dict_passes(self):
        check = NonEmpty()
        assert check({"a": 1}) is True

    def test_zero_is_non_empty(self):
        """Numeric zero is a valid value, not 'empty'."""
        check = NonEmpty()
        assert check(0) is True

    def test_false_is_non_empty(self):
        """Boolean False is a valid value, not 'empty'."""
        check = NonEmpty()
        assert check(False) is True

    def test_returns_check_instance(self):
        assert isinstance(NonEmpty(), Check)

    def test_custom_message(self):
        check = NonEmpty(message="Required field")
        assert check.message == "Required field"


# =============================================================================
# InRange
# =============================================================================


class TestInRange:
    """Tests for the InRange builtin check."""

    def test_within_range_passes(self):
        check = InRange(0.0, 1.0)
        assert check(0.5) is True

    def test_at_min_boundary_passes(self):
        check = InRange(0.0, 1.0)
        assert check(0.0) is True

    def test_at_max_boundary_passes(self):
        check = InRange(0.0, 1.0)
        assert check(1.0) is True

    def test_below_min_fails(self):
        check = InRange(0.0, 1.0)
        assert check(-0.1) is False

    def test_above_max_fails(self):
        check = InRange(0.0, 1.0)
        assert check(1.1) is False

    def test_min_only(self):
        check = InRange(min_val=0)
        assert check(100) is True
        assert check(-1) is False

    def test_max_only(self):
        check = InRange(max_val=10)
        assert check(-100) is True
        assert check(11) is False

    def test_integer_range(self):
        check = InRange(1, 100)
        assert check(50) is True
        assert check(0) is False

    def test_returns_check_instance(self):
        assert isinstance(InRange(0, 1), Check)

    def test_message_both_bounds(self):
        check = InRange(0.0, 1.0)
        msg = check.format_message(value=1.5)
        assert "1.5" in msg
        assert "0.0" in msg
        assert "1.0" in msg

    def test_message_min_only(self):
        check = InRange(min_val=0)
        msg = check.format_message(value=-1)
        assert ">=" in msg

    def test_message_max_only(self):
        check = InRange(max_val=10)
        msg = check.format_message(value=11)
        assert "<=" in msg

    def test_custom_message(self):
        check = InRange(0, 1, message="Score {value} invalid")
        msg = check.format_message(value=2.0)
        assert msg == "Score 2.0 invalid"


# =============================================================================
# Matches
# =============================================================================


class TestMatches:
    """Tests for the Matches builtin check."""

    def test_matching_pattern_passes(self):
        check = Matches(r"^https?://")
        assert check("https://example.com") is True
        assert check("http://example.com") is True

    def test_non_matching_fails(self):
        check = Matches(r"^https?://")
        assert check("ftp://example.com") is False

    def test_partial_match(self):
        """Pattern uses re.search, so partial matches work."""
        check = Matches(r"\d+")
        assert check("abc123def") is True
        assert check("no digits") is False

    def test_full_string_match_with_anchors(self):
        check = Matches(r"^\d+$")
        assert check("123") is True
        assert check("abc123") is False

    def test_pattern_with_braces(self):
        """Braces in patterns should not break message formatting."""
        check = Matches(r"\{[a-z]+\}")
        assert check("{hello}") is True
        assert check("no braces") is False
        # Message should not raise
        msg = check.format_message(value="test")
        assert isinstance(msg, str)

    def test_returns_check_instance(self):
        assert isinstance(Matches(r".*"), Check)

    def test_custom_message(self):
        check = Matches(r"^\d+$", message="Must be numeric: {value}")
        msg = check.format_message(value="abc")
        assert msg == "Must be numeric: abc"


# =============================================================================
# MaxLength
# =============================================================================


class TestMaxLength:
    """Tests for the MaxLength builtin check."""

    def test_within_limit_passes(self):
        check = MaxLength(10)
        assert check("short") is True

    def test_at_limit_passes(self):
        check = MaxLength(5)
        assert check("12345") is True

    def test_over_limit_fails(self):
        check = MaxLength(5)
        assert check("123456") is False

    def test_empty_passes(self):
        check = MaxLength(5)
        assert check("") is True

    def test_list_length(self):
        check = MaxLength(3)
        assert check([1, 2, 3]) is True
        assert check([1, 2, 3, 4]) is False

    def test_returns_check_instance(self):
        assert isinstance(MaxLength(10), Check)

    def test_default_message(self):
        check = MaxLength(5)
        msg = check.format_message(value="toolong")
        assert "5" in msg

    def test_custom_message(self):
        check = MaxLength(100, message="Too long: {value!r}")
        msg = check.format_message(value="x" * 200)
        assert msg.startswith("Too long:")


# =============================================================================
# Integration: Check with Annotated types
# =============================================================================


class TestCheckAnnotatedIntegration:
    """Test that Check instances work inside Annotated[] metadata."""

    def test_check_in_annotated_metadata(self):
        """Check instances can be placed in Annotated metadata."""
        from typing import get_type_hints

        class MyModel:
            source: Annotated[Input(Path), FileExists()]
            score: Annotated[Input(float), InRange(0.0, 1.0)]

        hints = get_type_hints(MyModel, include_extras=True)

        # Verify Check markers are present in metadata
        for name in ("source", "score"):
            hint = hints[name]
            checks = [m for m in hint.__metadata__ if isinstance(m, Check)]
            assert len(checks) == 1, f"Expected 1 Check on {name}, got {len(checks)}"

    def test_multiple_checks_on_one_field(self):
        """Multiple Check markers can coexist on a single field."""
        from typing import get_type_hints

        class MyModel:
            summary: Annotated[Output(str), NonEmpty(), MaxLength(500)]

        hints = get_type_hints(MyModel, include_extras=True)
        hint = hints["summary"]
        checks = [m for m in hint.__metadata__ if isinstance(m, Check)]
        assert len(checks) == 2
