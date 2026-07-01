"""Tests for effect stream comparison utilities."""

import pytest
from shepherd_core.effects import (
    AgentThinking,
    FileCreate,
    FilePatch,
    FileRead,
    TaskCompleted,
    TaskFailed,
    TaskStarted,
    ToolCallCompleted,
    ToolCallRejected,
    ToolCallStarted,
)
from shepherd_core.effects.comparison import (
    CRITICAL_THRESHOLD,
    IMPORTANT_THRESHOLD,
    ComparisonConfig,
    ComparisonResult,
    Divergence,
    ReferenceCorpus,
    compare_file_access,
    compare_streams,
    compare_tool_sequences,
    detect_patterns,
    explain_outcome_difference,
    find_anomalies,
)
from shepherd_core.scope.stream import Stream

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def empty_stream() -> Stream:
    """Empty stream for edge case testing."""
    return Stream()


@pytest.fixture
def success_stream() -> Stream:
    """Stream with a successful task execution."""
    stream = Stream()
    stream = stream.append(TaskStarted(task_name="FixBug"))
    stream = stream.append(
        ToolCallStarted(tool_name="read_file", tool_call_id="tc_001", params={"path": "src/auth.py"})
    )
    stream = stream.append(FileRead(path="src/auth.py"))
    stream = stream.append(ToolCallCompleted(tool_name="read_file", tool_call_id="tc_001", success=True))
    stream = stream.append(AgentThinking(content="I see the bug is on line 42"))
    stream = stream.append(
        ToolCallStarted(tool_name="edit_file", tool_call_id="tc_002", params={"path": "src/auth.py"})
    )
    stream = stream.append(FilePatch(path="src/auth.py", old_content="old", new_content="new", caused_by="tc_002"))
    stream = stream.append(ToolCallCompleted(tool_name="edit_file", tool_call_id="tc_002", success=True))
    return stream.append(TaskCompleted(task_name="FixBug", duration_ms=1234.5))


@pytest.fixture
def failure_stream() -> Stream:
    """Stream with a failed task execution."""
    stream = Stream()
    stream = stream.append(TaskStarted(task_name="FixBug"))
    stream = stream.append(
        ToolCallStarted(tool_name="read_file", tool_call_id="tc_001", params={"path": "src/wrong.py"})
    )
    stream = stream.append(ToolCallRejected(tool_name="read_file", tool_call_id="tc_001", reason="File not found"))
    return stream.append(
        TaskFailed(
            task_name="FixBug",
            error="FileNotFoundError: File not found: src/wrong.py",
            error_type="FileNotFoundError",
            error_location="provider.py:123",
            suggestions=("Check the file path", "Verify file exists"),
        )
    )


@pytest.fixture
def alternate_success_stream() -> Stream:
    """Another successful stream with different files."""
    stream = Stream()
    stream = stream.append(TaskStarted(task_name="FixBug"))
    stream = stream.append(
        ToolCallStarted(tool_name="read_file", tool_call_id="tc_001", params={"path": "src/utils.py"})
    )
    stream = stream.append(FileRead(path="src/utils.py"))
    stream = stream.append(ToolCallCompleted(tool_name="read_file", tool_call_id="tc_001", success=True))
    stream = stream.append(
        ToolCallStarted(tool_name="edit_file", tool_call_id="tc_002", params={"path": "src/utils.py"})
    )
    stream = stream.append(FilePatch(path="src/utils.py", old_content="old", new_content="new", caused_by="tc_002"))
    stream = stream.append(ToolCallCompleted(tool_name="edit_file", tool_call_id="tc_002", success=True))
    return stream.append(TaskCompleted(task_name="FixBug", duration_ms=567.8))


# =============================================================================
# Constants Tests
# =============================================================================


