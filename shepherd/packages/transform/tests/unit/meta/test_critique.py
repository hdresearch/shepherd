"""Tests for CritiqueTask meta-task."""

from __future__ import annotations

from shepherd_runtime.task.markers import TaskRef
from shepherd_runtime.task.metadata import extract_task_metadata
from shepherd_transform.meta import CritiqueTask


class TestCritiqueTaskDefinition:
    """Test CritiqueTask class structure and metadata."""

    def test_is_task_decorated(self):
        """CritiqueTask should be decorated with @task."""
        assert hasattr(CritiqueTask, "_task_meta")

    def test_has_target_input(self):
        """CritiqueTask should have a target input of type TaskRef."""
        meta = extract_task_metadata(CritiqueTask)
        assert "target" in meta.inputs
        assert meta.inputs["target"].inner_type is TaskRef

    def test_has_criteria_input(self):
        """CritiqueTask should have a criteria input."""
        meta = extract_task_metadata(CritiqueTask)
        assert "criteria" in meta.inputs
        # Should have a default value
        instance = CritiqueTask(target=CritiqueTask)
        assert instance.criteria == ["clarity", "completeness", "error_handling"]

    def test_has_expected_outputs(self):
        """CritiqueTask should have critique, suggestions, and severity outputs."""
        meta = extract_task_metadata(CritiqueTask)
        assert "critique" in meta.outputs
        assert "suggestions" in meta.outputs
        assert "severity" in meta.outputs

    def test_critique_output_is_string(self):
        """Critique output should be a string (or str | None)."""
        meta = extract_task_metadata(CritiqueTask)
        import typing

        inner = meta.outputs["critique"].inner_type
        # Output types are T | None, extract the non-None type
        args = typing.get_args(inner)
        non_none = [a for a in args if a is not type(None)]
        assert str in non_none

    def test_suggestions_output_is_list(self):
        """Suggestions output should be a list of strings."""
        meta = extract_task_metadata(CritiqueTask)
        import typing

        inner = meta.outputs["suggestions"].inner_type
        # Output types are T | None, extract the non-None type
        args = typing.get_args(inner)
        non_none = [a for a in args if a is not type(None)]
        assert len(non_none) == 1
        origin = typing.get_origin(non_none[0])
        assert origin is list

    def test_severity_output_is_literal(self):
        """Severity output should be a Literal type."""
        meta = extract_task_metadata(CritiqueTask)
        import typing

        inner = meta.outputs["severity"].inner_type
        # Output types are T | None, extract the non-None type
        args = typing.get_args(inner)
        non_none = [a for a in args if a is not type(None)]
        assert len(non_none) == 1
        origin = typing.get_origin(non_none[0])
        assert origin is typing.Literal


class TestCritiqueTaskInstantiation:
    """Test CritiqueTask can be instantiated with various inputs."""

    def test_instantiate_with_task_class(self, simple_calculator):
        """CritiqueTask can be instantiated with a task class."""
        instance = CritiqueTask(target=simple_calculator)
        assert instance.target is simple_calculator

    def test_instantiate_with_custom_criteria(self, simple_calculator):
        """CritiqueTask can be instantiated with custom criteria."""
        instance = CritiqueTask(
            target=simple_calculator,
            criteria=["type_safety", "documentation"],
        )
        assert instance.criteria == ["type_safety", "documentation"]

    def test_outputs_are_populated_in_mock_mode(self, simple_calculator):
        """Output fields should be populated with mock values in mock mode."""
        instance = CritiqueTask(target=simple_calculator)
        # In mock mode, outputs get mock values
        assert instance.critique is not None
        assert instance.suggestions is not None
        assert instance.severity is not None

    def test_instantiate_with_itself(self):
        """CritiqueTask can critique itself (meta!)."""
        instance = CritiqueTask(target=CritiqueTask)
        assert instance.target is CritiqueTask


class TestCritiqueTaskWithSampleTasks:
    """Test CritiqueTask with various sample tasks."""

    def test_can_accept_task_with_issues(self, task_with_issues):
        """CritiqueTask can accept a task with design issues."""
        instance = CritiqueTask(target=task_with_issues)
        assert instance.target is task_with_issues

    def test_can_accept_well_designed_task(self, well_designed_task):
        """CritiqueTask can accept a well-designed task."""
        instance = CritiqueTask(target=well_designed_task)
        assert instance.target is well_designed_task

    def test_criteria_options(self, simple_calculator):
        """All documented criteria should be valid."""
        for criteria in [
            ["clarity"],
            ["completeness"],
            ["error_handling"],
            ["type_safety"],
            ["documentation"],
            ["clarity", "completeness", "error_handling", "type_safety", "documentation"],
        ]:
            instance = CritiqueTask(target=simple_calculator, criteria=criteria)
            assert instance.criteria == criteria


class TestCritiqueTaskMetadata:
    """Test CritiqueTask metadata and docstrings."""

    def test_has_docstring(self):
        """CritiqueTask should have a docstring."""
        assert CritiqueTask.__doc__ is not None
        assert len(CritiqueTask.__doc__) > 50

    def test_docstring_mentions_evaluation(self):
        """Docstring should mention evaluation criteria."""
        assert "Evaluation criteria" in CritiqueTask.__doc__
        assert "clarity" in CritiqueTask.__doc__

    def test_target_has_description(self):
        """Target field should have a description."""
        meta = extract_task_metadata(CritiqueTask)
        assert meta.inputs["target"].description is not None
        assert len(meta.inputs["target"].description) > 0

    def test_all_outputs_have_descriptions(self):
        """All output fields should have descriptions."""
        meta = extract_task_metadata(CritiqueTask)
        for name, field_info in meta.outputs.items():
            assert field_info.description is not None, f"Output '{name}' has no description"
            assert len(field_info.description) > 0, f"Output '{name}' has empty description"
