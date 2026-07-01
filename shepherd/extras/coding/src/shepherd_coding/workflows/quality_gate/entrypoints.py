"""Quality gate utilities."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pipeline import PrePushQualityGate


def write_pr_description(result: PrePushQualityGate, output_dir: str = ".shepherd") -> str:
    """Write the generated PR description to a file.

    Returns the path to the written file.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / "pr-description.md"

    out_file.write_text((result.pr_title or "") + "\n\n" + (result.pr_body or ""))

    return str(out_file)
