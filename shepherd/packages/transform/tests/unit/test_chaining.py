"""Unit tests for transformation chaining."""

from __future__ import annotations

import pytest
from shepherd_transform.chaining import (
    ChainResult,
    TransformationEngine,
    TransformationResult,
    TransformSpec,
    calculate_chain_confidence,
    estimate_safe_depth,
)

# =============================================================================
# Test Data
# =============================================================================


INITIAL_SOURCE = '''
@task
class TestTask(BaseModel):
    """A test task."""
    x: Input(int)
    result: Output(int)
'''


def mock_transform_success(source: str, description: str) -> tuple[str, float]:
    """Mock transform that always succeeds."""
    return f"# {description}\n{source}", 0.9


def mock_transform_fail(source: str, description: str) -> tuple[str, float]:
    """Mock transform that always fails."""
    raise ValueError("Transform failed")


def mock_transform_low_confidence(source: str, description: str) -> tuple[str, float]:
    """Mock transform with low confidence."""
    return f"# {description}\n{source}", 0.5


# =============================================================================
# TransformSpec Tests
# =============================================================================


class TestTransformSpec:
    """Tests for TransformSpec dataclass."""

    def test_create_spec(self):
        """Test creating a transform spec."""
        spec = TransformSpec(description="Add logging")
        assert spec.description == "Add logging"
        assert spec.expected_confidence == 0.9
        assert spec.metadata == {}

    def test_create_spec_with_confidence(self):
        """Test creating spec with custom confidence."""
        spec = TransformSpec(description="Add validation", expected_confidence=0.95)
        assert spec.expected_confidence == 0.95

    def test_create_spec_with_metadata(self):
        """Test creating spec with metadata."""
        spec = TransformSpec(
            description="Refactor",
            metadata={"author": "test", "version": 2},
        )
        assert spec.metadata["author"] == "test"
        assert spec.metadata["version"] == 2


# =============================================================================
# ChainResult Tests
# =============================================================================


class TestChainResult:
    """Tests for ChainResult dataclass."""

    def test_chain_depth(self):
        """Test chain_depth property."""
        result = ChainResult(
            chain_id="test",
            transformations=[
                TransformationResult(
                    spec=TransformSpec("t1"),
                    success=True,
                    input_source="",
                    output_source="",
                    grounding_confidence=0.9,
                ),
                TransformationResult(
                    spec=TransformSpec("t2"),
                    success=True,
                    input_source="",
                    output_source="",
                    grounding_confidence=0.9,
                ),
            ],
            final_source="",
            final_class=None,
            overall_confidence=0.8,
            success=True,
        )
        assert result.chain_depth == 2

    def test_successful_transforms(self):
        """Test successful_transforms property."""
        result = ChainResult(
            chain_id="test",
            transformations=[
                TransformationResult(
                    spec=TransformSpec("t1"),
                    success=True,
                    input_source="",
                    output_source="",
                    grounding_confidence=0.9,
                ),
                TransformationResult(
                    spec=TransformSpec("t2"),
                    success=False,
                    input_source="",
                    output_source="",
                    grounding_confidence=0.0,
                    error="failed",
                ),
            ],
            final_source="",
            final_class=None,
            overall_confidence=0.5,
            success=False,
        )
        assert result.successful_transforms == 1

    def test_provenance(self):
        """Test provenance property."""
        result = ChainResult(
            chain_id="test",
            transformations=[
                TransformationResult(
                    spec=TransformSpec("Add logging"),
                    success=True,
                    input_source="",
                    output_source="",
                    grounding_confidence=0.9,
                ),
                TransformationResult(
                    spec=TransformSpec("Add validation"),
                    success=True,
                    input_source="",
                    output_source="",
                    grounding_confidence=0.9,
                ),
            ],
            final_source="",
            final_class=None,
            overall_confidence=0.8,
            success=True,
        )
        assert result.provenance == ["Add logging", "Add validation"]


# =============================================================================
# TransformationEngine Tests
# =============================================================================


