"""Explicit runtime gateway for shared task source-validation helpers."""

from __future__ import annotations

import ast

__all__ = [
    "FORBIDDEN_ATTRIBUTES",
    "FORBIDDEN_IMPORTS",
    "FORBIDDEN_NAMES",
    "SourceValidationError",
    "validate_task_source",
]


class SourceValidationError(Exception):
    """Raised when source code fails security validation."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__(f"Source validation failed: {violations}")


FORBIDDEN_IMPORTS = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "ctypes",
        "importlib",
        "builtins",
        "socket",
        "multiprocessing",
        "threading",
        "signal",
        "shutil",
        "tempfile",
        "pathlib",
        "io",
    }
)

FORBIDDEN_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "globals",
        "locals",
        "vars",
        "dir",
        "breakpoint",
        "input",
        "memoryview",
    }
)

FORBIDDEN_ATTRIBUTES = frozenset(
    {
        "__class__",
        "__bases__",
        "__subclasses__",
        "__mro__",
        "__globals__",
        "__code__",
        "__builtins__",
        "__reduce__",
        "__reduce_ex__",
        "__getstate__",
        "__setstate__",
    }
)


def validate_task_source(source: str, *, strict: bool = True) -> list[str]:
    """Validate task source code for dangerous patterns."""
    violations = []

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"Syntax error: {exc}"]

    forbidden = FORBIDDEN_IMPORTS
    if not strict:
        forbidden = forbidden - {"pathlib"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in forbidden:
                    violations.append(f"Line {node.lineno}: Forbidden import '{alias.name}'")

        if isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in forbidden:
                violations.append(f"Line {node.lineno}: Forbidden import from '{node.module}'")

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_NAMES:
            violations.append(f"Line {node.lineno}: Forbidden call '{node.func.id}()'")

        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            violations.append(f"Line {node.lineno}: Forbidden attribute '{node.attr}'")

    return violations
