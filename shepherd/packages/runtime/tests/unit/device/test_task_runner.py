"""Unit tests for container task runner.

Tests the task runner functions used inside containers:
- load_rebind_env: Environment variable loading from file
- load_input / write_output: JSON I/O for task data
- _create_provider: Provider creation with fallback
- _build_binding_from_contexts: ProviderBinding construction
- _serialize_execution_result: Result serialization
"""

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from shepherd_core.provider import DefaultProviderRuntime
from shepherd_runtime.device.container.task_runner import (
    ProviderNotAvailableError,
    _build_binding_from_contexts,
    _create_provider,
    _discover_fuse_layers,
    _MockProvider,
    _serialize_execution_result,
    _validate_session_resumable,
    load_input,
    load_rebind_env,
    write_error,
    write_output,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# =============================================================================
# load_rebind_env Tests
# =============================================================================


class TestLoadRebindEnv:
    """Tests for rebind environment loading."""

    def test_load_empty_file(self, temp_dir):
        """Empty file returns empty dict."""
        env_file = temp_dir / "rebind.env"
        env_file.write_text("")

        result = load_rebind_env(env_file)

        assert result == {}

    def test_load_simple_vars(self, temp_dir):
        """Simple KEY=VALUE pairs are parsed."""
        env_file = temp_dir / "rebind.env"
        env_file.write_text("WORKSPACE_PATH=/container/workspace\nSESSION_PATH=/container/.claude\n")

        result = load_rebind_env(env_file)

        assert result == {
            "WORKSPACE_PATH": "/container/workspace",
            "SESSION_PATH": "/container/.claude",
        }

    def test_ignore_comments(self, temp_dir):
        """Lines starting with # are ignored."""
        env_file = temp_dir / "rebind.env"
        env_file.write_text("# This is a comment\nPATH=/foo\n# Another comment\n")

        result = load_rebind_env(env_file)

        assert result == {"PATH": "/foo"}

    def test_ignore_empty_lines(self, temp_dir):
        """Empty lines are ignored."""
        env_file = temp_dir / "rebind.env"
        env_file.write_text("KEY1=val1\n\n\nKEY2=val2\n")

        result = load_rebind_env(env_file)

        assert result == {"KEY1": "val1", "KEY2": "val2"}

    def test_handle_values_with_equals(self, temp_dir):
        """Values containing = are handled correctly."""
        env_file = temp_dir / "rebind.env"
        env_file.write_text("URL=https://example.com?foo=bar\n")

        result = load_rebind_env(env_file)

        assert result == {"URL": "https://example.com?foo=bar"}

    def test_strip_whitespace(self, temp_dir):
        """Whitespace around keys and values is stripped."""
        env_file = temp_dir / "rebind.env"
        env_file.write_text("  KEY  =  value  \n")

        result = load_rebind_env(env_file)

        assert result == {"KEY": "value"}

    def test_missing_file_returns_empty(self, temp_dir):
        """Missing file returns empty dict."""
        nonexistent = temp_dir / "does_not_exist.env"

        result = load_rebind_env(nonexistent)

        assert result == {}


class TestDiscoverFuseLayers:
    """Tests for fuse workspace layer discovery."""

    def test_returns_none_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("SHEPHERD_LAYERS", raising=False)

        assert _discover_fuse_layers() is None

    def test_discovers_layers_in_declared_order(self, monkeypatch, temp_dir):
        layers_root = temp_dir / "layers"
        (layers_root / "parent_0").mkdir(parents=True)
        (layers_root / "base").mkdir()

        monkeypatch.setenv("SHEPHERD_LAYERS", "parent_0:base")
        monkeypatch.setattr("shepherd_runtime.device.container.task_runner.LAYERS_ROOT", layers_root)

        assert _discover_fuse_layers() == [layers_root / "parent_0", layers_root / "base"]

    def test_returns_none_when_all_declared_layers_are_missing(self, monkeypatch, temp_dir):
        layers_root = temp_dir / "layers"
        layers_root.mkdir(parents=True)

        monkeypatch.setenv("SHEPHERD_LAYERS", "parent_0:base")
        monkeypatch.setattr("shepherd_runtime.device.container.task_runner.LAYERS_ROOT", layers_root)

        assert _discover_fuse_layers() is None


# =============================================================================
# load_input / write_output Tests
# =============================================================================


class TestInputOutput:
    """Tests for JSON I/O functions."""

    def test_load_input_valid_json(self, temp_dir):
        """Valid JSON is loaded correctly."""
        input_file = temp_dir / "input.json"
        data = {"prompt": "Hello", "provider_config": {"type": "mock"}}
        input_file.write_text(json.dumps(data))

        result = load_input(input_file)

        assert result == data

    def test_load_input_missing_file(self, temp_dir):
        """Missing file raises FileNotFoundError."""
        nonexistent = temp_dir / "missing.json"

        with pytest.raises(FileNotFoundError):
            load_input(nonexistent)

    def test_load_input_invalid_json(self, temp_dir):
        """Invalid JSON raises JSONDecodeError."""
        bad_file = temp_dir / "bad.json"
        bad_file.write_text("not valid json {")

        with pytest.raises(json.JSONDecodeError):
            load_input(bad_file)

    def test_write_output_creates_file(self, temp_dir):
        """write_output creates the output file."""
        output_file = temp_dir / "output.json"
        data = {"success": True, "result": "done"}

        write_output(data, output_file)

        assert output_file.exists()
        loaded = json.loads(output_file.read_text())
        assert loaded == data

    def test_write_output_creates_parent_dirs(self, temp_dir):
        """write_output creates parent directories if needed."""
        output_file = temp_dir / "nested" / "dir" / "output.json"
        data = {"key": "value"}

        write_output(data, output_file)

        assert output_file.exists()

    def test_write_error_format(self, temp_dir):
        """write_error produces correct error format."""
        output_file = temp_dir / "output.json"

        write_error("Something went wrong", output_file)

        loaded = json.loads(output_file.read_text())
        assert loaded["success"] is False
        assert loaded["error"] == "Something went wrong"
        assert loaded["result"] is None
        assert loaded["collected_effects"] is None


# =============================================================================
# _create_provider Tests
# =============================================================================


class TestCreateProvider:
    """Tests for provider creation."""

    def test_unknown_provider_raises_error(self):
        """Unknown provider type raises ProviderNotAvailableError."""
        config = {"provider_type": "nonexistent", "name": "test"}

        with pytest.raises(ProviderNotAvailableError) as exc_info:
            _create_provider(config)

        assert "Unknown provider type: 'nonexistent'" in str(exc_info.value)

    def test_mock_provider_id(self):
        """Mock provider has correct ID format."""
        provider = _MockProvider({"name": "test"})

        assert provider.provider_id.startswith("provider:mock:")

    @pytest.mark.asyncio
    async def test_mock_provider_execute(self):
        """Mock provider returns placeholder result."""
        provider = _MockProvider({"name": "test"})

        result = await provider.execute_sdk(
            prompt="Hello world",
            binding=None,
            runtime=DefaultProviderRuntime.from_emitter(MagicMock(), task_name="test_task"),
        )

        assert result.success is True
        assert "[Mock execution]" in result.output_text
        assert "Hello world" in result.output_text

    def test_uses_registered_factory(self):
        """Uses registered factory when available."""
        from shepherd_runtime.device.container.provider_registry import (
            _PROVIDER_FACTORIES,
            register_provider_factory,
        )

        # Save original state
        original = _PROVIDER_FACTORIES.copy()

        try:
            mock_factory = MagicMock(return_value=MagicMock(provider_id="test:custom"))
            register_provider_factory("custom", mock_factory)

            config = {"provider_type": "custom", "name": "test"}
            provider = _create_provider(config)

            mock_factory.assert_called_once_with(config)
        finally:
            # Restore original state
            _PROVIDER_FACTORIES.clear()
            _PROVIDER_FACTORIES.update(original)

    def test_claude_unavailable_raises_error(self):
        """Requesting 'claude' when unavailable raises ProviderNotAvailableError."""
        config = {"provider_type": "claude", "name": "test"}

        # Mock the import to simulate shepherd-providers not installed
        with (
            patch.dict("sys.modules", {"shepherd_providers.claude": None}),
            pytest.raises(ProviderNotAvailableError) as exc_info,
        ):
            _create_provider(config)

        error_msg = str(exc_info.value)
        assert "Provider 'claude' requested" in error_msg
        assert "shepherd-providers is not installed" in error_msg
        assert "pip install shepherd-providers" in error_msg

    def test_openai_unavailable_raises_error(self):
        """Requesting 'openai' when unavailable raises ProviderNotAvailableError."""
        config = {"provider_type": "openai", "name": "test"}

        # Mock the import to simulate shepherd-providers not installed
        with (
            patch.dict("sys.modules", {"shepherd_providers.openai": None}),
            pytest.raises(ProviderNotAvailableError) as exc_info,
        ):
            _create_provider(config)

        error_msg = str(exc_info.value)
        assert "Provider 'openai' requested" in error_msg
        assert "shepherd-providers is not installed" in error_msg
        assert "pip install shepherd-providers" in error_msg

    def test_explicit_mock_provider_works(self):
        """Explicit 'mock' provider_type returns mock provider."""
        config = {"provider_type": "mock", "name": "test"}

        with patch("shepherd_runtime.device.container.provider_execution.logger"):
            provider = _create_provider(config)

        assert isinstance(provider, _MockProvider)
        assert provider.config == config

    def test_none_provider_type_uses_mock(self):
        """None provider_type returns mock provider."""
        config = {"name": "test"}  # No provider_type

        with patch("shepherd_runtime.device.container.provider_execution.logger"):
            provider = _create_provider(config)

        assert isinstance(provider, _MockProvider)
        assert provider.config == config

    def test_error_includes_original_exception(self):
        """ProviderNotAvailableError chains the original ImportError."""
        config = {"provider_type": "claude", "name": "test"}

        # Mock the import to simulate shepherd-providers not installed
        with (
            patch.dict("sys.modules", {"shepherd_providers.claude": None}),
            pytest.raises(ProviderNotAvailableError) as exc_info,
        ):
            _create_provider(config)

        # Check that the original ImportError is chained
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, (ImportError, AttributeError))