class TestTransformationEngine:
    """Tests for TransformationEngine."""

    def test_create_engine_defaults(self):
        """Test creating engine with defaults."""
        engine = TransformationEngine()
        assert engine.confidence_decay == 0.95
        assert engine.min_confidence == 0.5
        assert engine.max_depth == 7

    def test_create_engine_custom(self):
        """Test creating engine with custom parameters."""
        engine = TransformationEngine(
            confidence_decay=0.9,
            min_confidence=0.6,
            max_depth=5,
        )
        assert engine.confidence_decay == 0.9
        assert engine.min_confidence == 0.6
        assert engine.max_depth == 5

    def test_invalid_decay(self):
        """Test invalid confidence_decay raises error."""
        with pytest.raises(ValueError, match="confidence_decay"):
            TransformationEngine(confidence_decay=1.5)
        with pytest.raises(ValueError, match="confidence_decay"):
            TransformationEngine(confidence_decay=0)

    def test_invalid_min_confidence(self):
        """Test invalid min_confidence raises error."""
        with pytest.raises(ValueError, match="min_confidence"):
            TransformationEngine(min_confidence=-0.1)
        with pytest.raises(ValueError, match="min_confidence"):
            TransformationEngine(min_confidence=1.5)

    def test_invalid_max_depth(self):
        """Test invalid max_depth raises error."""
        with pytest.raises(ValueError, match="max_depth"):
            TransformationEngine(max_depth=0)


class TestApplyChain:
    """Tests for apply_chain method."""

    def test_single_transform(self):
        """Test single transformation."""
        engine = TransformationEngine()
        specs = [TransformSpec("Add logging")]

        result = engine.apply_chain(
            INITIAL_SOURCE,
            specs,
            mock_transform_success,
            reconstruct_final=False,
        )

        assert result.success
        assert result.chain_depth == 1
        assert result.successful_transforms == 1
        assert "# Add logging" in result.final_source

    def test_multiple_transforms(self):
        """Test multiple transformations."""
        engine = TransformationEngine()
        specs = [
            TransformSpec("Add logging"),
            TransformSpec("Add validation"),
            TransformSpec("Add metrics"),
        ]

        result = engine.apply_chain(
            INITIAL_SOURCE,
            specs,
            mock_transform_success,
            reconstruct_final=False,
        )

        assert result.success
        assert result.chain_depth == 3
        assert result.successful_transforms == 3
        assert len(result.checkpoints) == 4  # Initial + 3 transforms

    def test_confidence_degradation(self):
        """Test confidence degrades across chain."""
        engine = TransformationEngine(confidence_decay=0.95)
        specs = [
            TransformSpec("t1"),
            TransformSpec("t2"),
            TransformSpec("t3"),
        ]

        result = engine.apply_chain(
            INITIAL_SOURCE,
            specs,
            mock_transform_success,
            reconstruct_final=False,
        )

        # Expected: (0.9 * 0.95) ** 3 ≈ 0.625
        expected = (0.9 * 0.95) ** 3
        assert abs(result.overall_confidence - expected) < 0.01

    def test_stop_on_failure(self):
        """Test chain stops on failure."""
        engine = TransformationEngine()
        specs = [
            TransformSpec("t1"),
            TransformSpec("t2 - will fail"),
            TransformSpec("t3 - should not run"),
        ]

        call_count = 0

        def counting_transform(source: str, description: str) -> tuple[str, float]:
            nonlocal call_count
            call_count += 1
            if "fail" in description:
                raise ValueError("Failed")
            return f"# {description}\n{source}", 0.9

        result = engine.apply_chain(
            INITIAL_SOURCE,
            specs,
            counting_transform,
            stop_on_failure=True,
            reconstruct_final=False,
        )

        assert not result.success
        assert result.stopped_at == 1
        assert call_count == 2  # t1 and t2, not t3

    def test_max_depth_limit(self):
        """Test chain stops at max depth."""
        engine = TransformationEngine(max_depth=2)
        specs = [
            TransformSpec("t1"),
            TransformSpec("t2"),
            TransformSpec("t3"),
            TransformSpec("t4"),
        ]

        result = engine.apply_chain(
            INITIAL_SOURCE,
            specs,
            mock_transform_success,
            reconstruct_final=False,
        )

        assert not result.success
        assert result.stopped_at == 2
        assert "max_depth" in result.stop_reason

    def test_confidence_threshold_stop(self):
        """Test chain stops when confidence drops below threshold."""
        engine = TransformationEngine(min_confidence=0.7)
        specs = [TransformSpec(f"t{i}") for i in range(10)]

        result = engine.apply_chain(
            INITIAL_SOURCE,
            specs,
            mock_transform_success,
            reconstruct_final=False,
        )

        assert not result.success
        assert "confidence" in result.stop_reason.lower()

    def test_checkpoints(self):
        """Test checkpoints are recorded."""
        engine = TransformationEngine()
        specs = [
            TransformSpec("t1"),
            TransformSpec("t2"),
        ]

        result = engine.apply_chain(
            INITIAL_SOURCE,
            specs,
            mock_transform_success,
            reconstruct_final=False,
        )

        assert len(result.checkpoints) == 3
        assert result.checkpoints[0] == INITIAL_SOURCE
        assert "# t1" in result.checkpoints[1]
        assert "# t2" in result.checkpoints[2]


