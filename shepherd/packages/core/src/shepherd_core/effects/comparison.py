"""Comparison utilities for effect streams.

This module provides tools for comparing and analyzing effect streams:
- compare_streams(): Compare two streams and identify divergences
- explain_outcome_difference(): Explain why one execution succeeded and another failed
- detect_patterns(): Find recurring patterns across multiple streams
- find_anomalies(): Identify unusual patterns compared to a reference corpus

Example:
    # Compare success vs failure
    result = compare_streams(success.effects, failure.effects)
    print(result.summary())

    for d in result.critical_divergences:
        print(f"Critical: {d.description}")

    # Find patterns across executions
    patterns = detect_patterns(historical_streams)
    anomalies = find_anomalies(new_stream, historical_streams)
"""

from __future__ import annotations

import difflib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from shepherd_core.effects import Effect
    from shepherd_core.scope.stream import Stream


# =============================================================================
# SIGNIFICANCE THRESHOLDS AND WEIGHTS
# =============================================================================
#
# Divergence significance is a heuristic measure (0.0 to 1.0) indicating how
# likely this divergence is to explain behavioral differences.
#
# THRESHOLDS are used for filtering:
#   - CRITICAL_THRESHOLD (0.8): Used by `critical_divergences` property
#   - IMPORTANT_THRESHOLD (0.6): Used for "important" divergences
#   - Divergences >= threshold are considered "critical" or "important"
#
# Significance Scale:
#   0.8-1.0: CRITICAL - Likely root cause (filtered by `critical_divergences`)
#            Examples: outcome differences, errors, missing critical files
#
#   0.6-0.7: IMPORTANT - Significant behavioral change
#            Examples: different tool sequences, unexpected operations
#
#   0.4-0.5: MODERATE - Notable difference worth investigating
#            Examples: different file access patterns, modified vs read-only
#
#   0.2-0.3: MINOR - Probably not causal
#            Examples: timing differences, ordering variations
#
#   0.0-0.1: COSMETIC - Unlikely to matter
#            Examples: formatting, metadata differences
# =============================================================================

CRITICAL_THRESHOLD = 0.8
IMPORTANT_THRESHOLD = 0.6


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class ComparisonConfig:
    """Configuration for divergence significance weights.

    These weights determine how significant different types of divergences are.
    The resulting significance value is compared against CRITICAL_THRESHOLD
    to determine if a divergence is critical.

    Allows domain-specific tuning without forking the comparison code.

    Example:
        # For a domain where file access is more critical than tool order
        config = ComparisonConfig(
            tool_sequence_weight=0.5,
            file_access_exclusive_weight=0.8,
        )
        result = compare_streams(a, b, config=config)
    """

    outcome_weight: float = 0.9  # Outcome differences
    tool_sequence_weight: float = 0.7  # Tool sequence differences
    file_access_exclusive_weight: float = 0.5  # Files only in one stream
    file_access_operation_weight: float = 0.6  # Read vs write on same file


# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class Divergence:
    """A point where two effect streams diverged.

    Significance Heuristics:
    - 0.9-1.0: Critical - likely root cause (errors, missing critical files)
    - 0.7-0.8: Important - different tool sequences, unexpected operations
    - 0.5-0.6: Moderate - different file access patterns
    - 0.2-0.4: Minor - timing differences, ordering variations
    - 0.0-0.1: Cosmetic - formatting, metadata differences

    These are heuristics, not guarantees. Use `critical_divergences` property
    on ComparisonResult to filter to high-significance items.
    """

    aspect: Literal["tool_sequence", "file_access", "outcome", "timing", "error", "pattern"]
    description: str  # Human-readable explanation
    sequence_a: int | None = None  # Position in stream A
    sequence_b: int | None = None  # Position in stream B
    effect_a: Effect | None = None  # Effect from stream A
    effect_b: Effect | None = None  # Effect from stream B
    significance: float = 0.5  # 0.0 (minor) to 1.0 (critical)


@dataclass
class FileAccessComparison:
    """Result of comparing file access patterns between two streams.

    Provides detailed breakdown of file access differences:
    - files_only_in_a/b: Files accessed exclusively by one stream
    - files_modified_only_in_a/b: Files in both but modified only in one

    The latter is important for "silent failure" detection where both
    streams access a file but only one modifies it.
    """

    divergences: list[Divergence]
    files_only_in_a: list[str]  # Accessed exclusively in A
    files_only_in_b: list[str]  # Accessed exclusively in B
    files_modified_only_in_a: list[str] = field(default_factory=list)  # In both, modified only in A
    files_modified_only_in_b: list[str] = field(default_factory=list)  # In both, modified only in B