# =============================================================================
# _build_binding_from_contexts Tests
# =============================================================================


class TestBuildBindingFromContexts:
    """Tests for ProviderBinding construction."""

    def test_empty_contexts_returns_none(self):
        """Empty contexts dict returns None."""
        result = _build_binding_from_contexts({}, None)
        assert result is None

    def test_extracts_path_from_object(self):
        """Extracts path from object with path attribute."""
        mock_state = MagicMock()
        mock_state.path = "/workspace/project"
        mock_state.capabilities = ["read", "write"]

        binding = _build_binding_from_contexts(
            {"workspace": mock_state},
            tools=None,
        )

        assert binding.cwd == "/workspace/project"

    def test_extracts_path_from_dict(self):
        """Extracts path from dict with path key."""
        state_dict = {"path": "/workspace/project", "capabilities": ["read"]}

        binding = _build_binding_from_contexts(
            {"workspace": state_dict},
            tools=None,
        )

        assert binding.cwd == "/workspace/project"

    def test_collects_capabilities_from_object(self):
        """Collects capabilities from object."""
        mock_state = MagicMock()
        mock_state.path = None
        mock_state.capabilities = {"read", "write", "execute"}

        binding = _build_binding_from_contexts(
            {"context": mock_state},
            tools=None,
        )

        assert "read" in binding.capabilities
        assert "write" in binding.capabilities
        assert "execute" in binding.capabilities

    def test_collects_capabilities_from_dict(self):
        """Collects capabilities from dict."""
        state_dict = {"capabilities": ["read", "net"]}

        binding = _build_binding_from_contexts(
            {"context": state_dict},
            tools=None,
        )

        assert "read" in binding.capabilities
        assert "net" in binding.capabilities

    def test_default_capabilities(self):
        """Default capabilities when none provided."""
        mock_state = MagicMock(spec=[])  # No capabilities attr

        binding = _build_binding_from_contexts(
            {"context": mock_state},
            tools=None,
        )

        assert binding.capabilities == frozenset({"read", "write"})

    def test_tools_param_accepted(self):
        """Tools parameter is accepted without error."""
        mock_state = MagicMock(spec=[])

        # Note: ProviderBinding doesn't have an allowed_tools field -
        # it has blocked_tools and a method allowed_tools().
        # The _build_binding_from_contexts passes tools but they're
        # currently ignored by ProviderBinding.
        # This test just verifies the function doesn't error.
        binding = _build_binding_from_contexts(
            {"context": mock_state},
            tools=["Read", "Write", "Bash"],
        )

        assert binding is not None
        assert binding.context_id == "container-execution"

    def test_includes_output_format(self):
        """Includes output_format in binding."""
        mock_state = MagicMock(spec=[])
        output_schema = {"type": "object", "properties": {"result": {"type": "string"}}}

        binding = _build_binding_from_contexts(
            {"context": mock_state},
            tools=None,
            output_format=output_schema,
        )

        assert binding.output_format == output_schema

    def test_multiple_contexts_merge_capabilities(self):
        """Multiple contexts merge their capabilities."""

        # Use objects with explicit attributes
        class WorkspaceState:
            capabilities = {"read", "write"}

        class SessionState:
            capabilities = {"session_read"}

        # Note: Don't include path attributes to avoid the "last path wins" behavior
        binding = _build_binding_from_contexts(
            {"workspace": WorkspaceState(), "session": SessionState()},
            tools=None,
        )

        # Capabilities are merged from all contexts
        assert "read" in binding.capabilities
        assert "write" in binding.capabilities
        assert "session_read" in binding.capabilities

    def test_last_path_wins(self):
        """When multiple contexts have paths, last one wins."""

        # Note: This tests the actual behavior - last path wins,
        # even if it's None. This may be a bug in the implementation.
        class FirstContext:
            path = "/first"
            capabilities = set()

        class SecondContext:
            path = "/second"
            capabilities = set()

        binding = _build_binding_from_contexts(
            {"first": FirstContext(), "second": SecondContext()},
            tools=None,
        )

        # Second context's path wins (last in iteration)
        assert binding.cwd == "/second"

    def test_builds_context_description_from_workspace(self):
        """Builds context_description from workspace path and capabilities."""

        class WorkspaceState:
            path = "/container/workspace"
            capabilities = {"read", "write"}
            pending_patches = []

        binding = _build_binding_from_contexts(
            {"workspace": WorkspaceState()},
            tools=None,
        )

        # Should include workspace path and capabilities in description
        assert binding.context_description is not None
        assert "/container/workspace" in binding.context_description
        assert "read" in binding.context_description
        assert "write" in binding.context_description

    def test_context_description_includes_pending_patches(self):
        """Context description mentions pending patches if present."""

        class WorkspaceState:
            path = "/workspace"
            capabilities = {"read", "write"}
            pending_patches = [{"patch": "data"}, {"patch": "data2"}]

        binding = _build_binding_from_contexts(
            {"workspace": WorkspaceState()},
            tools=None,
        )

        assert "2 pending patches" in binding.context_description

    def test_context_description_from_dict(self):
        """Builds context_description from dict-based state."""
        state_dict = {
            "path": "/my/workspace",
            "capabilities": ["read"],
            "pending_patches": [],
        }

        binding = _build_binding_from_contexts(
            {"workspace": state_dict},
            tools=None,
        )

        assert binding.context_description is not None
        assert "/my/workspace" in binding.context_description
        assert "read" in binding.context_description

    # =========================================================================
    # Session extraction tests (Change 3 from session resumption plan)
    # =========================================================================

    def test_extracts_session_id_from_object(self):
        """Extracts session_id from session context object."""

        class SessionState:
            context_type = "session"
            session_id = "abc123"

        binding = _build_binding_from_contexts(
            {"session": SessionState()},
            tools=None,
        )

        assert binding.session_id == "abc123"
        assert binding.session_isolation == "forked"

    def test_extracts_session_id_from_dict(self):
        """Extracts session_id from session context dict."""
        state_dict = {
            "context_type": "session",
            "session_id": "def456",
        }

        binding = _build_binding_from_contexts(
            {"session": state_dict},
            tools=None,
        )

        assert binding.session_id == "def456"
        assert binding.session_isolation == "forked"

    def test_session_without_id_uses_isolated(self):
        """Session without session_id uses isolated isolation."""
        state_dict = {
            "context_type": "session",
            "session_id": None,
        }

        binding = _build_binding_from_contexts(
            {"session": state_dict},
            tools=None,
        )

        assert binding.session_id is None
        assert binding.session_isolation == "isolated"

    def test_no_session_context_defaults(self):
        """Without session context, session fields are default."""

        class WorkspaceState:
            path = "/workspace"
            capabilities = {"read", "write"}

        binding = _build_binding_from_contexts(
            {"workspace": WorkspaceState()},
            tools=None,
        )

        assert binding.session_id is None
        assert binding.session_isolation == "isolated"

    def test_detects_session_by_binding_name(self):
        """Detects session context by binding name 'session'."""

        class AnyState:
            session_id = "byname123"

        binding = _build_binding_from_contexts(
            {"session": AnyState()},  # detected by name, not context_type
            tools=None,
        )

        assert binding.session_id == "byname123"

    def test_detects_session_by_context_type(self):
        """Detects session context by context_type='session'."""
        state_dict = {
            "context_type": "session",
            "session_id": "bytype456",
        }

        binding = _build_binding_from_contexts(
            {"my_session": state_dict},  # different binding name
            tools=None,
        )

        assert binding.session_id == "bytype456"


