from __future__ import annotations

import pytest

from shepherd_dialect.runtime_options import RuntimeOptionsError, parse_runtime_options


def test_runtime_options_reject_authority_shaped_may_field() -> None:
    with pytest.raises(RuntimeOptionsError, match=r"unknown runtime field\(s\): may"):
        parse_runtime_options({"may": "Permissive"})


@pytest.mark.parametrize(
    "field",
    [
        "session",
        "budget",
        "budget_seconds",
        "timeout",
        "device",
        "plan",
        "world",
        "tools",
        "max_turns",
        "provider_options",
    ],
)
def test_runtime_options_reject_reserved_future_fields(field: str) -> None:
    with pytest.raises(RuntimeOptionsError, match=f"runtime field\\(s\\) reserved for future use: {field}"):
        parse_runtime_options({field: "reserved"})


@pytest.mark.parametrize(
    ("runtime", "message"),
    [
        ([], "runtime must be an object"),
        ({"trace": "launch"}, "runtime.trace must be an object"),
        ({"trace": {"unknown": True}}, "unknown runtime.trace field"),
        ({"trace": {"label": ""}}, "runtime.trace.label must be a non-empty string"),
        ({"trace": {"tags": "visual"}}, "runtime.trace.tags must be a list or tuple"),
        ({"trace": {"tags": ["visual", "visual"]}}, "duplicate runtime.trace tag"),
        ({"provider": {}}, "runtime.provider.id must be a non-empty string"),
        ({"provider": {"id": "static", "tools": []}}, "unknown runtime.provider field"),
        ({"model": {}}, "runtime.model.name must be a non-empty string"),
        ({"model": {"name": "sonnet", "max_turns": 1}}, "unknown runtime.model field"),
    ],
)
def test_runtime_options_reject_malformed_nested_fields(runtime: object, message: str) -> None:
    with pytest.raises(RuntimeOptionsError, match=message):
        parse_runtime_options(runtime)


def test_runtime_options_canonicalize_supported_shorthand() -> None:
    options = parse_runtime_options(
        {
            "trace": {"label": "launch", "tags": ["visual", "static"]},
            "provider": "static-mock",
            "model": "sonnet",
        }
    )

    assert options.to_payload() == {
        "trace": {"label": "launch", "tags": ["visual", "static"]},
        "provider": {"id": "static-mock"},
        "model": {"name": "sonnet"},
    }
