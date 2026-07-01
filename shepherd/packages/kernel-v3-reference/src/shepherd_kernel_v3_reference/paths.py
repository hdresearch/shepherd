"""Stable semantic path refs shared by kernel, trace, and admission code."""

from __future__ import annotations


def source_path_ref(selection_ref: str, source_ref: str, branch_ref: str) -> str:
    """Path consumed when a selected continuation source is resumed."""

    return f"path:{selection_ref}/{source_ref}/{branch_ref}"


def unhandled_source_path_ref(source_ref: str, branch_ref: str) -> str:
    """Path consumed when an unhandled top-level continuation source is resumed."""

    return f"path:unhandled/{source_ref}/{branch_ref}"
