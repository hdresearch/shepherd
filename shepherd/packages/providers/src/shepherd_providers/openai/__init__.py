"""OpenAI provider subpackage.

This module provides the OpenAIProvider implementation for the OpenAI Agents SDK.

Usage:
    from shepherd_providers.openai import OpenAIProvider

    provider = OpenAIProvider(
        name="fetcher",
        model="gpt-4o",
    )
"""

from shepherd_providers.openai.provider import OpenAIProvider

__all__ = ["OpenAIProvider"]
