"""Transform-owned facade for task source extraction, validation, and reconstruction."""

from __future__ import annotations

import ast
import contextlib
import inspect
import sys
import textwrap
import types
import uuid
from dataclasses import dataclass
from typing import Any, TypeGuard

from shepherd_runtime.nucleus import CallableTask as _RuntimeCallableTask
from shepherd_runtime.task._source_state import reconstruction_source
from shepherd_runtime.task.reconstruction import ReconstructionError as _RuntimeReconstructionError
from shepherd_runtime.task.reconstruction import reconstruct_task_class as _runtime_reconstruct_task_class
from shepherd_runtime.task.secure import SecurityError, secure_reconstruct_task_class
from shepherd_runtime.task.source_analysis import (
    SourceExtractionError as _RuntimeSourceExtractionError,
)
from shepherd_runtime.task.source_analysis import (
    extract_imports_from_file as _runtime_extract_imports_from_file,
)
from shepherd_runtime.task.source_analysis import (
    extract_imports_from_source as _runtime_extract_imports_from_source,
)
from shepherd_runtime.task.source_analysis import (
    extract_referenced_names as _runtime_extract_referenced_names,
)
from shepherd_runtime.task.source_analysis import (
    extract_task_imports as _runtime_extract_task_imports,
)
from shepherd_runtime.task.source_analysis import (
    extract_task_source as _runtime_extract_task_source,
)
from shepherd_runtime.task.source_analysis import (
    extract_task_with_imports as _runtime_extract_task_with_imports,
)
from shepherd_runtime.task.source_validation import (
    SourceValidationError as _RuntimeSourceValidationError,
)
from shepherd_runtime.task.source_validation import (
    validate_task_source as _runtime_validate_task_source,
)

__all__ = [
    "ReconstructionError",
    "ReconstructionResult",
    "SourceExtractionError",
    "SourceValidationError",
    "extract_imports_from_file",
    "extract_imports_from_source",
    "extract_referenced_names",
    "extract_task_imports",
    "extract_task_source",
    "extract_task_with_imports",
    "reconstruct_task",
    "reconstruct_task_class",
    "try_reconstruct_task",
    "try_reconstruct_task_class",
    "validate_task_source",
]


class SourceExtractionError(Exception):
    """Raised when task source cannot be extracted from the transform facade."""