# =============================================================================
# _validate_session_resumable Tests
# =============================================================================


class TestValidateSessionResumable:
    """Tests for session transcript validation (Change 9)."""

    def test_none_session_returns_none(self):
        """None session_id returns None."""
        result = _validate_session_resumable(None, "/some/path")
        assert result is None

    def test_existing_transcript_returns_session_id(self, temp_dir):
        """Returns session_id if transcript exists."""
        # Create the transcript file at expected location
        # Note: Must use resolve() because compute_transcript_path does the same
        # (on macOS, /var resolves to /private/var)
        session_id = "abc123"
        resolved_temp_dir = temp_dir.resolve()
        project_folder = str(resolved_temp_dir).replace("/", "-").replace("_", "-")
        transcript_dir = Path.home() / ".claude" / "projects" / project_folder
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_file = transcript_dir / f"{session_id}.jsonl"
        transcript_file.write_text('{"test": "data"}\n')

        try:
            result = _validate_session_resumable(session_id, str(temp_dir))
            assert result == session_id
        finally:
            # Cleanup
            transcript_file.unlink(missing_ok=True)
            transcript_dir.rmdir()

    def test_missing_transcript_returns_none(self, temp_dir):
        """Returns None if transcript is missing."""
        session_id = "nonexistent_session_789"

        result = _validate_session_resumable(session_id, str(temp_dir))

        # Should return None (graceful fallback to new session)
        assert result is None


