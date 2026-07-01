"""Explicit runtime gateway for non-secure task reconstruction."""

from __future__ import annotations

import contextlib
import sys
import types
import uuid
from typing import Any

from shepherd_runtime.task.source_validation import SourceValidationError, validate_task_source

from ._source_state import reconstruction_source

try:
    from pydantic import ValidationError as PydanticValidationError
except ImportError:
    PydanticValidationError = None  # type: ignore[misc,assignment]

STANDARD_IMPORTS = [
    "from __future__ import annotations",
    "from pydantic import BaseModel, Field",
    "from typing import Any, Literal, Optional, Union, Annotated",
]

SHEPHERD_IMPORTS = [
    "from shepherd_runtime.task.authoring import Artifact, Context, Input, Output, task",
]

__all__ = [
    "ReconstructionError",
    "reconstruct_task_class",
]


class ReconstructionError(Exception):
    """Raised when task reconstruction fails."""

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


def reconstruct_task_class(
    source: str,
    imports: list[str] | None = None,
    extra_namespace: dict[str, Any] | None = None,
    *,
    validate: bool = True,
) -> type:
    """Reconstruct a @task class from source code."""
    if validate:
        violations = validate_task_source(source)
        if violations:
            raise SourceValidationError(violations)

    module_name = f"shepherd_reconstructed_{uuid.uuid4().hex[:8]}"
    module = types.ModuleType(module_name)
    module.__dict__["__builtins__"] = __builtins__

    for imp in STANDARD_IMPORTS + SHEPHERD_IMPORTS:
        with contextlib.suppress(ImportError):
            exec(imp, module.__dict__)  # noqa: S102

    if imports:
        for imp in imports:
            with contextlib.suppress(ImportError):
                exec(imp, module.__dict__)  # noqa: S102

    if extra_namespace:
        module.__dict__.update(extra_namespace)

    pre_exec_names = set(module.__dict__.keys())
    sys.modules[module_name] = module

    try:
        token = reconstruction_source.set(source)
        try:
            exec(source, module.__dict__)  # noqa: S102
        finally:
            reconstruction_source.reset(token)

        for name, obj in module.__dict__.items():
            if name in pre_exec_names:
                continue
            if extra_namespace and name in extra_namespace:
                continue
            if isinstance(obj, type) and hasattr(obj, "_task_meta"):
                return obj

        raise ReconstructionError(
            error_type="MISSING_TASK_DECORATOR",
            message="No @task class found in source",
            suggestion="Ensure the source includes a class decorated with @task.",
        )
    except SyntaxError as exc:
        line_number = exc.lineno
        snippet = exc.text.strip() if exc.text else source[:100]
        if "indent" in str(exc).lower():
            raise ReconstructionError(
                error_type="INDENTATION_ERROR",
                message=str(exc),
                suggestion="Fix indentation. The code may have mixed tabs/spaces.",
                line_number=line_number,
                source_snippet=snippet,
            ) from exc
        raise ReconstructionError(
            error_type="SYNTAX_ERROR",
            message=str(exc),
            suggestion="Check for missing colons, parentheses, or invalid syntax.",
            line_number=line_number,
            source_snippet=snippet,
        ) from exc
    except NameError as exc:
        raise ReconstructionError(
            error_type="UNDEFINED_NAME",
            message=str(exc),
            suggestion="A name is undefined. Check for missing imports or typos.",
            source_snippet=source[:100],
        ) from exc
    except ImportError as exc:
        raise ReconstructionError(
            error_type="IMPORT_ERROR",
            message=str(exc),
            suggestion="An import failed. The module may not be available.",
            source_snippet=source[:100],
        ) from exc
    except TypeError as exc:
        msg = str(exc)
        if "get_type_hints" in msg or "ForwardRef" in msg or "is not defined" in msg:
            raise ReconstructionError(
                error_type="TYPE_HINT_ERROR",
                message=msg,
                suggestion=(
                    "A type annotation could not be resolved. Check that all referenced types are imported or defined."
                ),
                source_snippet=source[:100],
            ) from exc
        raise ReconstructionError(
            error_type="TYPE_ERROR",
            message=msg,
            suggestion="Type error during class creation. Review decorator usage.",
            source_snippet=source[:100],
        ) from exc
    except ReconstructionError:
        raise
    except Exception as exc:
        if PydanticValidationError is not None and isinstance(exc, PydanticValidationError):
            raise ReconstructionError(
                error_type="PYDANTIC_ERROR",
                message=str(exc),
                suggestion=(
                    "Pydantic field validation failed. Check that field definitions match "
                    "Pydantic requirements (types, defaults, validators)."
                ),
                source_snippet=source[:100],
            ) from exc

        msg = str(exc)
        if "get_type_hints" in msg or "ForwardRef" in msg:
            raise ReconstructionError(
                error_type="TYPE_HINT_ERROR",
                message=msg,
                suggestion=(
                    "A type annotation could not be resolved. Check that all referenced types are imported or defined."
                ),
                source_snippet=source[:100],
            ) from exc

        if "Failed to extract type hints" in msg:
            raise ReconstructionError(
                error_type="TYPE_HINT_ERROR",
                message=msg,
                suggestion="A type annotation references an undefined name. Ensure all types are imported.",
                source_snippet=source[:100],
            ) from exc

        raise ReconstructionError(
            error_type="UNKNOWN_ERROR",
            message=msg,
            suggestion="Unknown error. Review the full traceback.",
            recoverable=False,
            source_snippet=source[:100],
        ) from exc
    finally:
        if module_name in sys.modules:
            del sys.modules[module_name]
