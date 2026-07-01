"""Shared subprocess helper for programmatic tool runner tasks.

Encapsulates the common pattern: locate binary → skip if missing →
run subprocess with timeout → capture output → return ToolRunResult.
"""

from __future__ import annotations

import shutil
import subprocess

from shepherd_coding.models import ToolRunResult


def run_tool(
    *,
    binary: str,
    tool_name: str,
    cmd: list[str],
    cwd: str,
    timeout: int = 60,
    parse_output: callable | None = None,
) -> ToolRunResult:
    """Run an external tool and return a structured result.

    Args:
        binary: Name of the binary to locate via ``shutil.which()``.
        tool_name: Human-readable tool name for the result record.
        cmd: Full command list (must already include the resolved binary path).
        cwd: Working directory for the subprocess.
        timeout: Maximum seconds before the process is killed.
        parse_output: Optional callable ``(str) -> list[Issue]`` to extract
            structured issues from the combined stdout+stderr output.

    Returns:
        A ``ToolRunResult`` with pass/fail, optional issues, and raw output.
        If the binary is not on PATH, returns a skipped result.
    """
    bin_path = shutil.which(binary)
    if bin_path is None:
        return ToolRunResult(
            tool=tool_name,
            passed=True,
            skipped=True,
            skip_reason=f"{binary} not found on PATH",
        )

    try:
        proc = subprocess.run(
            cmd,
            check=False,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolRunResult(
            tool=tool_name,
            passed=False,
            raw_output=f"Timed out after {timeout}s",
        )

    combined = (proc.stdout + "\n" + proc.stderr).strip()
    findings = parse_output(combined) if parse_output else []

    return ToolRunResult(
        tool=tool_name,
        passed=proc.returncode == 0,
        findings=findings,
        raw_output=combined[:5000],
    )