class TestApplyChainWithRollback:
    """Tests for apply_chain_with_rollback method."""

    def test_rollback_on_failure(self):
        """Test rollback restores last good state."""
        engine = TransformationEngine()
        specs = [
            TransformSpec("t1"),
            TransformSpec("t2 - will fail"),
        ]

        call_count = 0

        def failing_transform(source: str, description: str) -> tuple[str, float]:
            nonlocal call_count
            call_count += 1
            if "fail" in description:
                raise ValueError("Failed")
            return f"# {description}\n{source}", 0.9

        result = engine.apply_chain_with_rollback(
            INITIAL_SOURCE,
            specs,
            failing_transform,
        )

        assert not result.success
        assert "rolled back" in result.stop_reason
        # Final source should be after t1, not the failed t2
        assert "# t1" in result.final_source
        assert "# t2" not in result.final_source

    def test_rollback_preserves_confidence(self):
        """Test rollback restores confidence to last checkpoint."""
        engine = TransformationEngine(confidence_decay=0.95)
        specs = [
            TransformSpec("t1"),
            TransformSpec("t2 - will fail"),
        ]

        def failing_transform(source: str, description: str) -> tuple[str, float]:
            if "fail" in description:
                raise ValueError("Failed")
            return f"# {description}\n{source}", 0.9

        result = engine.apply_chain_with_rollback(
            INITIAL_SOURCE,
            specs,
            failing_transform,
        )

        # Confidence should be at t1 level, not degraded by failure
        expected = 0.9 * 0.95
        assert abs(result.overall_confidence - expected) < 0.01

    def test_test_fn_validation(self):
        """Test that test_fn can reject transforms."""
        engine = TransformationEngine()
        specs = [
            TransformSpec("t1"),
            TransformSpec("t2"),
        ]

        def test_fn(source: str) -> bool:
            return "# t2" not in source  # Reject t2

        result = engine.apply_chain_with_rollback(
            INITIAL_SOURCE,
            specs,
            mock_transform_success,
            test_fn=test_fn,
        )

        assert not result.success
        assert result.stopped_at == 1


# =============================================================================
# Utility Function Tests
# =============================================================================


class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_calculate_chain_confidence(self):
        """Test calculate_chain_confidence."""
        scores = [0.9, 0.9, 0.9]
        result = calculate_chain_confidence(scores, decay=0.95)

        expected = (0.9 * 0.95) ** 3
        assert abs(result - expected) < 0.001

    def test_calculate_chain_confidence_empty(self):
        """Test with empty scores."""
        result = calculate_chain_confidence([], decay=0.95)
        assert result == 1.0

    def test_estimate_safe_depth(self):
        """Test estimate_safe_depth."""
        depth = estimate_safe_depth(
            expected_grounding=0.9,
            decay=0.95,
            min_confidence=0.5,
        )

        # With 0.9 * 0.95 = 0.855 per step:
        # Depth 1: 0.855
        # Depth 2: 0.731
        # Depth 3: 0.625
        # Depth 4: 0.534
        # Depth 5: 0.457 < 0.5
        assert depth == 4

    def test_estimate_safe_depth_high_threshold(self):
        """Test with high confidence threshold."""
        depth = estimate_safe_depth(
            expected_grounding=0.9,
            decay=0.95,
            min_confidence=0.8,
        )
        # Depth 1: 0.855 > 0.8
        # Depth 2: 0.731 < 0.8
        assert depth == 1

    def test_estimate_safe_depth_low_threshold(self):
        """Test with low confidence threshold."""
        depth = estimate_safe_depth(
            expected_grounding=0.9,
            decay=0.95,
            min_confidence=0.2,
        )
        # Should allow deeper chains
        assert depth >= 7
