"""Tests for OptimizeFromEffects meta-task."""

from __future__ import annotations

import typing

import pytest
from shepherd_runtime.task.markers import CompletedTask, TaskRef
from shepherd_runtime.task.metadata import extract_task_metadata
from shepherd_transform.meta import OptimizeFromEffects


class TestOptimizeFromEffectsDefinition:
    """Test OptimizeFromEffects class structure and metadata."""

    def test_is_task_decorated(self):
        """OptimizeFromEffects should be decorated with @task."""
        assert hasattr(OptimizeFromEffects, "_task_meta")

    def test_has_executions_input(self):
        """OptimizeFromEffects should have an executions input of type list[CompletedTask]."""
        meta = extract_task_metadata(OptimizeFromEffects)
        assert "executions" in meta.inputs
        origin = typing.get_origin(meta.inputs["executions"].inner_type)
        assert origin is list
        args = typing.get_args(meta.inputs["executions"].inner_type)
        assert args
        assert args[0] is CompletedTask

    def test_has_feedback_input(self, completed_calculator):
        """OptimizeFromEffects should have feedback input with default."""
        instance = OptimizeFromEffects(
            executions=[completed_calculator],
        )
        assert instance.feedback == ""

    def test_has_optimization_goals_input(self, completed_calculator):
        """OptimizeFromEffects should have optimization_goals input with defaults."""
        meta = extract_task_metadata(OptimizeFromEffects)
        assert "optimization_goals" in meta.inputs
        instance = OptimizeFromEffects(
            executions=[completed_calculator],
        )
        assert instance.optimization_goals == ["efficiency", "reliability"]

    def test_has_optimized_output(self):
        """OptimizeFromEffects should have optimized output of type TaskRef."""
        meta = extract_task_metadata(OptimizeFromEffects)
        assert "optimized" in meta.outputs
        inner = meta.outputs["optimized"].inner_type
        args = typing.get_args(inner)
        non_none = [a for a in args if a is not type(None)]
        assert TaskRef in non_none

    def test_has_optimized_source_output(self):
        """OptimizeFromEffects should have optimized_source output."""
        meta = extract_task_metadata(OptimizeFromEffects)
        assert "optimized_source" in meta.outputs
        inner = meta.outputs["optimized_source"].inner_type
        args = typing.get_args(inner)
        non_none = [a for a in args if a is not type(None)]
        assert str in non_none

    def test_has_changes_made_output(self):
        """OptimizeFromEffects should have changes_made output."""
        meta = extract_task_metadata(OptimizeFromEffects)
        assert "changes_made" in meta.outputs
        inner = meta.outputs["changes_made"].inner_type
        args = typing.get_args(inner)
        non_none = [a for a in args if a is not type(None)]
        assert len(non_none) == 1
        origin = typing.get_origin(non_none[0])
        assert origin is list

    def test_has_expected_improvement_output(self):
        """OptimizeFromEffects should have expected_improvement output."""
        meta = extract_task_metadata(OptimizeFromEffects)
        assert "expected_improvement" in meta.outputs
        inner = meta.outputs["expected_improvement"].inner_type
        args = typing.get_args(inner)
        non_none = [a for a in args if a is not type(None)]
        assert str in non_none


class TestOptimizeFromEffectsInstantiation:
    """Test OptimizeFromEffects can be instantiated with various inputs."""

    def test_instantiate_with_minimal_inputs(self, completed_calculator):
        """OptimizeFromEffects can be instantiated with minimal inputs."""
        instance = OptimizeFromEffects(
            executions=[completed_calculator],
        )
        assert len(instance.executions) == 1

    def test_instantiate_with_feedback(self, completed_calculator):
        """OptimizeFromEffects can be instantiated with feedback."""
        instance = OptimizeFromEffects(
            executions=[completed_calculator],
            feedback="Task is slow on large inputs",
        )
        assert instance.feedback == "Task is slow on large inputs"

    def test_instantiate_with_custom_goals(self, completed_calculator):
        """OptimizeFromEffects can be instantiated with custom goals."""
        instance = OptimizeFromEffects(
            executions=[completed_calculator],
            optimization_goals=["performance", "clarity"],
        )
        assert instance.optimization_goals == ["performance", "clarity"]

    def test_instantiate_with_multiple_executions(self, completed_calculator):
        """OptimizeFromEffects can be instantiated with multiple executions."""
        from .conftest import SimpleCalculator

        run2 = SimpleCalculator(a=100, b=200)
        run3 = SimpleCalculator(a=-5, b=10)
        instance = OptimizeFromEffects(
            executions=[completed_calculator, run2, run3],
        )
        assert len(instance.executions) == 3

    def test_outputs_are_populated_in_mock_mode(self, completed_calculator):
        """Output fields should be populated with mock values in mock mode."""
        instance = OptimizeFromEffects(
            executions=[completed_calculator],
        )
        assert instance.optimized is not None
        assert instance.optimized_source is not None
        assert instance.changes_made is not None
        assert instance.expected_improvement is not None