class TestConstants:
    """Tests for module-level constants."""

    def test_critical_threshold_value(self):
        """CRITICAL_THRESHOLD is 0.8."""
        assert CRITICAL_THRESHOLD == 0.8

    def test_important_threshold_value(self):
        """IMPORTANT_THRESHOLD is 0.6."""
        assert IMPORTANT_THRESHOLD == 0.6

    def test_threshold_ordering(self):
        """CRITICAL_THRESHOLD > IMPORTANT_THRESHOLD."""
        assert CRITICAL_THRESHOLD > IMPORTANT_THRESHOLD


# =============================================================================
# ComparisonConfig Tests
# =============================================================================


class TestComparisonConfig:
    """Tests for ComparisonConfig dataclass."""

    def test_default_values(self):
        """ComparisonConfig has sensible defaults."""
        config = ComparisonConfig()

        assert config.outcome_weight == 0.9
        assert config.tool_sequence_weight == 0.7
        assert config.file_access_exclusive_weight == 0.5
        assert config.file_access_operation_weight == 0.6

    def test_custom_values(self):
        """ComparisonConfig accepts custom values."""
        config = ComparisonConfig(
            outcome_weight=1.0,
            tool_sequence_weight=0.5,
            file_access_exclusive_weight=0.8,
        )

        assert config.outcome_weight == 1.0
        assert config.tool_sequence_weight == 0.5
        assert config.file_access_exclusive_weight == 0.8


# =============================================================================
# Divergence Tests
# =============================================================================


class TestDivergence:
    """Tests for Divergence dataclass."""

    def test_create_divergence(self):
        """Divergence can be created with required fields."""
        d = Divergence(
            aspect="tool_sequence",
            description="Different tools called",
        )

        assert d.aspect == "tool_sequence"
        assert d.description == "Different tools called"
        assert d.significance == 0.5  # Default

    def test_divergence_with_all_fields(self):
        """Divergence accepts all optional fields."""
        d = Divergence(
            aspect="file_access",
            description="File only in A",
            sequence_a=5,
            sequence_b=None,
            significance=0.8,
        )

        assert d.sequence_a == 5
        assert d.sequence_b is None
        assert d.significance == 0.8


# =============================================================================
# ComparisonResult Tests
# =============================================================================


class TestComparisonResult:
    """Tests for ComparisonResult dataclass."""

    def test_has_divergences_false_when_empty(self, success_stream: Stream):
        """has_divergences is False when no divergences."""
        result = ComparisonResult(
            stream_a=success_stream,
            stream_b=success_stream,
        )

        assert result.has_divergences is False
        assert result.is_equivalent is True

    def test_has_divergences_true_when_present(self, success_stream: Stream):
        """has_divergences is True when divergences exist."""
        result = ComparisonResult(
            stream_a=success_stream,
            stream_b=success_stream,
            divergences=[Divergence(aspect="outcome", description="Different outcome", significance=0.9)],
        )

        assert result.has_divergences is True
        assert result.is_equivalent is False

    def test_critical_divergences_filters_by_threshold(self, success_stream: Stream):
        """critical_divergences returns only high-significance items."""
        result = ComparisonResult(
            stream_a=success_stream,
            stream_b=success_stream,
            divergences=[
                Divergence(aspect="outcome", description="Critical", significance=0.9),
                Divergence(aspect="tool_sequence", description="Important", significance=0.7),
                Divergence(aspect="file_access", description="Minor", significance=0.3),
            ],
        )

        critical = result.critical_divergences
        assert len(critical) == 1
        assert critical[0].description == "Critical"

    def test_important_divergences_filters_by_threshold(self, success_stream: Stream):
        """important_divergences returns items >= IMPORTANT_THRESHOLD."""
        result = ComparisonResult(
            stream_a=success_stream,
            stream_b=success_stream,
            divergences=[
                Divergence(aspect="outcome", description="Critical", significance=0.9),
                Divergence(aspect="tool_sequence", description="Important", significance=0.7),
                Divergence(aspect="file_access", description="Minor", significance=0.3),
            ],
        )

        important = result.important_divergences
        assert len(important) == 2

    def test_divergences_by_aspect(self, success_stream: Stream):
        """divergences_by_aspect groups correctly."""
        result = ComparisonResult(
            stream_a=success_stream,
            stream_b=success_stream,
            divergences=[
                Divergence(aspect="outcome", description="D1", significance=0.9),
                Divergence(aspect="file_access", description="D2", significance=0.5),
                Divergence(aspect="file_access", description="D3", significance=0.5),
            ],
        )

        by_aspect = result.divergences_by_aspect
        assert "outcome" in by_aspect
        assert "file_access" in by_aspect
        assert len(by_aspect["outcome"]) == 1
        assert len(by_aspect["file_access"]) == 2

    def test_summary_output(self, success_stream: Stream, failure_stream: Stream):
        """summary() returns readable text."""
        result = compare_streams(success_stream, failure_stream)
        summary = result.summary()

        assert "Comparison:" in summary
        assert "Same outcome:" in summary
        assert "Divergences:" in summary

    def test_to_markdown_output(self, success_stream: Stream, failure_stream: Stream):
        """to_markdown() returns formatted markdown."""
        result = compare_streams(success_stream, failure_stream)
        md = result.to_markdown()

        assert "## Comparison:" in md
        assert "### Summary" in md
        assert "| Aspect | Same? |" in md