# =============================================================================
# _serialize_execution_result Tests
# =============================================================================


class TestSerializeExecutionResult:
    """Tests for result serialization."""

    def test_serialize_pydantic_model(self):
        """Serializes Pydantic model using model_dump."""
        from shepherd_core.types import ExecutionResult

        result = ExecutionResult(
            success=True,
            output_text="Hello",
            metadata={"key": "value"},
        )

        serialized = _serialize_execution_result(result)

        assert serialized["success"] is True
        assert serialized["output_text"] == "Hello"
        assert serialized["metadata"] == {"key": "value"}

    def test_serialize_dataclass(self):
        """Serializes dataclass using __dict__."""

        @dataclass
        class SimpleResult:
            success: bool
            message: str
            _private: str = "hidden"

        result = SimpleResult(success=True, message="done", _private="secret")

        serialized = _serialize_execution_result(result)

        assert serialized["success"] is True
        assert serialized["message"] == "done"
        assert "_private" not in serialized

    def test_serialize_nested_pydantic(self):
        """Serializes nested Pydantic models."""
        from shepherd_core.types import ExecutionResult

        # Create a result with nested structure
        result = ExecutionResult(
            success=True,
            output_text="done",
        )

        serialized = _serialize_execution_result(result)

        assert isinstance(serialized, dict)
        assert serialized["success"] is True

    def test_serialize_fallback(self):
        """Falls back to basic dict for unknown types."""

        class MinimalResult:
            """Class without __dict__ or model_dump."""

            __slots__ = ()  # Prevents __dict__
            success = True

        result = MinimalResult()

        serialized = _serialize_execution_result(result)

        # Falls back to checking for success attribute
        assert serialized == {"success": True}

    def test_serialize_list_of_pydantic(self):
        """Serializes lists containing Pydantic models."""

        @dataclass
        class ResultWithList:
            items: list

        inner = MagicMock()
        inner.model_dump = MagicMock(return_value={"id": 1})

        result = ResultWithList(items=[inner, inner])

        serialized = _serialize_execution_result(result)

        assert serialized["items"] == [{"id": 1}, {"id": 1}]


