"""OpenCode provider for the Shepherd framework.

Delegates execution to a local OpenCode server process, supporting
75+ models across multiple providers.
"""

from shepherd_providers.opencode.provider import OpenCodeProvider

__all__ = ["OpenCodeProvider"]