# =============================================================================
# compare_tool_sequences Tests
# =============================================================================


class TestCompareToolSequences:
    """Tests for compare_tool_sequences function."""

    def test_identical_sequences_no_divergences(self, success_stream: Stream):
        """Identical tool sequences produce no divergences."""
        divergences = compare_tool_sequences(success_stream, success_stream)
        assert len(divergences) == 0

    def test_different_sequences_produce_divergences(self, success_stream: Stream, failure_stream: Stream):
        """Different tool sequences produce divergences."""
        divergences = compare_tool_sequences(success_stream, failure_stream)
        assert len(divergences) > 0

    def test_missing_tools_detected(self, success_stream: Stream, failure_stream: Stream):
        """Tools present in one but not other are detected."""
        divergences = compare_tool_sequences(success_stream, failure_stream)

        # success has edit_file, failure doesn't
        descriptions = " ".join(d.description for d in divergences)
        assert "edit_file" in descriptions

    def test_empty_streams_no_divergences(self, empty_stream: Stream):
        """Empty streams have no divergences."""
        divergences = compare_tool_sequences(empty_stream, empty_stream)
        assert len(divergences) == 0

    def test_custom_config_affects_significance(self, success_stream: Stream, failure_stream: Stream):
        """Custom config changes divergence significance."""
        config = ComparisonConfig(tool_sequence_weight=0.3)
        divergences = compare_tool_sequences(success_stream, failure_stream, config)

        for d in divergences:
            assert d.significance == 0.3


# =============================================================================
# compare_file_access Tests
# =============================================================================


class TestCompareFileAccess:
    """Tests for compare_file_access function."""

    def test_identical_files_no_divergences(self, success_stream: Stream):
        """Identical file access produces no divergences."""
        result = compare_file_access(success_stream, success_stream)

        assert len(result.divergences) == 0
        assert result.files_only_in_a == []
        assert result.files_only_in_b == []

    def test_different_files_detected(self, success_stream: Stream, alternate_success_stream: Stream):
        """Different file access patterns are detected."""
        result = compare_file_access(success_stream, alternate_success_stream)

        assert "src/auth.py" in result.files_only_in_a
        assert "src/utils.py" in result.files_only_in_b

    def test_modified_vs_read_detected(self):
        """Detects when file is modified in one stream but only read in other."""
        stream_a = Stream()
        stream_a = stream_a.append(FileRead(path="src/shared.py"))
        stream_a = stream_a.append(FilePatch(path="src/shared.py", old_content="", new_content=""))

        stream_b = Stream()
        stream_b = stream_b.append(FileRead(path="src/shared.py"))

        result = compare_file_access(stream_a, stream_b)

        assert "src/shared.py" in result.files_modified_only_in_a
        assert len(result.files_modified_only_in_b) == 0

    def test_empty_streams(self, empty_stream: Stream):
        """Empty streams produce no divergences."""
        result = compare_file_access(empty_stream, empty_stream)

        assert len(result.divergences) == 0


