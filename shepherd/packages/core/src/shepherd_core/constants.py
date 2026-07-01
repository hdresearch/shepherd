"""Global constants for the shepherd framework.

This module defines system-wide constants that are shared across packages.
"""

# SDK transport buffer limit (1MB)
# This is the maximum buffer size used by the Claude Agent SDK for transferring
# data between the host and SDK subprocess. Commands that produce output larger
# than this will cause buffer overflow errors.
SDK_BUFFER_LIMIT_BYTES = 1_048_576  # 1MB


__all__ = ["SDK_BUFFER_LIMIT_BYTES"]