@dataclass
class ComparisonResult:
    """Result of comparing two effect streams.

    Usage:
        result = compare_streams(stream_a, stream_b)

        # Check for equivalence explicitly
        if not result.has_divergences:
            print("Streams are equivalent")

        # Check specific aspects
        if result.same_outcome and result.same_tool_sequence:
            print("Behaviorally equivalent")

        # Get critical issues
        for d in result.critical_divergences:
            print(f"Critical: {d.description}")

    Note: We intentionally don't implement __bool__ because "truthy comparison"
    is ambiguous (does True mean "same" or "found differences"?). Use explicit
    property checks instead.
    """

    stream_a: Stream
    stream_b: Stream
    label_a: str = "A"
    label_b: str = "B"

    # High-level summary
    same_outcome: bool = False  # Both succeeded or both failed
    same_tool_sequence: bool = False  # Same tools called in same order
    same_files_touched: bool = False  # Same files read/modified

    # Detailed divergences
    divergences: list[Divergence] = field(default_factory=list)

    # Aggregated differences
    tools_only_in_a: list[str] = field(default_factory=list)
    tools_only_in_b: list[str] = field(default_factory=list)
    files_only_in_a: list[str] = field(default_factory=list)
    files_only_in_b: list[str] = field(default_factory=list)

    @property
    def has_divergences(self) -> bool:
        """True if any divergences were found."""
        return len(self.divergences) > 0

    @property
    def is_equivalent(self) -> bool:
        """True if streams are behaviorally equivalent (no divergences)."""
        return not self.has_divergences

    @property
    def critical_divergences(self) -> list[Divergence]:
        """Divergences with significance >= CRITICAL_THRESHOLD (0.8)."""
        return [d for d in self.divergences if d.significance >= CRITICAL_THRESHOLD]

    @property
    def important_divergences(self) -> list[Divergence]:
        """Divergences with significance >= IMPORTANT_THRESHOLD (0.6)."""
        return [d for d in self.divergences if d.significance >= IMPORTANT_THRESHOLD]

    @property
    def divergences_by_aspect(self) -> dict[str, list[Divergence]]:
        """Group divergences by aspect type.

        Useful for checking if specific categories of divergences exist.

        Example:
            if "error" in comparison.divergences_by_aspect:
                print("Error-related divergences found")

            for aspect, divs in comparison.divergences_by_aspect.items():
                print(f"{aspect}: {len(divs)} divergences")
        """
        grouped: dict[str, list[Divergence]] = defaultdict(list)
        for d in self.divergences:
            grouped[d.aspect].append(d)
        return dict(grouped)

    def summary(self) -> str:
        """Human-readable comparison summary."""
        lines = [
            f"Comparison: {self.label_a} vs {self.label_b}",
            f"  Same outcome: {self.same_outcome}",
            f"  Same tool sequence: {self.same_tool_sequence}",
            f"  Same files touched: {self.same_files_touched}",
            f"  Divergences: {len(self.divergences)}",
        ]
        if self.tools_only_in_a:
            lines.append(f"  Tools only in {self.label_a}: {', '.join(self.tools_only_in_a)}")
        if self.tools_only_in_b:
            lines.append(f"  Tools only in {self.label_b}: {', '.join(self.tools_only_in_b)}")
        if self.files_only_in_a:
            lines.append(f"  Files only in {self.label_a}: {', '.join(self.files_only_in_a)}")
        if self.files_only_in_b:
            lines.append(f"  Files only in {self.label_b}: {', '.join(self.files_only_in_b)}")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        """Detailed markdown comparison report."""
        sections = [
            f"## Comparison: {self.label_a} vs {self.label_b}",
            "",
            "### Summary",
            "",
            "| Aspect | Same? |",
            "|--------|-------|",
            f"| Outcome | {'Yes' if self.same_outcome else 'No'} |",
            f"| Tool Sequence | {'Yes' if self.same_tool_sequence else 'No'} |",
            f"| Files Touched | {'Yes' if self.same_files_touched else 'No'} |",
            "",
        ]

        if self.divergences:
            sections.extend(
                [
                    "### Divergences",
                    "",
                    f"**Total**: {len(self.divergences)} ({len(self.critical_divergences)} critical)",
                    "",
                ]
            )

            if self.critical_divergences:
                sections.append("#### Critical")
                sections.append("")
                for d in self.critical_divergences:
                    sections.append(f"- **{d.aspect}**: {d.description}")
                sections.append("")

            non_critical = [d for d in self.divergences if d.significance < CRITICAL_THRESHOLD]
            if non_critical:
                sections.append("#### Other")
                sections.append("")
                for d in non_critical[:10]:  # Limit to 10
                    sections.append(f"- {d.aspect}: {d.description}")
                if len(non_critical) > 10:
                    sections.append(f"- *... {len(non_critical) - 10} more*")
                sections.append("")

        if self.tools_only_in_a or self.tools_only_in_b:
            sections.extend(
                [
                    "### Tool Differences",
                    "",
                ]
            )
            if self.tools_only_in_a:
                sections.append(f"**Only in {self.label_a}**: {', '.join(self.tools_only_in_a)}")
            if self.tools_only_in_b:
                sections.append(f"**Only in {self.label_b}**: {', '.join(self.tools_only_in_b)}")
            sections.append("")

        if self.files_only_in_a or self.files_only_in_b:
            sections.extend(
                [
                    "### File Differences",
                    "",
                ]
            )
            if self.files_only_in_a:
                sections.append(f"**Only in {self.label_a}**: {', '.join(self.files_only_in_a)}")
            if self.files_only_in_b:
                sections.append(f"**Only in {self.label_b}**: {', '.join(self.files_only_in_b)}")
            sections.append("")

        return "\n".join(sections)


