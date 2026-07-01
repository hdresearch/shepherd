"""Function-form callable-spine source handling for transform owner paths."""

from __future__ import annotations

import sys

import pytest
from shepherd_runtime.nucleus import CallableTask, deliver, task
from shepherd_transform.source import (
    ReconstructionError,
    SourceExtractionError,
    SourceValidationError,
    extract_task_imports,
    extract_task_source,
    extract_task_with_imports,
    reconstruct_task,
    reconstruct_task_class,
    try_reconstruct_task,
    validate_task_source,
)


@task(guidance="Use terse prose.", name="summarize-topic")
async def summarize_topic(topic: str, tone: str = "plain") -> str:
    return await deliver(str, goal=f"{tone}: {topic}")


CALLABLE_TASK_SOURCE = """@task(guidance="Use terse prose.", name="draft-summary")
async def draft_summary(topic: str) -> str:
    return await deliver(str, goal=f"Summarize {topic}")
"""

CALLABLE_TASK_SOURCE_WITH_ALIASES = """from shepherd_runtime.nucleus import deliver as send, task as runtime_task

@runtime_task(guidance="Use terse prose.", name="alias-summary")
async def alias_summary(topic: str) -> str:
    return await send(str, goal=f"Summarize {topic}")
"""

CALLABLE_TASK_SOURCE_WITH_PUBLIC_ALIASES = """from shepherd import deliver as send, task as public_task

@public_task(guidance="Use public aliases.", name="public-alias-summary")
async def public_alias_summary(topic: str) -> str:
    return await send(str, goal=f"Summarize {topic}")
"""

CALLABLE_TASK_SOURCE_WITH_IMPORTS = """from __future__ import annotations

from shepherd_runtime.nucleus import deliver, task

@task(guidance="Use terse prose.", name="draft-summary")
async def draft_summary(topic: str) -> str:
    return await deliver(str, goal=f"Summarize {topic}")
"""

CALLABLE_TASK_SOURCE_WITH_INPUT_MARKER = """from __future__ import annotations

import shepherd.markers

@task(guidance="Use marked input.", name="marked-summary")
async def marked_summary(
    topic: Annotated[str, shepherd.markers.InputMarker(description="Topic to summarize")],
) -> str:
    return topic.strip()
"""

CALLABLE_TASK_SOURCE_WITH_LOCAL_HELPER = """def normalize_topic(topic: str) -> str:
    return topic.strip().lower()

@task(guidance="Normalize input.", name="helper-summary")
async def helper_summary(topic: str) -> str:
    return normalize_topic(topic)
"""

CALLABLE_TASK_SOURCE_WITH_PREEXISTING_ALIAS = """existing_alias = existing_summary

@task(guidance="Use terse prose.", name="proposal-summary")
async def proposal_summary(topic: str) -> str:
    return await deliver(str, goal=f"Summarize {topic}")
"""

CALLABLE_TASK_SOURCE_WITH_FAILED_IMPORT = """from missing_callable_dependency import helper

@task(guidance="Use helper.", name="import-failure")
async def import_failure(topic: str) -> str:
    return helper(topic)
"""

CALLABLE_TASK_SOURCE_WITH_BLOCKED_IMPORT = """import os

@task(guidance="Use helper.", name="blocked-import")
async def blocked_import(topic: str) -> str:
    return os.getenv(topic, "")
"""


class TestCallableTaskSourceExtraction:
    def test_extracts_function_form_task_source(self) -> None:
        source = extract_task_source(summarize_topic)

        assert "@task(guidance=" in source
        assert "async def summarize_topic" in source
        assert 'return await deliver(str, goal=f"{tone}: {topic}")' in source
        assert summarize_topic.metadata.source == source

    def test_extracts_function_form_task_imports(self) -> None:
        imports = extract_task_imports(summarize_topic)

        assert any("shepherd_runtime.nucleus" in import_line for import_line in imports)

    def test_extracts_function_form_task_with_imports(self) -> None:
        source, imports = extract_task_with_imports(summarize_topic)

        assert "async def summarize_topic" in source
        assert any("shepherd_runtime.nucleus" in import_line for import_line in imports)

    def test_rejects_name_keyed_source_lookup(self) -> None:
        with pytest.raises(SourceExtractionError) as exc_info:
            extract_task_source("summarize_topic")

        assert "expected a class-form @task class or a function-form @task callable object" in str(exc_info.value)


