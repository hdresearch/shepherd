"""VCR utilities for recording and replaying API interactions.

Use these utilities to create deterministic integration tests
that don't require network access after initial recording.

Requires: pip install shepherd-tests[vcr]
"""

from pathlib import Path
from typing import Any

# VCR is an optional dependency
try:
    import vcr

    HAS_VCR = True
except ImportError:
    HAS_VCR = False
    vcr = None  # type: ignore


def get_vcr_config(
    cassette_dir: str | Path = "cassettes",
    record_mode: str = "once",
    filter_headers: list[str] | None = None,
    filter_query_parameters: list[str] | None = None,
) -> dict[str, Any]:
    """Get VCR configuration for Shepherd provider tests.

    Args:
        cassette_dir: Directory to store cassettes.
        record_mode: VCR record mode ("once", "new_episodes", "none", "all").
        filter_headers: Headers to redact from recordings.
        filter_query_parameters: Query parameters to redact.

    Returns:
        VCR configuration dictionary.

    Example:
        @pytest.fixture(scope="module")
        def vcr_config():
            return get_vcr_config(
                cassette_dir="tests/cassettes",
                filter_headers=["x-api-key", "authorization"],
            )
    """
    if filter_headers is None:
        filter_headers = [
            "authorization",
            "x-api-key",
            "anthropic-api-key",
            "openai-api-key",
        ]

    if filter_query_parameters is None:
        filter_query_parameters = ["api_key", "key"]

    return {
        "cassette_library_dir": str(cassette_dir),
        "record_mode": record_mode,
        "match_on": ["method", "scheme", "host", "port", "path", "query"],
        "filter_headers": filter_headers,
        "filter_query_parameters": filter_query_parameters,
        "before_record_response": _scrub_response_headers,
    }


def _scrub_response_headers(response: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive headers from recorded responses."""
    if "headers" in response:
        # Keep only non-sensitive headers
        safe_headers = {
            k: v
            for k, v in response["headers"].items()
            if k.lower()
            not in {
                "set-cookie",
                "x-request-id",
                "x-trace-id",
                "date",
            }
        }
        response["headers"] = safe_headers
    return response


def create_vcr_cassette(
    cassette_path: str | Path,
    record_mode: str = "once",
    **kwargs: Any,
) -> Any:
    """Create a VCR cassette context manager.

    Args:
        cassette_path: Path to the cassette file.
        record_mode: VCR record mode.
        **kwargs: Additional VCR options.

    Returns:
        VCR cassette context manager.

    Raises:
        ImportError: If vcrpy is not installed.

    Example:
        with create_vcr_cassette("tests/cassettes/test_api.yaml"):
            # API calls are recorded/replayed
            response = make_api_call()
    """
    if not HAS_VCR:
        raise ImportError("vcrpy is required for VCR cassettes. Install with: pip install shepherd-tests[vcr]")

    config = get_vcr_config(**kwargs)
    my_vcr = vcr.VCR(**config)
    return my_vcr.use_cassette(str(cassette_path), record_mode=record_mode)