# =============================================================================
# Pattern Detection
# =============================================================================


@dataclass
class EffectPattern:
    """A recurring pattern in effect streams."""

    name: str
    description: str
    effect_sequence: list[str]  # Effect type sequence
    frequency: float  # How often it appears (0.0 to 1.0)
    example_streams: list[int] = field(default_factory=list)  # Indices of streams containing this


@dataclass
class ReferenceCorpus:
    """Pre-computed patterns from a reference corpus for efficient anomaly detection.

    Use this when you need to check multiple streams against the same reference
    corpus. Patterns are computed once and reused.

    Example:
        # Compute patterns once
        corpus = ReferenceCorpus.from_streams(historical_streams)

        # Check multiple new streams efficiently
        for stream in new_streams:
            anomalies = corpus.find_anomalies(stream)
            if anomalies:
                print(f"Found {len(anomalies)} anomalies")
    """

    _pattern_freq: dict[tuple[str, ...], float] = field(default_factory=dict)
    _stream_count: int = 0

    @classmethod
    def from_streams(
        cls,
        streams: list[Stream],
        max_patterns: int = 10000,
        min_length: int = 2,
        max_length: int = 5,
    ) -> ReferenceCorpus:
        """Build a reference corpus from a list of streams.

        Args:
            streams: Reference streams representing "normal" behavior
            max_patterns: Maximum patterns to retain (bounds memory)
            min_length: Minimum pattern length
            max_length: Maximum pattern length

        Returns:
            ReferenceCorpus ready for anomaly detection
        """
        patterns = detect_patterns(
            streams,
            min_frequency=0.0,  # Get all patterns for frequency lookup
            min_length=min_length,
            max_length=max_length,
            max_patterns=max_patterns,
            deduplicate_subpatterns=False,  # Keep all for lookup
        )

        pattern_freq = {tuple(p.effect_sequence): p.frequency for p in patterns}

        return cls(_pattern_freq=pattern_freq, _stream_count=len(streams))

    def find_anomalies(
        self,
        stream: Stream,
        threshold: float = 0.1,
    ) -> list[Divergence]:
        """Find unusual patterns in a stream compared to this corpus.

        Args:
            stream: Stream to analyze
            threshold: Maximum frequency (0-1) for pattern to be anomalous

        Returns:
            List of anomalous patterns, deduplicated by position
        """
        return _find_anomalies_with_lookup(stream, self._pattern_freq, threshold)


# =============================================================================
# Comparison Functions
# =============================================================================