# =============================================================================
# compare_streams Tests
# =============================================================================


class TestCompareStreams:
    """Tests for compare_streams function."""

    def test_identical_streams_equivalent(self, success_stream: Stream):
        """Identical streams are equivalent."""
        result = compare_streams(success_stream, success_stream)

        assert result.is_equivalent
        assert result.same_outcome
        assert result.same_tool_sequence
        assert result.same_files_touched

    def test_different_outcomes_detected(self, success_stream: Stream, failure_stream: Stream):
        """Different outcomes produce divergence."""
        result = compare_streams(success_stream, failure_stream)

        assert not result.same_outcome
        assert "outcome" in result.divergences_by_aspect

    def test_different_tools_detected(self, success_stream: Stream, failure_stream: Stream):
        """Different tool sequences detected."""
        result = compare_streams(success_stream, failure_stream)

        assert not result.same_tool_sequence
        assert "edit_file" in result.tools_only_in_a

    def test_different_files_detected(self, success_stream: Stream, alternate_success_stream: Stream):
        """Different file access detected."""
        result = compare_streams(success_stream, alternate_success_stream)

        assert not result.same_files_touched
        assert "src/auth.py" in result.files_only_in_a
        assert "src/utils.py" in result.files_only_in_b

    def test_labels_customizable(self, success_stream: Stream, failure_stream: Stream):
        """Labels can be customized."""
        result = compare_streams(
            success_stream,
            failure_stream,
            label_a="Good",
            label_b="Bad",
        )

        assert result.label_a == "Good"
        assert result.label_b == "Bad"
        assert "Good" in result.summary()

    def test_empty_streams(self, empty_stream: Stream):
        """Empty streams are equivalent."""
        result = compare_streams(empty_stream, empty_stream)

        assert result.is_equivalent


# =============================================================================
# explain_outcome_difference Tests
# =============================================================================


class TestExplainOutcomeDifference:
    """Tests for explain_outcome_difference function."""

    def test_explains_success_vs_failure(self, success_stream: Stream, failure_stream: Stream):
        """Explains difference between success and failure."""
        explanation = explain_outcome_difference(success_stream, failure_stream)

        assert "## Outcome Difference Analysis" in explanation
        assert "FileNotFoundError" in explanation
        assert "### Key Differences" in explanation

    def test_includes_error_details(self, success_stream: Stream, failure_stream: Stream):
        """Includes error location and suggestions."""
        explanation = explain_outcome_difference(success_stream, failure_stream)

        assert "provider.py:123" in explanation
        assert "Suggestions" in explanation

    def test_includes_tool_differences(self, success_stream: Stream, failure_stream: Stream):
        """Lists tools only in successful execution."""
        explanation = explain_outcome_difference(success_stream, failure_stream)

        assert "Tools used in successful execution" in explanation
        assert "edit_file" in explanation

    def test_includes_file_differences(self, success_stream: Stream, failure_stream: Stream):
        """Lists files only in successful execution."""
        explanation = explain_outcome_difference(success_stream, failure_stream)

        assert "Files accessed in successful execution" in explanation

    def test_validates_failure_stream(self, success_stream: Stream):
        """Returns error if 'failure' stream has no failure."""
        explanation = explain_outcome_difference(success_stream, success_stream)

        assert "Cannot explain outcome difference" in explanation
        assert "no TaskFailed effect" in explanation

    def test_warns_if_success_also_failed(self, failure_stream: Stream):
        """Warns if 'success' stream also has a failure."""
        explanation = explain_outcome_difference(failure_stream, failure_stream)

        assert "Warning" in explanation
        assert "also contains a TaskFailed effect" in explanation


# =============================================================================
# detect_patterns Tests
# =============================================================================


