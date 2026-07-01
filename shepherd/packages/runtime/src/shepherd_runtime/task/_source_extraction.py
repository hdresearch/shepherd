"""Legacy private shim for shared task source-analysis helpers."""

from __future__ import annotations

from .source_analysis import (
    SourceExtractionError,
    extract_imports_from_file,
    extract_imports_from_source,
    extract_referenced_names,
    extract_task_imports,
    extract_task_source,
    extract_task_with_imports,
)

__all__ = [
    "SourceExtractionError",
    "extract_imports_from_file",
    "extract_imports_from_source",
    "extract_referenced_names",
    "extract_task_imports",
    "extract_task_source",
    "extract_task_with_imports",
]