def _compare_outcomes(stream_a: Stream, stream_b: Stream) -> bool:
    """Check if both streams have the same success/failure outcome."""
    from shepherd_core.effects import TaskFailed

    a_failed = stream_a.first(TaskFailed) is not None
    b_failed = stream_b.first(TaskFailed) is not None
    return a_failed == b_failed


def compare_tool_sequences(
    stream_a: Stream,
    stream_b: Stream,
    config: ComparisonConfig | None = None,
) -> list[Divergence]:
    """Compare the sequence of tool calls between two streams.

    Uses difflib to find insertions, deletions, and replacements
    in the tool call sequence.
    """
    from shepherd_core.effects import ToolCallStarted

    cfg = config or ComparisonConfig()

    tools_a = [
        (layer.effect.tool_name, layer.sequence)
        for layer in stream_a.intents()
        if isinstance(layer.effect, ToolCallStarted)
    ]
    tools_b = [
        (layer.effect.tool_name, layer.sequence)
        for layer in stream_b.intents()
        if isinstance(layer.effect, ToolCallStarted)
    ]

    names_a = [t[0] for t in tools_a]
    names_b = [t[0] for t in tools_b]

    matcher = difflib.SequenceMatcher(None, names_a, names_b)

    divergences = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        if tag == "replace":
            desc = f"Different tools: {names_a[i1:i2]} vs {names_b[j1:j2]}"
        elif tag == "delete":
            desc = f"Tools only in A: {names_a[i1:i2]}"
        elif tag == "insert":
            desc = f"Tools only in B: {names_b[j1:j2]}"
        else:
            continue

        divergences.append(
            Divergence(
                aspect="tool_sequence",
                description=desc,
                sequence_a=tools_a[i1][1] if i1 < len(tools_a) else None,
                sequence_b=tools_b[j1][1] if j1 < len(tools_b) else None,
                significance=cfg.tool_sequence_weight,
            )
        )

    return divergences


def compare_file_access(
    stream_a: Stream,
    stream_b: Stream,
    config: ComparisonConfig | None = None,
) -> FileAccessComparison:
    """Compare file access patterns between two streams.

    Returns:
        FileAccessComparison with divergences and exclusive file lists
    """
    from shepherd_core.effects import FileCreate, FileDelete, FilePatch, FileRead

    cfg = config or ComparisonConfig()

    def extract_files(stream: Stream) -> tuple[set[str], set[str]]:
        read = set()
        modified = set()
        for layer in stream.outcomes():
            effect = layer.effect
            if isinstance(effect, FileRead):
                read.add(effect.path)
            elif isinstance(effect, (FilePatch, FileCreate, FileDelete)):
                modified.add(effect.path)
        return read, modified

    read_a, mod_a = extract_files(stream_a)
    read_b, mod_b = extract_files(stream_b)

    all_a = read_a | mod_a
    all_b = read_b | mod_b

    only_a = sorted(all_a - all_b)
    only_b = sorted(all_b - all_a)

    divergences = []

    if only_a:
        divergences.append(
            Divergence(
                aspect="file_access",
                description=f"Files only accessed in A: {only_a}",
                significance=cfg.file_access_exclusive_weight,
            )
        )

    if only_b:
        divergences.append(
            Divergence(
                aspect="file_access",
                description=f"Files only accessed in B: {only_b}",
                significance=cfg.file_access_exclusive_weight,
            )
        )

    # Check for different operations on same files
    common = all_a & all_b
    modified_only_in_a = []
    modified_only_in_b = []

    for path in sorted(common):
        in_mod_a = path in mod_a
        in_mod_b = path in mod_b
        if in_mod_a and not in_mod_b:
            modified_only_in_a.append(path)
            divergences.append(
                Divergence(
                    aspect="file_access",
                    description=f"{path}: modified in A, only read in B",
                    significance=cfg.file_access_operation_weight,
                )
            )
        elif in_mod_b and not in_mod_a:
            modified_only_in_b.append(path)
            divergences.append(
                Divergence(
                    aspect="file_access",
                    description=f"{path}: modified in B, only read in A",
                    significance=cfg.file_access_operation_weight,
                )
            )

    return FileAccessComparison(
        divergences=divergences,
        files_only_in_a=only_a,
        files_only_in_b=only_b,
        files_modified_only_in_a=modified_only_in_a,
        files_modified_only_in_b=modified_only_in_b,
    )