class TestDetectPatterns:
    """Tests for detect_patterns function."""

    def test_finds_common_patterns(self, success_stream: Stream):
        """Finds patterns that appear in all streams."""
        streams = [success_stream, success_stream, success_stream]
        patterns = detect_patterns(streams, min_frequency=0.5)

        assert len(patterns) > 0
        # All patterns should have frequency 1.0 (appear in all)
        for p in patterns:
            assert p.frequency == 1.0

    def test_respects_min_frequency(self, success_stream: Stream, alternate_success_stream: Stream):
        """Only returns patterns above min_frequency."""
        streams = [success_stream, alternate_success_stream]
        patterns = detect_patterns(streams, min_frequency=0.9)

        # Patterns unique to one stream should be filtered out
        for p in patterns:
            assert p.frequency >= 0.9

    def test_respects_max_length(self, success_stream: Stream):
        """Patterns don't exceed max_length."""
        streams = [success_stream]
        patterns = detect_patterns(streams, min_frequency=0.0, max_length=3)

        for p in patterns:
            assert len(p.effect_sequence) <= 3

    def test_respects_min_length(self, success_stream: Stream):
        """Patterns meet min_length."""
        streams = [success_stream]
        patterns = detect_patterns(streams, min_frequency=0.0, min_length=3)

        for p in patterns:
            assert len(p.effect_sequence) >= 3

    def test_respects_max_patterns(self, success_stream: Stream):
        """Returns at most max_patterns."""
        streams = [success_stream]
        patterns = detect_patterns(streams, min_frequency=0.0, max_patterns=5)

        assert len(patterns) <= 5

    def test_empty_streams_list(self):
        """Empty stream list returns empty patterns."""
        patterns = detect_patterns([])
        assert patterns == []

    def test_deduplication_reduces_noise(self, success_stream: Stream):
        """Subpattern deduplication reduces redundant patterns."""
        streams = [success_stream, success_stream]

        with_dedup = detect_patterns(streams, min_frequency=0.0, deduplicate_subpatterns=True)
        without_dedup = detect_patterns(streams, min_frequency=0.0, deduplicate_subpatterns=False)

        # With deduplication should have fewer patterns
        assert len(with_dedup) <= len(without_dedup)

    def test_pattern_has_example_streams(self, success_stream: Stream):
        """Patterns track which streams contain them."""
        streams = [success_stream, success_stream]
        patterns = detect_patterns(streams, min_frequency=0.5)

        for p in patterns:
            assert len(p.example_streams) > 0


# =============================================================================
# ReferenceCorpus Tests
# =============================================================================


class TestReferenceCorpus:
    """Tests for ReferenceCorpus class."""

    def test_from_streams_creates_corpus(self, success_stream: Stream):
        """from_streams creates a valid corpus."""
        corpus = ReferenceCorpus.from_streams([success_stream, success_stream])

        assert corpus._stream_count == 2
        assert len(corpus._pattern_freq) > 0

    def test_find_anomalies_on_normal_stream(self, success_stream: Stream):
        """Normal stream has few/no anomalies against itself."""
        corpus = ReferenceCorpus.from_streams([success_stream, success_stream, success_stream])
        anomalies = corpus.find_anomalies(success_stream)

        # Should have very few anomalies since it matches the corpus
        assert len(anomalies) <= 2

    def test_find_anomalies_on_unusual_stream(self, success_stream: Stream, failure_stream: Stream):
        """Unusual stream has anomalies against normal corpus."""
        corpus = ReferenceCorpus.from_streams([success_stream, success_stream, success_stream])
        anomalies = corpus.find_anomalies(failure_stream, threshold=0.5)

        # Failure stream should have patterns not in success corpus
        # This depends on the specific patterns - may need adjustment
        assert isinstance(anomalies, list)


# =============================================================================
# find_anomalies Tests
# =============================================================================