class TestCallableTaskReconstruction:
    def test_reconstructs_function_form_task(self) -> None:
        reconstructed = reconstruct_task(CALLABLE_TASK_SOURCE)

        assert isinstance(reconstructed, CallableTask)
        assert reconstructed.metadata.qualname == "draft_summary"
        assert reconstructed.metadata.is_async is True
        assert reconstructed.metadata.guidance == "Use terse prose."
        assert reconstructed.metadata.name == "draft-summary"
        assert list(reconstructed.metadata.signature.parameters) == ["topic"]
        assert extract_task_source(reconstructed) == CALLABLE_TASK_SOURCE

    def test_reconstructs_function_form_task_with_alias_imports(self) -> None:
        reconstructed = reconstruct_task(CALLABLE_TASK_SOURCE_WITH_ALIASES)

        assert isinstance(reconstructed, CallableTask)
        assert reconstructed.metadata.qualname == "alias_summary"
        assert reconstructed.metadata.name == "alias-summary"
        assert extract_task_source(reconstructed) == CALLABLE_TASK_SOURCE_WITH_ALIASES

    def test_reconstructs_function_form_task_with_public_alias_imports(self) -> None:
        reconstructed = reconstruct_task(CALLABLE_TASK_SOURCE_WITH_PUBLIC_ALIASES)

        assert isinstance(reconstructed, CallableTask)
        assert reconstructed.metadata.qualname == "public_alias_summary"
        assert reconstructed.metadata.name == "public-alias-summary"
        assert extract_task_source(reconstructed) == CALLABLE_TASK_SOURCE_WITH_PUBLIC_ALIASES

    def test_reconstructs_function_form_task_with_shepherd_input_marker(self) -> None:
        reconstructed = reconstruct_task(CALLABLE_TASK_SOURCE_WITH_INPUT_MARKER)

        assert isinstance(reconstructed, CallableTask)
        assert reconstructed.metadata.qualname == "marked_summary"
        assert "InputMarker" in repr(reconstructed.metadata.signature.parameters["topic"].annotation)
        assert extract_task_source(reconstructed) == CALLABLE_TASK_SOURCE_WITH_INPUT_MARKER

    def test_reconstructs_function_form_task_with_local_helper_function(self) -> None:
        reconstructed = reconstruct_task(CALLABLE_TASK_SOURCE_WITH_LOCAL_HELPER)

        target = reconstructed.__wrapped__
        assert target.__globals__["normalize_topic"]("  Mixed Case ") == "mixed case"
        assert extract_task_source(reconstructed) == CALLABLE_TASK_SOURCE_WITH_LOCAL_HELPER

    def test_reconstructs_new_callable_when_extra_namespace_aliases_existing_task(self) -> None:
        reconstructed = reconstruct_task(
            CALLABLE_TASK_SOURCE_WITH_PREEXISTING_ALIAS,
            extra_namespace={"existing_summary": summarize_topic},
        )

        assert isinstance(reconstructed, CallableTask)
        assert reconstructed is not summarize_topic
        assert reconstructed.metadata.qualname == "proposal_summary"
        assert reconstructed.metadata.name == "proposal-summary"
        assert extract_task_source(reconstructed) == CALLABLE_TASK_SOURCE_WITH_PREEXISTING_ALIAS

    def test_reconstructed_function_form_task_imports_fall_back_to_captured_source(self) -> None:
        reconstructed = reconstruct_task(CALLABLE_TASK_SOURCE_WITH_IMPORTS)

        source, imports = extract_task_with_imports(reconstructed)

        assert source == CALLABLE_TASK_SOURCE_WITH_IMPORTS
        assert imports == [
            "from __future__ import annotations",
            "from shepherd_runtime.nucleus import deliver, task",
        ]

    def test_reconstructed_function_form_task_without_imports_returns_empty_imports(self) -> None:
        reconstructed = reconstruct_task(CALLABLE_TASK_SOURCE)

        assert extract_task_imports(reconstructed) == []
        assert extract_task_with_imports(reconstructed) == (CALLABLE_TASK_SOURCE, [])

    def test_try_reconstruct_task_returns_callable_task(self) -> None:
        result = try_reconstruct_task(CALLABLE_TASK_SOURCE)

        assert result.success is True
        assert isinstance(result.task, CallableTask)
        assert result.task_class is None

    def test_reconstruct_task_class_rejects_function_form_source(self) -> None:
        with pytest.raises(ReconstructionError) as exc_info:
            reconstruct_task_class(CALLABLE_TASK_SOURCE)

        assert exc_info.value.error_type == "CALLABLE_TASK_SOURCE"
        assert "reconstruct_task()" in exc_info.value.suggestion

    def test_validate_task_source_accepts_function_form_task(self) -> None:
        assert validate_task_source(CALLABLE_TASK_SOURCE) == []

    def test_no_synthetic_module_leak_on_callable_success(self) -> None:
        initial_modules = {name for name in sys.modules if name.startswith("shepherd_reconstructed")}

        reconstruct_task(CALLABLE_TASK_SOURCE)

        final_modules = {name for name in sys.modules if name.startswith("shepherd_reconstructed")}
        assert final_modules == initial_modules

    def test_failed_import_raises_transform_reconstruction_error(self) -> None:
        initial_modules = {name for name in sys.modules if name.startswith("shepherd_reconstructed")}

        with pytest.raises(ReconstructionError) as exc_info:
            reconstruct_task(CALLABLE_TASK_SOURCE_WITH_FAILED_IMPORT)

        assert exc_info.value.error_type == "IMPORT_ERROR"
        assert "missing_callable_dependency" in exc_info.value.message
        final_modules = {name for name in sys.modules if name.startswith("shepherd_reconstructed")}
        assert final_modules == initial_modules

    def test_validation_error_uses_transform_exception_for_callable_source(self) -> None:
        with pytest.raises(SourceValidationError) as exc_info:
            reconstruct_task(CALLABLE_TASK_SOURCE_WITH_BLOCKED_IMPORT)

        assert any("os" in violation for violation in exc_info.value.violations)

    def test_try_reconstruct_task_reports_callable_validation_error(self) -> None:
        result = try_reconstruct_task(CALLABLE_TASK_SOURCE_WITH_BLOCKED_IMPORT)

        assert result.success is False
        assert result.task is None
        assert result.error_type == "VALIDATION_ERROR"

    def test_try_reconstruct_task_reports_callable_reconstruction_error(self) -> None:
        result = try_reconstruct_task("async def not_a_task(topic: str) -> str:\n    return topic\n")

        assert result.success is False
        assert result.task is None
        assert result.error_type == "MISSING_TASK_DECORATOR"
