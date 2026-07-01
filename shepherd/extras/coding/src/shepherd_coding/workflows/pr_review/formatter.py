"""Terminal formatter for PR review results.

Renders CodeFinding (or ReviewFinding) objects in a format resembling
GitHub review comments, enabling quality evaluation without publishing.
"""

from __future__ import annotations

from typing import Any, TextIO

# Severity indicators (work in terminals with Unicode support)
_SEVERITY_ICONS = {
    "blocker": "\u2b24 BLOCKER",  # ⬤
    "error": "\u2b24 ERROR",  # ⬤
    "warning": "\u25b2 WARNING",  # ▲
    "suggestion": "\u25c6 SUGGESTION",  # ◆
    "nit": "\u25cb NIT",  # ○
}

_SEVERITY_ORDER = {"blocker": 0, "error": 1, "warning": 2, "suggestion": 3, "nit": 4}


def format_review(
    result: Any,
    *,
    max_comments: int | None = None,
    file: TextIO | None = None,
) -> str:
    """Render a PRReview pipeline result to a human-readable string.

    Args:
        result: Completed PRReview task instance with summary, findings,
            approval, score, and stages attributes.
        max_comments: Override for max findings to show. If None, shows all.
        file: If provided, also writes the output to this stream.

    Returns:
        The formatted string.
    """
    lines: list[str] = []
    w = lines.append

    # Extract fields from the result
    summary: str = result.summary or ""
    findings: list[Any] = result.findings or []
    approval: str = result.approval or "COMMENT"
    score: float = result.score or 0.0
    stages: dict[str, Any] = dict(result.stages or {})

    # Determine PR info from stages
    pr_title = ""
    pr_number = 0
    if "fetch" in stages and stages["fetch"] is not None:
        fetch = stages["fetch"]
        details = getattr(fetch, "details", None)
        if details:
            pr_title = getattr(details, "title", "")
            pr_number = getattr(details, "number", 0)

    # Header
    header = f"PR #{pr_number}: {pr_title}" if pr_title else "PR Review"
    w(f"\n\u2500\u2500 {header} " + "\u2500" * max(0, 70 - len(header) - 4))
    w("")

    # Summary
    if summary:
        w(f"Summary: {summary}")
        w("")

    w(f"Score: {score:.2f}  Recommendation: {approval}")

    # Findings
    sorted_findings = sorted(findings, key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), f.confidence != "high"))

    if max_comments is not None:
        displayed = sorted_findings[:max_comments]
        hidden = len(sorted_findings) - len(displayed)
    else:
        displayed = sorted_findings
        hidden = 0

    w("")
    w(f"\u2500\u2500 Findings ({len(sorted_findings)}) " + "\u2500" * 54)

    if not displayed:
        w("")
        w("  No findings.")
    else:
        for f in displayed:
            w("")
            icon = _SEVERITY_ICONS.get(f.severity, f.severity.upper())
            w(f"  {icon}  {f.category}  {f.confidence} confidence")

            loc = f"   {f.file_path}:{f.line_start}"
            if f.line_end and f.line_end != f.line_start:
                loc += f"-{f.line_end}"
            w(loc)

            w(f"   {f.title}")
            w("")
            for body_line in f.body.split("\n"):
                w(f"   {body_line}")

    if hidden > 0:
        w("")
        w(f"  ... and {hidden} more finding{'s' if hidden != 1 else ''} (increase max_comments to see all)")

    # Stage summary
    w("")
    w("\u2500\u2500 Stage Summary " + "\u2500" * 55)

    for stage_name in ["fetch", "checkout", "triage", "verify", "review"]:
        if stage_name not in stages:
            continue
        stage = stages[stage_name]
        if stage is None:
            w(f"  {stage_name:<12} \u2013  skipped")
            continue

        detail_parts: list[str] = []

        # Triage details
        if stage_name == "triage":
            cat = getattr(stage, "category", None)
            risk = getattr(stage, "risk_level", None)
            if cat:
                detail_parts.append(f"category={cat}")
            if risk:
                detail_parts.append(f"risk={risk}")

        # Review details
        if stage_name == "review":
            stage_findings = getattr(stage, "findings", None) or getattr(stage, "review_comments", None) or []
            if stage_findings:
                by_sev: dict[str, int] = {}
                for sf in stage_findings:
                    sev = getattr(sf, "severity", "unknown")
                    by_sev[sev] = by_sev.get(sev, 0) + 1
                parts = [
                    f"{count} {sev}"
                    for sev, count in sorted(by_sev.items(), key=lambda x: _SEVERITY_ORDER.get(x[0], 99))
                ]
                detail_parts.append(", ".join(parts))

        # Verify details
        if stage_name == "verify":
            ts = getattr(stage, "test_success", None)
            bs = getattr(stage, "build_success", None)
            if bs is not None:
                detail_parts.append(f"build={'pass' if bs else 'FAIL'}")
            if ts is not None:
                detail_parts.append(f"tests={'pass' if ts else 'FAIL'}")

        detail = "  ".join(detail_parts)
        w(f"  {stage_name:<12} \u2713  {detail}")

    w("")

    output = "\n".join(lines)
    if file is not None:
        file.write(output)
    return output
