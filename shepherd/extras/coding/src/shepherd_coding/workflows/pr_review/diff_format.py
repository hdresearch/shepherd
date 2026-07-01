"""Format PR diffs for LLM consumption.

Converts PRFile objects with raw unified diff patches into a
human-readable format with clear file headers, suitable for
inclusion in an LLM prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shepherd_coding.models import PRDetails


def format_diff_for_review(
    pr_details: PRDetails,
    *,
    skip_patterns: list[str] | None = None,
    max_patch_lines: int = 500,
) -> str:
    """Format PR diffs into a readable string for the reviewer.

    Produces a structured document with one section per changed file,
    each containing the unified diff. Files matching skip_patterns
    are omitted. Very large patches are truncated with a note.

    Args:
        pr_details: PR details with enriched file data (patch, status).
        skip_patterns: File glob patterns to exclude (e.g., ['*.lock']).
        max_patch_lines: Maximum diff lines per file before truncation.

    Returns:
        Formatted string suitable for the ``diff_text`` input of ReviewPR.
    """
    import fnmatch

    skip_patterns = skip_patterns or []
    sections: list[str] = []

    # Header with PR context
    sections.append(f"# Diffs for PR #{pr_details.number}: {pr_details.title}")
    sections.append(
        f"# {pr_details.additions} additions, {pr_details.deletions} deletions across {pr_details.changed_files} files"
    )
    sections.append("")

    skipped_files: list[str] = []

    for f in pr_details.files:
        # Check skip patterns
        if any(fnmatch.fnmatch(f.path, pat) for pat in skip_patterns):
            skipped_files.append(f.path)
            continue

        # File header
        status_label = f.status
        if f.status == "renamed" and f.previous_path:
            status_label = f"renamed from {f.previous_path}"

        sections.append(f"## File: {f.path}  ({status_label}, +{f.additions}/-{f.deletions})")

        if f.patch:
            lines = f.patch.split("\n")
            if len(lines) > max_patch_lines:
                sections.append("```diff")
                sections.append("\n".join(lines[:max_patch_lines]))
                sections.append(f"... ({len(lines) - max_patch_lines} more lines truncated)")
                sections.append("```")
            else:
                sections.append("```diff")
                sections.append(f.patch)
                sections.append("```")
        elif f.status == "removed":
            sections.append("*(file deleted)*")
        else:
            sections.append("*(binary file or no diff available)*")

        sections.append("")

    if skipped_files:
        sections.append(f"# Skipped files ({len(skipped_files)}): {', '.join(skipped_files)}")

    return "\n".join(sections)