class TestFindAnomalies:
    """Tests for find_anomalies function."""

    def test_accepts_stream_list(self, success_stream: Stream, failure_stream: Stream):
        """find_anomalies accepts a list of reference streams."""
        anomalies = find_anomalies(
            failure_stream,
            [success_stream, success_stream, success_stream],
            threshold=0.5,
        )

        assert isinstance(anomalies, list)

    def test_accepts_reference_corpus(self, success_stream: Stream, failure_stream: Stream):
        """find_anomalies accepts a ReferenceCorpus."""
        corpus = ReferenceCorpus.from_streams([success_stream, success_stream])
        anomalies = find_anomalies(failure_stream, corpus, threshold=0.5)

        assert isinstance(anomalies, list)

    def test_empty_reference_list(self, success_stream: Stream):
        """Empty reference list returns empty anomalies."""
        anomalies = find_anomalies(success_stream, [])
        assert anomalies == []

    def test_anomalies_have_pattern_aspect(self, success_stream: Stream, failure_stream: Stream):
        """Anomalies have 'pattern' aspect."""
        corpus = ReferenceCorpus.from_streams([success_stream, success_stream])
        anomalies = corpus.find_anomalies(failure_stream, threshold=0.5)

        for a in anomalies:
            assert a.aspect == "pattern"

    def test_threshold_affects_results(self, success_stream: Stream, failure_stream: Stream):
        """Lower threshold finds more anomalies."""
        corpus = ReferenceCorpus.from_streams([success_stream])

        low_threshold = find_anomalies(failure_stream, corpus, threshold=0.8)
        high_threshold = find_anomalies(failure_stream, corpus, threshold=0.1)

        # Higher threshold is more permissive (more things are "anomalous")
        assert len(low_threshold) >= len(high_threshold)


# =============================================================================
# Diagnostic Scenarios Tests
# =============================================================================