class SourceValidationError(Exception):
    """Raised when task source fails transform-facing validation."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__(f"Source validation failed: {violations}")


class ReconstructionError(Exception):
    """Raised when non-secure task reconstruction fails."""

    def __init__(
        self,
        error_type: str,
        message: str,
        suggestion: str,
        *,
        recoverable: bool = True,
        line_number: int | None = None,
        source_snippet: str = "",
    ) -> None:
        self.error_type = error_type
        self.message = message
        self.suggestion = suggestion
        self.recoverable = recoverable
        self.line_number = line_number
        self.source_snippet = source_snippet
        super().__init__(f"{error_type}: {message}")


@dataclass
class ReconstructionResult:
    """Structured result for non-throwing reconstruction."""

    success: bool
    task: object | None = None
    task_class: type | None = None
    error: str | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        if self.task is None and self.task_class is not None:
            self.task = self.task_class
        elif self.task_class is None and isinstance(self.task, type):
            self.task_class = self.task


def _translate_source_extraction_error(exc: _RuntimeSourceExtractionError) -> SourceExtractionError:
    return SourceExtractionError(str(exc))


def _translate_source_validation_error(exc: _RuntimeSourceValidationError) -> SourceValidationError:
    return SourceValidationError(list(exc.violations))


def _translate_reconstruction_error(exc: _RuntimeReconstructionError) -> ReconstructionError:
    return ReconstructionError(
        exc.error_type,
        exc.message,
        exc.suggestion,
        recoverable=exc.recoverable,
        line_number=exc.line_number,
        source_snippet=exc.source_snippet,
    )


def _is_callable_task(candidate: object) -> TypeGuard[_RuntimeCallableTask[Any]]:
    return isinstance(candidate, _RuntimeCallableTask)


def _callable_task_function(task_obj: _RuntimeCallableTask[Any]) -> Any:
    return getattr(task_obj, "__wrapped__", getattr(task_obj, "_fn", task_obj))


def _extract_callable_task_source(task_obj: _RuntimeCallableTask[Any]) -> str:
    source = task_obj.metadata.source
    if source is not None:
        return textwrap.dedent(source)

    target = _callable_task_function(task_obj)
    try:
        return textwrap.dedent(inspect.getsource(target))
    except (OSError, TypeError) as exc:
        raise SourceExtractionError(
            f"Cannot extract source for callable task {task_obj.metadata.qualname}: {exc}"
        ) from exc


def _extract_callable_task_imports(task_obj: _RuntimeCallableTask[Any]) -> list[str]:
    target = _callable_task_function(task_obj)
    source_file = inspect.getsourcefile(target)
    if source_file is not None:
        return extract_imports_from_file(source_file)

    source = task_obj.metadata.source
    if source is not None:
        return extract_imports_from_source(source)

    raise SourceExtractionError(
        f"Cannot find source file or captured source for callable task {task_obj.metadata.qualname}"
    )


def _unsupported_task_source_object_error(task_obj: object) -> SourceExtractionError:
    label = getattr(task_obj, "__name__", type(task_obj).__name__)
    return SourceExtractionError(
        f"Cannot extract task source for {label!r}: expected a class-form @task class "
        "or a function-form @task callable object"
    )


def extract_task_source(task: object) -> str:
    """Extract complete source code of a class-form or function-form @task object."""
    if _is_callable_task(task):
        return _extract_callable_task_source(task)

    if not isinstance(task, type):
        raise _unsupported_task_source_object_error(task)

    try:
        return _runtime_extract_task_source(task)
    except _RuntimeSourceExtractionError as exc:
        raise _translate_source_extraction_error(exc) from exc


def extract_task_imports(task: object) -> list[str]:
    """Extract import statements from the file containing a task object."""
    if _is_callable_task(task):
        return _extract_callable_task_imports(task)

    if not isinstance(task, type):
        raise _unsupported_task_source_object_error(task)

    try:
        return _runtime_extract_task_imports(task)
    except _RuntimeSourceExtractionError as exc:
        raise _translate_source_extraction_error(exc) from exc


def extract_imports_from_file(file_path: str) -> list[str]:
    """Extract all import statements from a Python file."""
    try:
        return _runtime_extract_imports_from_file(file_path)
    except _RuntimeSourceExtractionError as exc:
        raise _translate_source_extraction_error(exc) from exc


def extract_imports_from_source(source: str) -> list[str]:
    """Extract import statements from a source string."""
    return _runtime_extract_imports_from_source(source)


def extract_referenced_names(source: str) -> set[str]:
    """Extract names referenced in source code."""
    return _runtime_extract_referenced_names(source)


def extract_task_with_imports(task: object) -> tuple[str, list[str]]:
    """Extract task source and source-file imports together."""
    if _is_callable_task(task):
        return extract_task_source(task), extract_task_imports(task)

    if not isinstance(task, type):
        raise _unsupported_task_source_object_error(task)

    try:
        return _runtime_extract_task_with_imports(task)
    except _RuntimeSourceExtractionError as exc:
        raise _translate_source_extraction_error(exc) from exc


def validate_task_source(source: str, *, strict: bool = True) -> list[str]:
    """Validate task source code for dangerous patterns."""
    return _runtime_validate_task_source(source, strict=strict)


def reconstruct_task_class(
    source: str,
    imports: list[str] | None = None,
    extra_namespace: dict[str, Any] | None = None,
    *,
    validate: bool = True,
) -> type:
    """Reconstruct a @task class through the transform-owned facade."""
    with contextlib.suppress(SyntaxError):
        if _detect_task_source_shape(source) == "callable":
            raise ReconstructionError(
                "CALLABLE_TASK_SOURCE",
                "Function-form @task source cannot be reconstructed with reconstruct_task_class().",
                "Use reconstruct_task() for callable-spine task source.",
            )

    try:
        return _runtime_reconstruct_task_class(
            source,
            imports,
            extra_namespace,
            validate=validate,
        )
    except _RuntimeSourceValidationError as exc:
        raise _translate_source_validation_error(exc) from exc
    except _RuntimeReconstructionError as exc:
        raise _translate_reconstruction_error(exc) from exc


def reconstruct_task(
    source: str,
    imports: list[str] | None = None,
    extra_namespace: dict[str, Any] | None = None,
    *,
    validate: bool = True,
) -> object:
    """Reconstruct class-form or function-form @task source.

    Function-form reconstruction uses the syntax nucleus owner paths
    (`shepherd_runtime.nucleus` and `shepherd_runtime.effects`) and returns
    the reconstructed `CallableTask` object. Class-form source is delegated
    to `reconstruct_task_class()` for compatibility with existing transform
    workflows.
    """
    try:
        shape = _detect_task_source_shape(source)
    except SyntaxError as exc:
        raise _syntax_reconstruction_error(exc, source) from exc

    if shape == "class":
        return reconstruct_task_class(source, imports, extra_namespace, validate=validate)
    if shape == "callable":
        return _reconstruct_callable_task(source, imports, extra_namespace, validate=validate)

    raise ReconstructionError(
        "MISSING_TASK_DECORATOR",
        "No class-form or function-form @task definition found in source.",
        "Ensure the source includes a class or function decorated with @task.",
    )


def try_reconstruct_task(
    source: str,
    imports: list[str] | None = None,
    extra_namespace: dict[str, Any] | None = None,
) -> ReconstructionResult:
    """Attempt class-form or function-form reconstruction without throwing."""
    try:
        task_obj = reconstruct_task(source, imports, extra_namespace)
        return ReconstructionResult(success=True, task=task_obj)
    except SourceValidationError as exc:
        return ReconstructionResult(success=False, error=str(exc), error_type="VALIDATION_ERROR")
    except ReconstructionError as exc:
        return ReconstructionResult(success=False, error=str(exc), error_type=exc.error_type)
    except Exception as exc:  # noqa: BLE001
        return ReconstructionResult(success=False, error=str(exc), error_type="UNKNOWN_ERROR")


def try_reconstruct_task_class(
    source: str,
    imports: list[str] | None = None,
    extra_namespace: dict[str, Any] | None = None,
    *,
    allowed_imports: frozenset[str] = frozenset(),
) -> ReconstructionResult:
    """Attempt reconstruction without throwing."""
    try:
        task_class = secure_reconstruct_task_class(
            source,
            imports,
            extra_namespace,
            allowed_imports=allowed_imports,
        )
        return ReconstructionResult(success=True, task=task_class, task_class=task_class)
    except SecurityError as exc:
        return ReconstructionResult(success=False, error=str(exc), error_type="SECURITY_ERROR")
    except SyntaxError as exc:
        return ReconstructionResult(success=False, error=str(exc), error_type="SYNTAX_ERROR")
    except ValueError as exc:
        return ReconstructionResult(success=False, error=str(exc), error_type="MISSING_TASK")
    except Exception as exc:  # noqa: BLE001
        return ReconstructionResult(success=False, error=str(exc), error_type="UNKNOWN_ERROR")


def _detect_task_source_shape(source: str) -> str | None:
    tree = ast.parse(source)
    task_names = _task_decorator_names(tree)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _has_task_decorator(node.decorator_list, task_names):
            return "class"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _has_task_decorator(
            node.decorator_list, task_names
        ):
            return "callable"
    return None


def _task_decorator_names(tree: ast.Module) -> set[str]:
    task_names = {"task"}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module not in {"shepherd", "shepherd_runtime.nucleus"}:
            continue
        for alias in node.names:
            if alias.name == "task":
                task_names.add(alias.asname or alias.name)
    return task_names


def _has_task_decorator(decorators: list[ast.expr], task_names: set[str]) -> bool:
    return any(_is_task_decorator(decorator, task_names) for decorator in decorators)


def _is_task_decorator(decorator: ast.expr, task_names: set[str]) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id in task_names
    if isinstance(target, ast.Attribute):
        return target.attr == "task"
    return False


def _syntax_reconstruction_error(exc: SyntaxError, source: str) -> ReconstructionError:
    return ReconstructionError(
        "SYNTAX_ERROR",
        str(exc),
        "Check for missing colons, parentheses, or invalid syntax.",
        line_number=exc.lineno,
        source_snippet=exc.text.strip() if exc.text else source[:100],
    )


FUNCTION_FORM_STANDARD_IMPORTS = [
    "from __future__ import annotations",
    "from dataclasses import dataclass",
    "from typing import Any, Annotated, Literal, Optional, Union",
    "from pydantic import BaseModel, Field",
]

FUNCTION_FORM_SHEPHERD_IMPORTS = [
    "from shepherd_runtime.nucleus import Artifact, Run, deliver, emit_artifact, task",
    "from shepherd_runtime.effects import Ask, Tell, ask, handle, tell",
]


def _reconstruct_callable_task(
    source: str,
    imports: list[str] | None,
    extra_namespace: dict[str, Any] | None,
    *,
    validate: bool,
) -> _RuntimeCallableTask[Any]:
    if validate:
        violations = validate_task_source(source)
        if violations:
            raise SourceValidationError(violations)

    module_name = f"shepherd_reconstructed_{uuid.uuid4().hex[:8]}"
    module = types.ModuleType(module_name)
    module.__dict__["__builtins__"] = __builtins__

    for imp in FUNCTION_FORM_STANDARD_IMPORTS + FUNCTION_FORM_SHEPHERD_IMPORTS:
        with contextlib.suppress(ImportError):
            exec(imp, module.__dict__)  # noqa: S102

    if imports:
        for imp in imports:
            with contextlib.suppress(ImportError):
                exec(imp, module.__dict__)  # noqa: S102

    if extra_namespace:
        module.__dict__.update(extra_namespace)

    pre_exec_object_ids = {id(obj) for obj in module.__dict__.values()}
    sys.modules[module_name] = module

    try:
        captured_source = textwrap.dedent(source)
        token = reconstruction_source.set(captured_source)
        try:
            exec(captured_source, module.__dict__)  # noqa: S102
        finally:
            reconstruction_source.reset(token)

        for obj in module.__dict__.values():
            if id(obj) in pre_exec_object_ids:
                continue
            if _is_callable_task(obj):
                return obj

        raise ReconstructionError(
            "MISSING_CALLABLE_TASK_DECORATOR",
            "No function-form @task callable found in source.",
            "Ensure the source includes a function decorated with @task, or use reconstruct_task_class() for classes.",
        )
    except SyntaxError as exc:
        raise _syntax_reconstruction_error(exc, source) from exc
    except NameError as exc:
        raise ReconstructionError(
            "UNDEFINED_NAME",
            str(exc),
            "A name is undefined. Check for missing imports or typos.",
            source_snippet=source[:100],
        ) from exc
    except ImportError as exc:
        raise ReconstructionError(
            "IMPORT_ERROR",
            str(exc),
            "An import failed. The module may not be available.",
            source_snippet=source[:100],
        ) from exc
    except TypeError as exc:
        msg = str(exc)
        if "type hints" in msg or "ForwardRef" in msg or "is not defined" in msg:
            raise ReconstructionError(
                "TYPE_HINT_ERROR",
                msg,
                "A type annotation could not be resolved. Check that all referenced types are imported or defined.",
                source_snippet=source[:100],
            ) from exc
        raise ReconstructionError(
            "TYPE_ERROR",
            msg,
            "Type error during callable task creation. Review decorator usage and annotations.",
            source_snippet=source[:100],
        ) from exc
    except ReconstructionError:
        raise
    except Exception as exc:
        msg = str(exc)
        if "Could not resolve type hints" in msg or "ForwardRef" in msg:
            raise ReconstructionError(
                "TYPE_HINT_ERROR",
                msg,
                "A type annotation could not be resolved. Check that all referenced types are imported or defined.",
                source_snippet=source[:100],
            ) from exc
        raise ReconstructionError(
            "UNKNOWN_ERROR",
            msg,
            "Unknown error. Review the full traceback.",
            recoverable=False,
            source_snippet=source[:100],
        ) from exc
    finally:
        sys.modules.pop(module_name, None)