# =============================================================================
# Integration Tests
# =============================================================================


class TestTaskRunnerIntegration:
    """Integration tests for task runner components."""

    def test_full_io_roundtrip(self, temp_dir):
        """Full input/output roundtrip works correctly."""
        input_file = temp_dir / "input.json"
        output_file = temp_dir / "output.json"

        # Write input
        input_data = {
            "prompt": "Test prompt",
            "provider_config": {"provider_type": "mock"},
            "context_states": {},
            "tools": ["Read", "Write"],
        }
        input_file.write_text(json.dumps(input_data))

        # Load input
        loaded = load_input(input_file)
        assert loaded == input_data

        # Process and write output
        output_data = {
            "success": True,
            "result": {"output_text": "Done"},
            "collected_effects": {"effects": []},
            "error": None,
        }
        write_output(output_data, output_file)

        # Verify output
        final = json.loads(output_file.read_text())
        assert final == output_data

    def test_rebind_env_with_binding_construction(self, temp_dir):
        """Rebind env integrates with binding construction."""
        env_file = temp_dir / "rebind.env"
        env_file.write_text("WORKSPACE_PATH=/container/workspace\n")

        rebind_env = load_rebind_env(env_file)

        # Simulate using rebind env with context
        state_dict = {
            "path": rebind_env.get("WORKSPACE_PATH", "/default"),
            "capabilities": ["read", "write"],
        }

        binding = _build_binding_from_contexts(
            {"workspace": state_dict},
            tools=None,
        )

        assert binding.cwd == "/container/workspace"


