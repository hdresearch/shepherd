"""Public text utilities shared across kernel and runtime packages."""

from __future__ import annotations


def smart_truncate(
    text: str,
    max_len: int = 500,
    tail_ratio: float = 0.3,
    *,
    min_for_split: int = 80,
) -> str:
    """Truncate text preserving both head and tail."""
    if len(text) <= max_len:
        return text

    if max_len < min_for_split:
        return text[: max_len - 3] + "..."

    omitted = len(text) - max_len
    marker_base_len = 15
    marker_num_len = len(str(omitted + marker_base_len))
    marker_len = marker_base_len + marker_num_len

    available = max_len - marker_len
    if available < 10:
        return text[: max_len - 3] + "..."

    tail_len = int(available * tail_ratio)
    head_len = available - tail_len

    head = text[:head_len]
    tail = text[-tail_len:] if tail_len > 0 else ""

    actual_omitted = len(text) - len(head) - len(tail)
    marker = f"...[{actual_omitted} chars]..."

    return head + marker + tail


__all__ = ["smart_truncate"]