class TestOptimizeFromEffectsVerifyTransformation:
    """Test OptimizeFromEffects.verify_transformation() method."""

    def test_verify_transformation_raises_without_optimized(self, completed_calculator):
        """verify_transformation raises ValueError if no optimized task."""
        from .conftest import SimpleCalculator

        instance = OptimizeFromEffects(
            executions=[completed_calculator],
        )
        # Mock mode populates outputs, so manually clear for this test
        instance.optimized = None
        with pytest.raises(ValueError, match="No optimized task available"):
            instance.verify_transformation(SimpleCalculator)

    def test_verify_transformation_signature(self):
        """verify_transformation has the expected signature."""
        import inspect

        sig = inspect.signature(OptimizeFromEffects.verify_transformation)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "original_class" in params
        assert "test_cases" in params
        assert "equivalence" in params

    def test_verify_transformation_with_mock_optimized(self, completed_calculator):
        """verify_transformation works when optimized is set manually."""
        from .conftest import SimpleCalculator

        instance = OptimizeFromEffects(
            executions=[completed_calculator],
        )
        instance.optimized = SimpleCalculator

        result = instance.verify_transformation(SimpleCalculator)
        assert result.passed
        assert result.test_count > 0

    def test_verify_transformation_with_custom_test_cases(self, completed_calculator):
        """verify_transformation accepts custom test cases."""
        from .conftest import SimpleCalculator

        instance = OptimizeFromEffects(
            executions=[completed_calculator],
        )
        instance.optimized = SimpleCalculator

        custom_cases = [
            {"a": 100, "b": 200},
            {"a": -5, "b": 5},
        ]
        result = instance.verify_transformation(
            SimpleCalculator,
            test_cases=custom_cases,
        )
        assert result.passed
        assert result.test_count == 2


class TestOptimizeFromEffectsUseCases:
    """Test OptimizeFromEffects with various optimization scenarios."""

    def test_optimize_for_efficiency(self, completed_calculator):
        """Can express efficiency optimization."""
        instance = OptimizeFromEffects(
            executions=[completed_calculator],
            optimization_goals=["efficiency"],
            feedback="Unnecessary allocations observed",
        )
        assert "efficiency" in instance.optimization_goals

    def test_optimize_for_reliability(self, completed_text_processor):
        """Can express reliability optimization."""
        instance = OptimizeFromEffects(
            executions=[completed_text_processor],
            optimization_goals=["reliability"],
            feedback="Task fails on empty input",
        )
        assert "reliability" in instance.optimization_goals

    def test_optimize_with_detailed_feedback(self, completed_calculator):
        """Can include detailed feedback about issues."""
        from .conftest import SimpleCalculator

        run2 = SimpleCalculator(a=1000, b=2000)
        run3 = SimpleCalculator(a=-5, b=10)
        feedback = """
        Observed issues:
        1. Task is 2x slower on inputs > 1000
        2. Memory usage spikes on repeated executions
        3. No caching of intermediate results
        """
        instance = OptimizeFromEffects(
            executions=[completed_calculator, run2, run3],
            feedback=feedback,
        )
        assert "slower" in instance.feedback
        assert "Memory" in instance.feedback


class TestOptimizeFromEffectsMetadata:
    """Test OptimizeFromEffects metadata and docstrings."""

    def test_has_docstring(self):
        """OptimizeFromEffects should have a docstring."""
        assert OptimizeFromEffects.__doc__ is not None
        assert len(OptimizeFromEffects.__doc__) > 50

    def test_docstring_mentions_effect_stream(self):
        """Docstring should mention effect streams."""
        assert "effect stream" in OptimizeFromEffects.__doc__.lower()

    def test_docstring_lists_optimization_goals(self):
        """Docstring should list optimization goals."""
        doc = OptimizeFromEffects.__doc__
        assert "efficiency" in doc
        assert "reliability" in doc

    def test_all_inputs_have_descriptions(self):
        """All input fields should have descriptions."""
        meta = extract_task_metadata(OptimizeFromEffects)
        for name, field_info in meta.inputs.items():
            assert field_info.description is not None, f"Input '{name}' has no description"

    def test_all_outputs_have_descriptions(self):
        """All output fields should have descriptions."""
        meta = extract_task_metadata(OptimizeFromEffects)
        for name, field_info in meta.outputs.items():
            assert field_info.description is not None, f"Output '{name}' has no description"


class TestOptimizeFromEffectsEdgeCases:
    """Test OptimizeFromEffects edge cases."""

    def test_accepts_empty_executions_list(self):
        """Can be instantiated with empty executions list."""
        instance = OptimizeFromEffects(
            executions=[],
        )
        assert instance.executions == []

    def test_task_ref_accessible_from_execution(self, completed_calculator):
        """Can retrieve the task class from a completed instance."""
        from .conftest import SimpleCalculator

        assert completed_calculator.task_ref is SimpleCalculator
