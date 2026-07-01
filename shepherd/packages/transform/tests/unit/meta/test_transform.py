"""Tests for TransformTask meta-task."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from shepherd_runtime.nucleus import CallableTask, deliver, reset_workspace_for_tests, task, workspace
from shepherd_runtime.task.markers import TaskRef
from shepherd_runtime.task.metadata import extract_task_metadata
from shepherd_transform.meta import TransformTask
from shepherd_transform.meta.transform import build_transform_proposal


@task(guidance="Use terse prose.", name="summarize-topic")
async def summarize_topic(topic: str) -> str:
    return await deliver(str, goal=f"Summarize {topic}")


@task(name="add-values")
def add_values(x: int, y: int) -> dict[str, int]:
    return {"result": x + y}


@dataclass(frozen=True)
class _OfflineModel:
    name: str = "offline-transform"
    provider_id: str = "provider.offline"


FUNCTION_FORM_PROPOSAL_SOURCE = """from shepherd_runtime.nucleus import deliver, task

@task(guidance="Use terse prose.", name="proposal-summary")
async def proposal_summary(topic: str) -> str:
    return await deliver(str, goal=f"Summarize {topic}")
"""

FUNCTION_FORM_GROUNDING_SOURCE = """from shepherd_runtime.nucleus import task

@task(name="add-values-proposal")
def add_values_proposal(x: int, y: int) -> dict[str, int]:
    return {"result": x + y}