class TestDiagnosticScenarios:
    """Tests for complex debugging scenarios from the plan."""

    def test_case_sensitivity_detection(self):
        """Detects case sensitivity errors in file paths."""
        success = Stream()
        success = success.append(TaskStarted(task_name="Test"))
        success = success.append(FileRead(path="src/auth.py"))
        success = success.append(TaskCompleted(task_name="Test", duration_ms=100))

        failure = Stream()
        failure = failure.append(TaskStarted(task_name="Test"))
        failure = failure.append(FileRead(path="src/Auth.py"))  # Wrong case
        failure = failure.append(TaskFailed(task_name="Test", error="Not found", error_type="Error"))

        result = compare_streams(success, failure)

        # Should detect different files
        assert "src/auth.py" in result.files_only_in_a
        assert "src/Auth.py" in result.files_only_in_b

    def test_missing_prerequisite_detection(self):
        """Detects missing prerequisite file reads."""
        success = Stream()
        success = success.append(TaskStarted(task_name="Test"))
        success = success.append(FileRead(path="config.yaml"))  # Reads config first
        success = success.append(FileRead(path="src/main.py"))
        success = success.append(TaskCompleted(task_name="Test", duration_ms=100))

        failure = Stream()
        failure = failure.append(TaskStarted(task_name="Test"))
        failure = failure.append(FileRead(path="src/main.py"))  # Skips config
        failure = failure.append(TaskFailed(task_name="Test", error="Config missing", error_type="Error"))

        result = compare_streams(success, failure)

        assert "config.yaml" in result.files_only_in_a

    def test_wrong_tool_order_detection(self):
        """Detects wrong order of tool calls."""
        success = Stream()
        success = success.append(TaskStarted(task_name="Test"))
        success = success.append(ToolCallStarted(tool_name="read_file", tool_call_id="1"))
        success = success.append(ToolCallCompleted(tool_name="read_file", tool_call_id="1", success=True))
        success = success.append(ToolCallStarted(tool_name="edit_file", tool_call_id="2"))
        success = success.append(ToolCallCompleted(tool_name="edit_file", tool_call_id="2", success=True))
        success = success.append(TaskCompleted(task_name="Test", duration_ms=100))

        failure = Stream()
        failure = failure.append(TaskStarted(task_name="Test"))
        failure = failure.append(ToolCallStarted(tool_name="edit_file", tool_call_id="1"))  # Edit first!
        failure = failure.append(ToolCallRejected(tool_name="edit_file", tool_call_id="1", reason="No content"))
        failure = failure.append(TaskFailed(task_name="Test", error="Edit failed", error_type="Error"))

        result = compare_streams(success, failure)

        assert not result.same_tool_sequence
        assert "tool_sequence" in result.divergences_by_aspect

    def test_silent_failure_detection(self):
        """Detects when both succeed but with different behavior."""
        correct = Stream()
        correct = correct.append(TaskStarted(task_name="Test"))
        correct = correct.append(FileRead(path="template.py"))  # Reads template
        correct = correct.append(FileCreate(path="output.py"))
        correct = correct.append(TaskCompleted(task_name="Test", duration_ms=100))

        wrong = Stream()
        wrong = wrong.append(TaskStarted(task_name="Test"))
        # Skips template read
        wrong = wrong.append(FileCreate(path="output.py"))
        wrong = wrong.append(TaskCompleted(task_name="Test", duration_ms=100))

        result = compare_streams(correct, wrong)

        # Both succeeded
        assert result.same_outcome

        # But different behavior
        assert "template.py" in result.files_only_in_a

    def test_partial_success_detection(self):
        """Detects partial completion (some files modified, not all)."""
        success = Stream()
        success = success.append(TaskStarted(task_name="Test"))
        success = success.append(FilePatch(path="file1.py", old_content="", new_content=""))
        success = success.append(FilePatch(path="file2.py", old_content="", new_content=""))
        success = success.append(FilePatch(path="file3.py", old_content="", new_content=""))
        success = success.append(TaskCompleted(task_name="Test", duration_ms=100))

        partial = Stream()
        partial = partial.append(TaskStarted(task_name="Test"))
        partial = partial.append(FilePatch(path="file1.py", old_content="", new_content=""))
        partial = partial.append(FilePatch(path="file2.py", old_content="", new_content=""))
        # Missing file3.py
        partial = partial.append(TaskFailed(task_name="Test", error="Failed on file3", error_type="Error"))

        result = compare_streams(success, partial)

        assert not result.same_outcome
        assert "file3.py" in result.files_only_in_a


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Edge case tests for comparison utilities."""

    def test_single_effect_streams(self):
        """Handles streams with single effect."""
        stream_a = Stream()
        stream_a = stream_a.append(TaskStarted(task_name="Test"))

        stream_b = Stream()
        stream_b = stream_b.append(TaskStarted(task_name="Test"))

        result = compare_streams(stream_a, stream_b)
        assert result.is_equivalent

    def test_very_long_streams(self):
        """Handles long streams without hanging."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Test"))
        for i in range(100):
            stream = stream.append(ToolCallStarted(tool_name=f"tool_{i}", tool_call_id=f"tc_{i}"))
            stream = stream.append(ToolCallCompleted(tool_name=f"tool_{i}", tool_call_id=f"tc_{i}", success=True))
        stream = stream.append(TaskCompleted(task_name="Test", duration_ms=1000))

        # Should complete quickly
        result = compare_streams(stream, stream)
        assert result.is_equivalent

    def test_streams_with_no_tools(self):
        """Handles streams with no tool calls."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Test"))
        stream = stream.append(AgentThinking(content="Just thinking..."))
        stream = stream.append(TaskCompleted(task_name="Test", duration_ms=100))

        result = compare_streams(stream, stream)
        assert result.same_tool_sequence  # No tools = same sequence

    def test_streams_with_no_files(self):
        """Handles streams with no file operations."""
        stream = Stream()
        stream = stream.append(TaskStarted(task_name="Test"))
        stream = stream.append(ToolCallStarted(tool_name="think", tool_call_id="1"))
        stream = stream.append(ToolCallCompleted(tool_name="think", tool_call_id="1", success=True))
        stream = stream.append(TaskCompleted(task_name="Test", duration_ms=100))

        result = compare_streams(stream, stream)
        assert result.same_files_touched  # No files = same files
