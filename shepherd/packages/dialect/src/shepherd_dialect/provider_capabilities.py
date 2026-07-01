"""Dialect-native provider capability claims.

The manifest in this module is intentionally separate from the legacy
``shepherd_core.ProviderCapabilities`` type. VcsCore-native providers expose the
capabilities they can prove through the confined execution-provider path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProviderTransport = Literal["deterministic_fake", "headless_cli", "agent_sdk_worker"]

READ_FILE = "read_file"
WRITE_FILE = "write_file"
EDIT_FILE = "edit_file"
SEARCH_FILES = "search_files"
SEARCH_CONTENT = "search_content"
BASH = "bash"

CANONICAL_WORKSPACE_TOOL_NAMES = frozenset(
    {
        READ_FILE,
        WRITE_FILE,
        EDIT_FILE,
        SEARCH_FILES,
        SEARCH_CONTENT,
        BASH,
    }
)

_NATIVE_TOOL_TO_CANONICAL = {
    "Read": READ_FILE,
    "Write": WRITE_FILE,
    "Edit": EDIT_FILE,
    "Glob": SEARCH_FILES,
    "Grep": SEARCH_CONTENT,
    "Bash": BASH,
    "read_file": READ_FILE,
    "write_file": WRITE_FILE,
    "edit_file": EDIT_FILE,
    "search_files": SEARCH_FILES,
    "search_content": SEARCH_CONTENT,
    "bash": BASH,
}


@dataclass(frozen=True)
class AgentProviderCapabilities:
    """Executable capability claim for a VcsCore-native agent provider."""

    provider_id: str
    transport: ProviderTransport
    confined: bool
    network_required: bool
    structured_output: bool
    session_resume: bool
    workspace_tools: frozenset[str]
    custom_tools: bool = False
    mcp: bool = False

    def __post_init__(self) -> None:
        if not self.provider_id:
            raise ValueError("provider_id must be non-empty")
        unknown = self.workspace_tools - CANONICAL_WORKSPACE_TOOL_NAMES
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"unknown canonical workspace tools: {names}")


def canonical_tool_name(native_name: str) -> str | None:
    """Return the canonical workspace-tool name for a provider-native name."""
    return _NATIVE_TOOL_TO_CANONICAL.get(native_name)


def canonical_tool_payload(native_name: str) -> dict[str, str]:
    """Return payload fields for a native tool name plus stable canonical name."""
    payload = {"tool_name": native_name}
    canonical = canonical_tool_name(native_name)
    if canonical is not None:
        payload["canonical_tool_name"] = canonical
    return payload


__all__ = [
    "BASH",
    "CANONICAL_WORKSPACE_TOOL_NAMES",
    "EDIT_FILE",
    "READ_FILE",
    "SEARCH_CONTENT",
    "SEARCH_FILES",
    "WRITE_FILE",
    "AgentProviderCapabilities",
    "ProviderTransport",
    "canonical_tool_name",
    "canonical_tool_payload",
]