"""


@pytest.fixture
def nucleus_workspace(tmp_path):
    reset_workspace_for_tests()
    workspace(model=_OfflineModel(), root=tmp_path)
    yield
    reset_workspace_for_tests()


class TestTransformTaskDefinition:
    """Test TransformTask class structure and metadata."""

    def test_is_task_decorated(self):
        """TransformTask should be decorated with @task."""
        assert hasattr(TransformTask, "_task_meta")

    def test_has_target_input(self):
        """TransformTask should have a target input of type TaskRef."""
        meta = extract_task_metadata(TransformTask)
        assert "target" in meta.inputs
        assert meta.inputs["target"].inner_type is TaskRef

    def test_has_instruction_input(self):
        """TransformTask should have an instruction input."""
        meta = extract_task_metadata(TransformTask)
        assert "instruction" in meta.inputs
        assert meta.inputs["instruction"].inner_type is str

    def test_has_preserve_behavior_input(self):
        """TransformTask should have a preserve_behavior input with default True."""
        meta = extract_task_metadata(TransformTask)
        assert "preserve_behavior" in meta.inputs
        instance = TransformTask(target=TransformTask, instruction="test")
        assert instance.preserve_behavior is True

    def test_has_transformed_output(self):
        """TransformTask should have a transformed output of type TaskRef."""
        meta = extract_task_metadata(TransformTask)
        import typing

        assert "transformed" in meta.outputs
        # Output types are T | None, extract the non-None type
        inner = meta.outputs["transformed"].inner_type
        args = typing.get_args(inner)
        non_none = [a for a in args if a is not type(None)]
        assert TaskRef in non_none

    def test_has_transformed_source_output(self):
        """TransformTask should have a transformed_source output."""
        meta = extract_task_metadata(TransformTask)
        import typing

        assert "transformed_source" in meta.outputs
        inner = meta.outputs["transformed_source"].inner_type
        args = typing.get_args(inner)
        non_none = [a for a in args if a is not type(None)]
        assert str in non_none

    def test_has_explanation_output(self):
        """TransformTask should have an explanation output."""
        meta = extract_task_metadata(TransformTask)
        import typing

        assert "explanation" in meta.outputs
        inner = meta.outputs["explanation"].inner_type
        args = typing.get_args(inner)
        non_none = [a for a in args if a is not type(None)]
        assert str in non_none


class TestTransformTaskInstantiation:
    """Test TransformTask can be instantiated with various inputs."""

    def test_instantiate_with_task_and_instruction(self, simple_calculator):
        """TransformTask can be instantiated with a task and instruction."""
        instance = TransformTask(
            target=simple_calculator,
            instruction="Add logging for each operation",
        )
        assert instance.target is simple_calculator
        assert instance.instruction == "Add logging for each operation"

    def test_instantiate_with_preserve_behavior_false(self, simple_calculator):
        """TransformTask can be instantiated with preserve_behavior=False."""
        instance = TransformTask(
            target=simple_calculator,
            instruction="Completely change the interface",
            preserve_behavior=False,
        )
        assert instance.preserve_behavior is False

    def test_outputs_are_populated_in_mock_mode(self, simple_calculator):
        """Output fields should be populated with mock values in mock mode."""
        instance = TransformTask(
            target=simple_calculator,
            instruction="test",
        )
        # In mock mode, outputs get mock values
        assert instance.transformed is not None
        assert instance.transformed_source is not None
        assert instance.explanation is not None


class TestTransformTaskVerifyTransformation:
    """Test TransformTask.verify_transformation() method."""

    def test_verify_transformation_raises_without_transformed(self, simple_calculator):
        """verify_transformation raises ValueError if no transformed task."""
        instance = TransformTask(
            target=simple_calculator,
            instruction="test",
        )
        # Mock mode populates outputs, so manually clear for this test
        instance.transformed = None
        with pytest.raises(ValueError, match="No transformed task available"):
            instance.verify_transformation(simple_calculator)

    def test_verify_transformation_signature(self, simple_calculator):
        """verify_transformation has the expected signature."""
        import inspect

        sig = inspect.signature(TransformTask.verify_transformation)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "original_class" in params
        assert "test_cases" in params
        assert "equivalence" in params

    def test_verify_transformation_with_mock_transformed(self, simple_calculator):
        """verify_transformation works when transformed is set manually."""
        instance = TransformTask(
            target=simple_calculator,
            instruction="test",
        )
        # Manually set the transformed task (simulating execution)
        instance.transformed = simple_calculator  # Same task = should pass

        # Should work (comparing task to itself should pass)
        result = instance.verify_transformation(simple_calculator)
        assert result.passed
        assert result.test_count > 0

    def test_verify_transformation_with_custom_test_cases(self, simple_calculator):
        """verify_transformation accepts custom test cases."""
        instance = TransformTask(
            target=simple_calculator,
            instruction="test",
        )
        instance.transformed = simple_calculator

        custom_cases = [
            {"a": 1, "b": 2},
            {"a": 10, "b": 20},
        ]
        result = instance.verify_transformation(
            simple_calculator,
            test_cases=custom_cases,
        )
        assert result.passed
        assert result.test_count == 2

    def test_verify_transformation_with_equivalence_level(self, simple_calculator):
        """verify_transformation accepts equivalence level."""
        from shepherd_transform.grounding import EquivalenceLevel

        instance = TransformTask(
            target=simple_calculator,
            instruction="test",
        )
        instance.transformed = simple_calculator

        result = instance.verify_transformation(
            simple_calculator,
            equivalence=EquivalenceLevel.STRICT,
        )
        assert result.passed


class TestTransformTaskUseCases:
    """Test TransformTask with various transformation use cases."""

    def test_add_logging_instruction(self, simple_calculator):
        """Can express 'add logging' transformation."""
        instance = TransformTask(
            target=simple_calculator,
            instruction="Add logging that prints each operation before computing",
        )
        assert "logging" in instance.instruction.lower()

    def test_add_validation_instruction(self, text_processor):
        """Can express 'add validation' transformation."""
        instance = TransformTask(
            target=text_processor,
            instruction="Add input validation to ensure text is not empty",
        )
        assert "validation" in instance.instruction.lower()

    def test_rename_field_instruction(self, simple_calculator):
        """Can express 'rename field' transformation."""
        instance = TransformTask(
            target=simple_calculator,
            instruction="Rename 'a' and 'b' to 'first_number' and 'second_number'",
        )
        assert "rename" in instance.instruction.lower()

    def test_add_output_field_instruction(self, simple_calculator):
        """Can express 'add output field' transformation."""
        instance = TransformTask(
            target=simple_calculator,
            instruction="Add a 'computation_time_ms' output field",
        )
        assert "output" in instance.instruction.lower()


class TestTransformTaskFunctionFormProposal:
    """Test function-form transform proposal support."""

    def test_build_transform_proposal_reconstructs_function_form_source(self, simple_calculator):
        """Function-form transformed source can be reconstructed as a proposal."""
        proposal = build_transform_proposal(
            target=simple_calculator,
            transformed_source=FUNCTION_FORM_PROPOSAL_SOURCE,
            instruction="Move this task to function form",
            explanation="Converted class-form task to callable form.",
        )

        assert proposal.target is simple_calculator
        assert proposal.instruction == "Move this task to function form"
        assert proposal.source == FUNCTION_FORM_PROPOSAL_SOURCE
        assert proposal.explanation == "Converted class-form task to callable form."
        assert proposal.is_function_form is True
        assert proposal.is_class_form is False
        assert proposal.task_class is None
        assert isinstance(proposal.task, CallableTask)
        assert proposal.task.metadata.qualname == "proposal_summary"
        assert proposal.task.metadata.name == "proposal-summary"

    def test_transform_task_build_proposal_uses_function_form_source_when_taskref_is_unset(self):
        """A TransformTask can build a function-form proposal from transformed_source only."""
        instance = TransformTask(
            target=summarize_topic,
            instruction="Add stricter summary guidance",
        )
        instance.transformed = None
        instance.transformed_source = FUNCTION_FORM_PROPOSAL_SOURCE
        instance.explanation = "Kept the proposal in function form."

        proposal = instance.build_proposal()

        assert proposal.target is summarize_topic
        assert proposal.explanation == "Kept the proposal in function form."
        assert proposal.is_function_form is True
        assert isinstance(proposal.task, CallableTask)
        assert proposal.task.metadata.qualname == "proposal_summary"

    def test_function_form_transform_proposal_verifies_behavior(self, nucleus_workspace):
        """Function-form proposals can be behaviorally grounded without class adapters."""
        proposal = build_transform_proposal(
            target=add_values,
            transformed_source=FUNCTION_FORM_GROUNDING_SOURCE,
            instruction="Keep behavior while renaming the function.",
        )

        result = proposal.verify(test_cases=[{"x": 1, "y": 2}, {"x": -5, "y": 10}])

        assert result.passed is True
        assert result.match_count == 2

    def test_deliver_backed_function_form_failures_do_not_verify_without_handler(self, nucleus_workspace):
        """Unhandled deliver failures on both sides do not count as preserved behavior."""
        proposal = build_transform_proposal(
            target=summarize_topic,
            transformed_source=FUNCTION_FORM_PROPOSAL_SOURCE,
            instruction="Keep summarization behavior.",
        )

        result = proposal.verify(test_cases=[{"topic": "phase1"}])

        assert result.passed is False
        assert result.match_count == 0
        assert len(result.mismatches) == 1
        assert "original failed" in (result.mismatches[0].error or "")
        assert "transformed failed" in (result.mismatches[0].error or "")
        assert 'handle("model.call"' in (result.mismatches[0].error or "")

    def test_transform_task_verify_transformation_accepts_function_form_task(self, tmp_path):
        """TransformTask.verify_transformation accepts function-form transformed tasks."""
        proposal = build_transform_proposal(
            target=add_values,
            transformed_source=FUNCTION_FORM_GROUNDING_SOURCE,
            instruction="Keep behavior while renaming the function.",
        )
        reset_workspace_for_tests()
        instance = TransformTask(
            target=add_values,
            instruction="Keep behavior while renaming the function.",
        )
        instance.transformed = proposal.task
        workspace(model=_OfflineModel(), root=tmp_path)

        try:
            result = instance.verify_transformation(
                add_values,
                test_cases=[{"x": 2, "y": 3}],
            )

            assert result.passed is True
        finally:
            reset_workspace_for_tests()


class TestTransformTaskMetadata:
    """Test TransformTask metadata and docstrings."""

    def test_has_docstring(self):
        """TransformTask should have a docstring."""
        assert TransformTask.__doc__ is not None
        assert len(TransformTask.__doc__) > 50

    def test_docstring_mentions_reconstruction(self):
        """Docstring should mention owner-path reconstruction."""
        assert "reconstruct" in TransformTask.__doc__.lower()

    def test_all_inputs_have_descriptions(self):
        """All input fields should have descriptions."""
        meta = extract_task_metadata(TransformTask)
        for name, field_info in meta.inputs.items():
            assert field_info.description is not None, f"Input '{name}' has no description"

    def test_all_outputs_have_descriptions(self):
        """All output fields should have descriptions."""
        meta = extract_task_metadata(TransformTask)
        for name, field_info in meta.outputs.items():
            assert field_info.description is not None, f"Output '{name}' has no description"
