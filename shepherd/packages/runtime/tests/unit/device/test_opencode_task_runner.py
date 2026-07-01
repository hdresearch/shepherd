"""Tests for task_runner OpenCode integration points.

Verifies that the task_runner can create OpenCode providers from config
and that session validation is bypassed for non-Claude providers.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


class TestOpenCodeAutoDetect:
    """_create_provider should auto-detect 'opencode' provider type."""

    def test_creates_opencode_provider(self) -> None:
        from shepherd_runtime.device.container.provider_execution import _create_provider

        config = {
            "provider_type": "opencode",
            "name": "test",
            "model": "anthropic/claude-sonnet-4-20250514",
        }
        provider = _create_provider(config)

        assert provider.provider_id.startswith("provider:opencode:")
        assert provider.name == "test"  # type: ignore[attr-defined]
        assert provider.model == "anthropic/claude-sonnet-4-20250514"  # type: ignore[attr-defined]

    def test_creates_with_container_env(self) -> None:
        from shepherd_runtime.device.container.provider_execution import _create_provider

        config = {
            "provider_type": "opencode",
            "name": "test",
            "container_env": ["GOOGLE_API_KEY"],
        }
        provider = _create_provider(config)
        assert provider.container_env == ("GOOGLE_API_KEY",)  # type: ignore[attr-defined]

    def test_opencode_not_available_raises(self) -> None:
        """If shepherd_providers.opencode can't be imported, raise ProviderNotAvailableError."""
        from shepherd_runtime.device.container.provider_execution import (
            ProviderNotAvailableError,
            _create_provider,
        )
        from shepherd_runtime.device.container.provider_registry import (
            _PROVIDER_FACTORIES,
        )

        # Remove the factory registration so _create_provider falls through
        # to the auto-detect branch, then simulate the module being unavailable
        saved = _PROVIDER_FACTORIES.pop("opencode", None)
        try:
            with (
                patch.dict(sys.modules, {"shepherd_providers.opencode": None}),
                pytest.raises(ProviderNotAvailableError, match="opencode"),
            ):
                _create_provider({"provider_type": "opencode"})
        finally:
            if saved is not None:
                _PROVIDER_FACTORIES["opencode"] = saved


class TestSessionValidationBypass:
    """Session validation should be skipped for non-Claude providers."""

    def test_session_validation_skipped_for_opencode(self) -> None:
        """_validate_session_resumable should not be called for opencode."""
        from shepherd_runtime.device.container.provider_execution import (
            _validate_session_resumable,
        )

        # For a non-existent session path, Claude would return None (fallback)
        result = _validate_session_resumable("fake-session-id", "/tmp/test")
        # This returns None because the transcript file doesn't exist
        assert result is None

        # The key test is in _enforce_container_session_invariants where the
        # guard checks provider_type. We verify the guard is present by
        # checking the source:
        import inspect

        from shepherd_runtime.device.container import provider_execution

        source = inspect.getsource(provider_execution._enforce_container_session_invariants)
        assert 'provider_type") == "claude"' in source


class TestContainerEnvForwarding:
    """_generate_rebind_env should forward container_env from provider config."""

    def test_forwards_container_env_vars(self) -> None:
        from shepherd_runtime.device.container.device import ContainerDevice

        device = ContainerDevice.__new__(ContainerDevice)

        sandbox = MagicMock()
        sandbox.overlays = {}

        provider_config = {
            "provider_type": "opencode",
            "container_env": ["MY_CUSTOM_KEY", "ANOTHER_KEY"],
        }

        with patch.dict("os.environ", {"MY_CUSTOM_KEY": "secret123", "ANTHROPIC_API_KEY": "ak"}):
            env = device._generate_rebind_env(sandbox, provider_config=provider_config)

        assert env["MY_CUSTOM_KEY"] == "secret123"
        assert env["ANTHROPIC_API_KEY"] == "ak"
        # ANOTHER_KEY not in env, so not forwarded
        assert "ANOTHER_KEY" not in env

    def test_no_provider_config_still_works(self) -> None:
        from shepherd_runtime.device.container.device import ContainerDevice

        device = ContainerDevice.__new__(ContainerDevice)

        sandbox = MagicMock()
        sandbox.overlays = {}

        env = device._generate_rebind_env(sandbox)

        # Should return a valid env dict without errors when no provider_config
        assert isinstance(env, dict)
