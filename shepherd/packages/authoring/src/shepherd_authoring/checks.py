"""Check predicate functions for filesystem-based validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def check_file_exists(path: Path) -> bool:
    """Check postcondition: file exists on disk and is non-empty."""
    return path.exists() and path.stat().st_size > 0


def check_document_structure(path: Path, required_sections: list[str]) -> bool:
    """Check postcondition: document contains required markdown sections."""
    if not path.exists():
        return False
    content = path.read_text()
    return all(f"## {section}" in content or f"# {section}" in content for section in required_sections)


def check_refinement_log(log_path: Path, min_iterations: int) -> bool:
    """Check postcondition: refinement log has expected iteration entries."""
    if not log_path.exists():
        return False
    content = log_path.read_text()
    return content.count("## Iteration") >= min_iterations


def check_version_history(versions_dir: Path, doc_name: str, min_versions: int) -> bool:
    """Check postcondition: version history has expected number of versions."""
    if not versions_dir.exists():
        return False
    versions = list(versions_dir.glob(f"{doc_name}.v*"))
    return len(versions) >= min_versions


__all__ = [
    "check_document_structure",
    "check_file_exists",
    "check_refinement_log",
    "check_version_history",
]
