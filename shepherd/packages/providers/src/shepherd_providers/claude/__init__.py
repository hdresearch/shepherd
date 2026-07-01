"""Claude provider subpackage.

This module provides the ClaudeProvider implementation for the Claude Agent SDK.

Usage:
    from shepherd_providers.claude import ClaudeProvider

    provider = ClaudeProvider(
        name="analyst",
        model="claude-sonnet-4-20250514",
    )
"""

from shepherd_providers.claude.provider import ClaudeProvider

__all__ = ["ClaudeProvider"]
