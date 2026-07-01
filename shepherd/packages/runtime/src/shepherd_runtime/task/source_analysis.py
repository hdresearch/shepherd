"""Explicit runtime gateway for shared task source-analysis helpers."""

from __future__ import annotations

import ast
import inspect
import textwrap

__all__ = [
    "SourceExtractionError",
    "extract_imports_from_file",
    "extract_imports_from_source",
    "extract_referenced_names",
    "extract_task_imports",
    "extract_task_source",
    "extract_task_with_imports",
]


class SourceExtractionError(Exception):
    """Raised when task source cannot be extracted."""


def extract_task_source(task_class: type) -> str:
    """Extract complete source code of a @task class."""
    if not hasattr(task_class, "_task_meta"):
        raise SourceExtractionError(f"Class {task_class.__name__} is not decorated with @task")

    stored_source = getattr(task_class, "_task_source", None)
    if stored_source is not None:
        return stored_source  # type: ignore[no-any-return]

    try:
        source = inspect.getsource(task_class)
    except (OSError, TypeError) as exc:
        raise SourceExtractionError(f"Cannot extract source for {task_class.__name__}: {exc}") from exc

    if source.startswith((" ", "\t")):
        source = textwrap.dedent(source)

    return source


def extract_task_imports(task_class: type) -> list[str]:
    """Extract import statements from the file containing a task class."""
    source_file = inspect.getsourcefile(task_class)
    if source_file is None:
        raise SourceExtractionError(f"Cannot find source file for {task_class.__name__}")

    return extract_imports_from_file(source_file)


def extract_imports_from_file(file_path: str) -> list[str]:
    """Extract all import statements from a Python file."""
    with open(file_path, encoding="utf-8") as handle:
        try:
            tree = ast.parse(handle.read())
        except SyntaxError as exc:
            raise SourceExtractionError(f"Syntax error in {file_path}: {exc}") from exc

    imports = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.unparse(node))

    return imports


def extract_imports_from_source(source: str) -> list[str]:
    """Extract import statements from a source string."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    imports = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.unparse(node))

    return imports


def extract_referenced_names(source: str) -> set[str]:
    """Extract names referenced in source code."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            current = node
            while isinstance(current, ast.Attribute):
                current = current.value  # type: ignore[assignment]
            if isinstance(current, ast.Name):
                names.add(current.id)

    return names


def extract_task_with_imports(task_class: type) -> tuple[str, list[str]]:
    """Extract task source and source-file imports together."""
    return extract_task_source(task_class), extract_task_imports(task_class)