def compare_streams(
    stream_a: Stream,
    stream_b: Stream,
    *,
    label_a: str = "A",
    label_b: str = "B",
    config: ComparisonConfig | None = None,
) -> ComparisonResult:
    """Compare two effect streams and identify divergences.

    Analyzes:
    - Outcome (success/failure)
    - Tool call sequence
    - Files accessed
    - Error patterns

    Args:
        stream_a: First stream to compare
        stream_b: Second stream to compare
        label_a: Label for first stream in output
        label_b: Label for second stream in output
        config: Optional configuration for significance weights

    Returns:
        ComparisonResult with detailed divergence analysis

    Example:
        >>> result = compare_streams(success.effects, failure.effects)
        >>> print(result.summary())
        >>> for d in result.critical_divergences:
        ...     print(f"  {d.aspect}: {d.description}")
    """
    from shepherd_core.effects import ToolCallStarted

    cfg = config or ComparisonConfig()

    result = ComparisonResult(
        stream_a=stream_a,
        stream_b=stream_b,
        label_a=label_a,
        label_b=label_b,
    )

    # Check outcomes
    result.same_outcome = _compare_outcomes(stream_a, stream_b)

    if not result.same_outcome:
        result.divergences.append(
            Divergence(
                aspect="outcome",
                description=f"{label_a} and {label_b} have different outcomes",
                significance=cfg.outcome_weight,
            )
        )

    # Check tool sequences
    tool_divergences = compare_tool_sequences(stream_a, stream_b, cfg)
    result.same_tool_sequence = len(tool_divergences) == 0
    result.divergences.extend(tool_divergences)

    # Check file access
    file_comparison = compare_file_access(stream_a, stream_b, cfg)
    result.same_files_touched = len(file_comparison.divergences) == 0
    result.divergences.extend(file_comparison.divergences)
    result.files_only_in_a = file_comparison.files_only_in_a
    result.files_only_in_b = file_comparison.files_only_in_b

    # Compute tool differences
    tools_a = {layer.effect.tool_name for layer in stream_a.intents() if isinstance(layer.effect, ToolCallStarted)}
    tools_b = {layer.effect.tool_name for layer in stream_b.intents() if isinstance(layer.effect, ToolCallStarted)}
    result.tools_only_in_a = sorted(tools_a - tools_b)
    result.tools_only_in_b = sorted(tools_b - tools_a)

    return result


def explain_outcome_difference(
    stream_success: Stream,
    stream_failure: Stream,
) -> str:
    """Explain why one execution succeeded and another failed.

    Analyzes the failing stream to identify:
    - Where it diverged from the successful path
    - What error occurred
    - What the successful execution did differently

    Args:
        stream_success: The successful execution's effect stream
        stream_failure: The failed execution's effect stream

    Returns:
        Human-readable explanation of the difference.
        Returns an error message if streams don't match expected pattern.

    Note:
        This function validates that stream_success actually succeeded and
        stream_failure actually failed. For comparing two arbitrary streams,
        use compare_streams() instead.
    """
    from shepherd_core.effects import TaskFailed

    # Validate assumptions about stream outcomes
    success_has_failure = stream_success.first(TaskFailed) is not None
    failure_has_failure = stream_failure.first(TaskFailed) is not None

    if not failure_has_failure:
        return (
            "Cannot explain outcome difference: 'stream_failure' has no TaskFailed effect. "
            "Use compare_streams() for general comparison."
        )

    if success_has_failure:
        return (
            "Warning: 'stream_success' also contains a TaskFailed effect. "
            "Results may be misleading. Use compare_streams() for general comparison."
        )

    # Find the failure
    failure_layer = stream_failure.last(TaskFailed)
    if failure_layer is None:
        return "Cannot explain: no TaskFailed effect found."

    failed_effect = failure_layer.effect

    # Compare up to failure point
    comparison = compare_streams(
        stream_success,
        stream_failure,
        label_a="Success",
        label_b="Failure",
    )

    # Extract failure details with defensive checks
    error_type = getattr(failed_effect, "error_type", "Unknown")
    error_msg = getattr(failed_effect, "error", "No error message")
    error_location = getattr(failed_effect, "error_location", None)
    suggestions = getattr(failed_effect, "suggestions", None)

    lines = [
        "## Outcome Difference Analysis",
        "",
        f"**Failure**: {error_type}: {error_msg}",
    ]

    if error_location:
        lines.append(f"**Location**: {error_location}")

    if suggestions:
        lines.append(f"**Suggestions**: {', '.join(suggestions)}")

    lines.extend(["", "### Key Differences", ""])

    if comparison.critical_divergences:
        for d in comparison.critical_divergences:
            lines.append(f"- **{d.aspect}**: {d.description}")
    elif comparison.divergences:
        for d in comparison.divergences[:5]:  # Top 5
            lines.append(f"- {d.aspect}: {d.description}")
    else:
        lines.append("- No significant divergences detected before failure")

    # What did success do that failure didn't?
    if comparison.tools_only_in_a:
        lines.extend(
            [
                "",
                "### Tools used in successful execution but not in failed:",
                *[f"- {t}" for t in comparison.tools_only_in_a],
            ]
        )

    if comparison.files_only_in_a:
        lines.extend(
            [
                "",
                "### Files accessed in successful execution but not in failed:",
                *[f"- {f}" for f in comparison.files_only_in_a],
            ]
        )

    return "\n".join(lines)


