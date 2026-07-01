"""Tests for shepherd_tests.tasks factory module."""

from typing import Literal

import pytest
from shepherd_tests import mock_steps
from shepherd_tests.tasks import (
    INLINE_STEP_TEST_CASES,
    RETURN_TYPE_TEST_CASES,
    _normalize_output_type,
    _type_name,
    make_inline_step_task,
    make_step_task,
)


class TestMakeStepTask:
    """Tests for make_step_task factory."""

    def test_returns_task_class(self):
        """Factory returns a class with @task metadata."""
        Task = make_step_task(str)
        assert hasattr(Task, "_task_meta")

    def test_rejects_none(self):
        """Factory raises TypeError for None."""
        with pytest.raises(TypeError, match="cannot be None"):
            make_step_task(None)

    def test_class_has_expected_fields(self):
        """Generated class has input_val and output_val."""
        Task = make_step_task(str)
        fields = Task.model_fields
        assert "input_val" in fields
        assert "output_val" in fields

    def test_class_has_process_method(self):
        """Generated class has process() step method."""
        Task = make_step_task(str)
        assert hasattr(Task, "process")
        assert hasattr(Task.process, "_step_metadata")

    def test_process_has_correct_return_type(self):
        """Step method has correct return type annotation."""
        Task = make_step_task(Literal["yes", "no"])
        annotations = Task.process.__annotations__
        assert annotations.get("return") == Literal["yes", "no"]

    def test_class_name_is_descriptive(self):
        """Generated class has readable __name__."""
        Task = make_step_task(str)
        assert "StepTask" in Task.__name__
        assert "str" in Task.__name__

    def test_different_types_produce_different_names(self):
        """Different return types produce different class names."""
        T1 = make_step_task(str)
        T2 = make_step_task(int)
        T3 = make_step_task(Literal["a", "b"])
        names = {T1.__name__, T2.__name__, T3.__name__}
        assert len(names) == 3

    def test_multiple_calls_produce_independent_classes(self):
        """Each factory call produces a new class (no shared state)."""
        T1 = make_step_task(str)
        T2 = make_step_task(str)
        assert T1 is not T2

    def test_executes_with_mock(self):
        """Generated task executes correctly under mock_steps()."""
        Task = make_step_task(Literal["yes", "no"])
        with mock_steps():
            t = Task(input_val="test")
            assert t.output_val == "yes"


class TestMakeInlineStepTask:
    """Tests for make_inline_step_task factory."""

    def test_returns_task_class(self):
        """Factory returns a class with @task metadata."""
        Task = make_inline_step_task(str)
        assert hasattr(Task, "_task_meta")

    def test_rejects_none(self):
        """Factory raises TypeError for None."""
        with pytest.raises(TypeError, match="cannot be None"):
            make_inline_step_task(None)

    def test_class_has_expected_fields(self):
        """Generated class has input_val and output_val."""
        Task = make_inline_step_task(str)
        fields = Task.model_fields
        assert "input_val" in fields
        assert "output_val" in fields

    def test_class_name_is_descriptive(self):
        """Generated class has readable __name__."""
        Task = make_inline_step_task(str)
        assert "InlineStepTask" in Task.__name__
        assert "str" in Task.__name__

    def test_different_types_produce_different_names(self):
        """Different return types produce different class names."""
        T1 = make_inline_step_task(str)
        T2 = make_inline_step_task(int)
        T3 = make_inline_step_task(Literal["a", "b"])
        names = {T1.__name__, T2.__name__, T3.__name__}
        assert len(names) == 3

    def test_executes_with_mock_literal(self):
        """Generated task executes correctly with Literal type."""
        Task = make_inline_step_task(Literal["yes", "no"], "Is {val} good?")
        with mock_steps():
            t = Task(input_val="test")
            assert t.output_val in ("yes", "no")

    def test_executes_with_mock_string(self):
        """Generated task executes correctly with str type."""
        Task = make_inline_step_task(str, "Summarize: {val}")
        with mock_steps():
            t = Task(input_val="test")
            assert "[mock" in t.output_val

    def test_custom_prompt_template(self):
        """Factory respects custom prompt template."""
        Task = make_inline_step_task(str, "Custom template: {val}")
        with mock_steps():
            t = Task(input_val="test")
            # Should execute without error
            assert t.output_val is not None


class TestNormalizeOutputType:
    """Tests for _normalize_output_type helper."""

    def test_literal_to_str(self):
        assert _normalize_output_type(Literal["a", "b"]) is str

    def test_list_to_list(self):
        assert _normalize_output_type(list[int]) is list

    def test_dict_to_dict(self):
        assert _normalize_output_type(dict[str, int]) is dict

    def test_primitives_unchanged(self):
        assert _normalize_output_type(str) is str
        assert _normalize_output_type(int) is int
        assert _normalize_output_type(float) is float
        assert _normalize_output_type(bool) is bool


class TestTypeName:
    """Tests for _type_name helper."""

    def test_named_types(self):
        assert _type_name(str) == "str"
        assert _type_name(int) == "int"

    def test_literal_short(self):
        name = _type_name(Literal["a", "b"])
        assert "Literal" in name
        assert "'a'" in name
        assert "'b'" in name

    def test_literal_truncated(self):
        name = _type_name(Literal["a", "b", "c", "d", "e"])
        assert "..." in name


class TestReturnTypeCases:
    """Tests that RETURN_TYPE_TEST_CASES work with factory."""

    def test_all_cases_have_unique_ids(self):
        """Each test case has a unique id."""
        ids = [case.id for case in RETURN_TYPE_TEST_CASES]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize("case", RETURN_TYPE_TEST_CASES, ids=lambda c: c.id)
    def test_case_produces_valid_task(self, case):
        """Each test case produces a functional task."""
        Task = make_step_task(case.return_type)
        with mock_steps():
            t = Task(input_val="test")
            if case.partial_match:
                assert case.expected in str(t.output_val)
            else:
                assert t.output_val == case.expected


class TestInlineStepCases:
    """Tests that INLINE_STEP_TEST_CASES work with factory."""

    def test_all_cases_have_unique_ids(self):
        """Each test case has a unique id."""
        ids = [case.id for case in INLINE_STEP_TEST_CASES]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize("case", INLINE_STEP_TEST_CASES, ids=lambda c: c.id)
    def test_case_produces_valid_task(self, case):
        """Each test case produces a functional task."""
        Task = make_inline_step_task(case.return_type, case.prompt_template)
        with mock_steps():
            t = Task(input_val="test")
            if case.partial_match:
                assert case.expected in str(t.output_val)
            elif isinstance(case.expected, tuple):
                # For Literal types, check if output is one of expected values
                assert t.output_val in case.expected
            else:
                assert t.output_val == case.expected