# =============================================================================
# Threading Safety Tests
# =============================================================================


class TestDeserializerRegistrationThreadSafety:
    """Tests for thread-safe deserializer registration."""

    def test_concurrent_calls_to_ensure_deserializers_registered(self):
        """Concurrent calls should not cause race conditions."""
        from concurrent.futures import ThreadPoolExecutor

        from shepherd_runtime.device.container import task_runner

        # Reset state before test
        task_runner._deserializers_registered = False

        def worker():
            """Worker function that calls the registration function."""
            from shepherd_runtime.device.container.task_runner import (
                _ensure_context_deserializers_registered,
            )

            _ensure_context_deserializers_registered()
            # If we made it here, registration succeeded
            return True

        # Run multiple threads concurrently
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker) for _ in range(20)]
            results = [f.result() for f in futures]

        # All threads should succeed
        assert all(results)

        # Registration should have happened exactly once
        assert task_runner._deserializers_registered is True

    def test_deserializer_registration_is_idempotent(self):
        """Multiple calls should be safe and idempotent."""
        from shepherd_runtime.device.container import task_runner
        from shepherd_runtime.device.container.task_runner import (
            _ensure_context_deserializers_registered,
        )

        # Reset state
        task_runner._deserializers_registered = False

        # Call multiple times
        _ensure_context_deserializers_registered()
        first_state = task_runner._deserializers_registered

        _ensure_context_deserializers_registered()
        second_state = task_runner._deserializers_registered

        _ensure_context_deserializers_registered()
        third_state = task_runner._deserializers_registered

        # All should be True
        assert first_state is True
        assert second_state is True
        assert third_state is True

    def test_fast_path_avoids_lock_contention(self):
        """Fast path should avoid lock when already registered."""
        import time

        from shepherd_runtime.device.container import task_runner
        from shepherd_runtime.device.container.task_runner import (
            _ensure_context_deserializers_registered,
        )

        # Ensure it's registered
        task_runner._deserializers_registered = True

        # Time many fast-path calls
        start = time.perf_counter()
        for _ in range(1000):
            _ensure_context_deserializers_registered()
        duration = time.perf_counter() - start

        # Should be very fast (under 10ms for 1000 calls)
        # This is a sanity check that we're not acquiring locks unnecessarily
        assert duration < 0.01
