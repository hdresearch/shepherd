"""Tests for thread-safe SDK cache initialization.

Verifies:
- Concurrent calls to _get_sdk() don't cause race conditions
- SDK is imported exactly once
- Fast path avoids lock contention
"""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest


class TestSDKCacheThreadSafety:
    """Tests for thread-safe SDK cache."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset SDK cache before each test."""
        from shepherd_providers.claude.provider import _reset_sdk_cache

        _reset_sdk_cache()
        yield
        _reset_sdk_cache()

    def test_concurrent_calls_to_get_sdk(self):
        """Concurrent calls should not cause race conditions."""
        import builtins

        from shepherd_providers.claude import provider

        # Track import attempts
        import_count = {"count": 0}

        # Mock the SDK import to track how many times it's called
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "claude_agent_sdk":
                import_count["count"] += 1
                # Simulate import failure (SDK not available)
                raise ImportError("SDK not available")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            # Reset cache
            provider._sdk_cache = provider._SDKCache(provider._SDKStatus.NOT_ATTEMPTED)

            def worker():
                """Worker function that calls _get_sdk()."""
                return provider._get_sdk()

            # Run multiple threads concurrently
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(worker) for _ in range(20)]
                results = [f.result() for f in futures]

            # All should return None (SDK not available)
            assert all(r is None for r in results)

            # Import should have been attempted only once despite 20 concurrent calls
            assert import_count["count"] == 1

    def test_sdk_cache_is_idempotent(self):
        """Multiple calls should be safe and return the same result."""
        from shepherd_providers.claude.provider import _get_sdk, _reset_sdk_cache

        _reset_sdk_cache()

        # Call multiple times
        result1 = _get_sdk()
        result2 = _get_sdk()
        result3 = _get_sdk()

        # All should return the same result (either None or the SDK dict)
        assert result1 == result2 == result3

    def test_fast_path_avoids_lock_when_available(self):
        """Fast path should avoid lock when SDK is already cached."""
        import time

        from shepherd_providers.claude import provider

        # Set cache to AVAILABLE state
        mock_sdk = {"query": MagicMock()}
        provider._sdk_cache = provider._SDKCache(provider._SDKStatus.AVAILABLE, mock_sdk)

        # Time many fast-path calls
        start = time.perf_counter()
        for _ in range(1000):
            result = provider._get_sdk()
            assert result == mock_sdk
        duration = time.perf_counter() - start

        # Should be very fast (under 10ms for 1000 calls)
        assert duration < 0.01

    def test_fast_path_avoids_lock_when_unavailable(self):
        """Fast path should avoid lock when SDK is known unavailable."""
        import time

        from shepherd_providers.claude import provider

        # Set cache to UNAVAILABLE state
        provider._sdk_cache = provider._SDKCache(provider._SDKStatus.UNAVAILABLE)

        # Time many fast-path calls
        start = time.perf_counter()
        for _ in range(1000):
            result = provider._get_sdk()
            assert result is None
        duration = time.perf_counter() - start

        # Should be very fast (under 10ms for 1000 calls)
        assert duration < 0.01

    def test_concurrent_first_calls_with_successful_import(self):
        """Concurrent first calls should handle successful SDK import correctly."""
        import builtins

        from shepherd_providers.claude import provider

        # Create mock SDK
        mock_sdk_objects = {
            "query": MagicMock(),
            "tool": MagicMock(),
            "create_sdk_mcp_server": MagicMock(),
            "ClaudeAgentOptions": MagicMock(),
            "AssistantMessage": MagicMock(),
            "ResultMessage": MagicMock(),
            "TextBlock": MagicMock(),
            "ThinkingBlock": MagicMock(),
            "ToolUseBlock": MagicMock(),
            "ToolResultBlock": MagicMock(),
        }

        import_count = {"count": 0}
        original_import = builtins.__import__

        def mock_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "claude_agent_sdk":
                import_count["count"] += 1
                # Return a mock module
                mock_module = MagicMock()
                for key, value in mock_sdk_objects.items():
                    setattr(mock_module, key, value)
                return mock_module
            # For other imports, use real import
            return original_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=mock_import):
            # Reset cache
            provider._sdk_cache = provider._SDKCache(provider._SDKStatus.NOT_ATTEMPTED)

            def worker():
                """Worker function that calls _get_sdk()."""
                result = provider._get_sdk()
                return result is not None

            # Run multiple threads concurrently
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(worker) for _ in range(20)]
                results = [f.result() for f in futures]

            # All should succeed
            assert all(results)

            # Import should have been attempted only once
            assert import_count["count"] == 1

            # Cache should be in AVAILABLE state
            assert provider._sdk_cache.status == provider._SDKStatus.AVAILABLE

    def test_reset_sdk_cache_clears_state(self):
        """_reset_sdk_cache() should clear the cache state."""
        from shepherd_providers.claude import provider

        # Set cache to AVAILABLE
        mock_sdk = {"query": MagicMock()}
        provider._sdk_cache = provider._SDKCache(provider._SDKStatus.AVAILABLE, mock_sdk)

        # Reset
        provider._reset_sdk_cache()

        # Should be back to NOT_ATTEMPTED
        assert provider._sdk_cache.status == provider._SDKStatus.NOT_ATTEMPTED
        assert provider._sdk_cache.sdk is None

    def test_sdk_available_uses_get_sdk(self):
        """_sdk_available() should use _get_sdk() internally."""
        from shepherd_providers.claude import provider

        with patch.object(provider, "_get_sdk") as mock_get_sdk:
            mock_get_sdk.return_value = {"query": MagicMock()}

            result = provider._sdk_available()

            assert result is True
            mock_get_sdk.assert_called_once()

        with patch.object(provider, "_get_sdk") as mock_get_sdk:
            mock_get_sdk.return_value = None

            result = provider._sdk_available()

            assert result is False
            mock_get_sdk.assert_called_once()