# =============================================================================
# Pattern Detection
# =============================================================================


def _is_subpattern(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    """Check if short is a contiguous subpattern of long."""
    if len(short) >= len(long):
        return False
    short_str = "|||".join(short)
    long_str = "|||".join(long)
    return short_str in long_str


def _deduplicate_subpatterns(patterns: list[EffectPattern]) -> list[EffectPattern]:
    """Filter out patterns that are strict subpatterns of equally/more frequent longer patterns.

    If pattern [A, B, C] appears in 80% of streams, [A, B] will also appear in at least 80%.
    This creates noise in the output. We filter subpatterns that are dominated by longer patterns.

    A pattern is "dominated" if there exists a longer pattern with >= frequency that contains it.
    """
    filtered = []
    for pattern in patterns:
        seq = tuple(pattern.effect_sequence)
        dominated = False

        for other in patterns:
            other_seq = tuple(other.effect_sequence)
            if len(other_seq) > len(seq) and other.frequency >= pattern.frequency and _is_subpattern(seq, other_seq):
                dominated = True
                break

        if not dominated:
            filtered.append(pattern)

    return filtered


def detect_patterns(
    streams: list[Stream],
    min_frequency: float = 0.3,
    min_length: int = 2,
    max_length: int = 5,
    max_patterns: int = 50,
    deduplicate_subpatterns: bool = True,
) -> list[EffectPattern]:
    """Detect recurring patterns across multiple effect streams.

    Finds common subsequences that appear frequently,
    useful for understanding "what good executions do."

    **Complexity Note**: This uses a sliding window approach with O(N * M * L)
    complexity where N = number of streams, M = avg stream length, L = max_length.
    For large corpora, keep max_length low (default 5) and use max_patterns to
    limit output.

    **Performance guidance** (validated via spike):
    - 100 streams x 50 effects x max_length=5: ~4ms
    - 1000 streams x 100 effects x max_length=5: ~800ms
    - 10000 streams: consider using ReferenceCorpus for caching

    Args:
        streams: List of effect streams to analyze
        min_frequency: Minimum frequency (0-1) for pattern to be reported
        min_length: Minimum pattern length
        max_length: Maximum pattern length (default 5 for performance)
        max_patterns: Maximum patterns to return (default 50)
        deduplicate_subpatterns: If True (default), filters out patterns that are
            strict subpatterns of equally/more frequent longer patterns. This reduces
            noise: if [A,B,C] appears 80%, [A,B] will also appear >=80%, which is
            redundant information. Set False to see all patterns.

    Returns:
        List of detected patterns, sorted by frequency (up to max_patterns)
    """
    if not streams:
        return []

    # Extract effect type sequences
    sequences = []
    for stream in streams:
        seq = [layer.effect.effect_type for layer in stream]
        sequences.append(seq)

    # Find common subsequences using sliding window
    pattern_counts: Counter[tuple[str, ...]] = Counter()
    pattern_locations: dict[tuple[str, ...], list[int]] = {}

    for stream_idx, seq in enumerate(sequences):
        seen_in_stream: set[tuple[str, ...]] = set()

        for length in range(min_length, min(max_length + 1, len(seq) + 1)):
            for i in range(len(seq) - length + 1):
                subseq = tuple(seq[i : i + length])
                if subseq not in seen_in_stream:
                    seen_in_stream.add(subseq)
                    pattern_counts[subseq] += 1
                    if subseq not in pattern_locations:
                        pattern_locations[subseq] = []
                    pattern_locations[subseq].append(stream_idx)

    # Filter by frequency and build results
    total = len(streams)
    patterns = []  # type: ignore[var-annotated]

    for subseq, count in pattern_counts.items():
        freq = count / total
        if freq >= min_frequency:
            patterns.append(
                EffectPattern(
                    name=f"Pattern_{len(patterns) + 1}",
                    description=f"Sequence: {' -> '.join(subseq)}",
                    effect_sequence=list(subseq),
                    frequency=freq,
                    example_streams=pattern_locations[subseq],
                )
            )

    # Sort by frequency descending, then by length descending
    patterns.sort(key=lambda p: (-p.frequency, -len(p.effect_sequence)))

    # Deduplicate subpatterns if requested (reduces noise significantly)
    if deduplicate_subpatterns:
        patterns = _deduplicate_subpatterns(patterns)

    # Respect max_patterns limit
    return patterns[:max_patterns]


def _find_anomalies_with_lookup(
    stream: Stream,
    pattern_freq: dict[tuple[str, ...], float],
    threshold: float,
) -> list[Divergence]:
    """Core anomaly detection logic using pre-computed frequency lookup.

    Deduplicates overlapping anomalies by keeping the longest pattern
    at each position.
    """
    seq = [layer.effect.effect_type for layer in stream]
    if not seq:
        return []

    # Track best (longest) anomaly starting at each position
    best_at_position: dict[int, Divergence] = {}

    for length in range(2, min(6, len(seq) + 1)):
        for i in range(len(seq) - length + 1):
            subseq = tuple(seq[i : i + length])
            freq = pattern_freq.get(subseq, 0.0)

            if freq < threshold:
                # Only keep if longer than existing anomaly at this position
                existing = best_at_position.get(i)
                if existing is None or length > len(existing.description.split(" -> ")):
                    best_at_position[i] = Divergence(
                        aspect="pattern",
                        description=f"Rare sequence (freq={freq:.1%}): {' -> '.join(subseq)}",
                        sequence_a=i,
                        significance=1.0 - freq,
                    )

    return list(best_at_position.values())


def find_anomalies(
    stream: Stream,
    reference: list[Stream] | ReferenceCorpus,
    threshold: float = 0.1,
    max_reference_patterns: int = 10000,
) -> list[Divergence]:
    """Find unusual patterns in a stream compared to references.

    Identifies effect sequences that rarely appear in the
    reference corpus - potential bugs or edge cases.

    Args:
        stream: Stream to analyze
        reference: Either a list of reference streams or a pre-computed ReferenceCorpus.
            Use ReferenceCorpus when checking multiple streams against the same reference.
        threshold: Maximum frequency (0-1) for something to be anomalous
        max_reference_patterns: Maximum patterns to extract (only used if reference is a list)

    Returns:
        List of anomalous patterns found, deduplicated by position

    Example:
        # One-off check
        anomalies = find_anomalies(stream, historical_streams)

        # Repeated checks (more efficient)
        corpus = ReferenceCorpus.from_streams(historical_streams)
        for stream in new_streams:
            anomalies = find_anomalies(stream, corpus)
    """
    if isinstance(reference, ReferenceCorpus):
        return reference.find_anomalies(stream, threshold)

    # Empty reference list = no baseline to compare against
    if not reference:
        return []

    # Build corpus on the fly for one-off use
    corpus = ReferenceCorpus.from_streams(
        reference,
        max_patterns=max_reference_patterns,
    )
    return corpus.find_anomalies(stream, threshold)


__all__ = [
    # Constants
    "CRITICAL_THRESHOLD",
    "IMPORTANT_THRESHOLD",
    # Configuration
    "ComparisonConfig",
    "ComparisonResult",
    # Data structures
    "Divergence",
    "EffectPattern",
    "FileAccessComparison",
    "ReferenceCorpus",
    "compare_file_access",
    # Comparison functions
    "compare_streams",
    "compare_tool_sequences",
    # Pattern detection
    "detect_patterns",
    "explain_outcome_difference",
    "find_anomalies",
]
