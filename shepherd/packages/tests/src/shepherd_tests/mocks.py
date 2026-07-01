"""Mock utilities for testing Shepherd components.

Provides factory functions and mock implementations for testing.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from shepherd_core import (
    ExecutionContext,
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ReversibilityLevel,
)
from shepherd_core.provider import ProviderRuntime


def create_mock_binding(
    context_id: str = "mock-context",
    capabilities: frozenset[str] | None = None,
    blocked_tools: frozenset[str] | None = None,
    trust_level: str = "trusted",
    **kwargs: Any,
) -> ProviderBinding:
    """Create a mock ProviderBinding for testing.

    Args:
        context_id: The context ID for the binding.
        capabilities: Required capabilities (default: empty).
        blocked_tools: Tools to block (default: empty).
        trust_level: Trust level (default: "trusted").
        **kwargs: Additional binding attributes.

    Returns:
        A ProviderBinding instance.

    Example:
        binding = create_mock_binding(
            context_id="test",
            capabilities=frozenset({"read", "write"}),
        )
    """
    return ProviderBinding(
        context_id=context_id,
        capabilities=capabilities or frozenset(),
        blocked_tools=blocked_tools or frozenset(),
        trust_level=trust_level,
        **kwargs,
    )


def create_mock_context(
    context_id: str = "mock-context",
    reversibility: ReversibilityLevel = ReversibilityLevel.AUTO,
    binding: ProviderBinding | None = None,
) -> ExecutionContext:
    """Create a mock ExecutionContext for testing.

    Returns a MagicMock that implements the ExecutionContext protocol
    with configurable behavior.

    Args:
        context_id: The context ID.
        reversibility: The reversibility level.
        binding: Optional binding to return from configure().

    Returns:
        A mock ExecutionContext.

    Example:
        context = create_mock_context(
            context_id="test-workspace",
            reversibility=ReversibilityLevel.AUTO,
        )
    """
    mock = MagicMock(spec=ExecutionContext)
    mock.context_id = context_id
    mock.reversibility = reversibility

    # Configure returns binding
    if binding is None:
        binding = create_mock_binding(context_id=context_id)
    mock.configure.return_value = binding

    # Prepare returns self
    mock.prepare.return_value = mock

    # v2 API: extract_effects returns empty list
    mock.extract_effects.return_value = []

    # v2 API: apply_effect returns self
    mock.apply_effect.return_value = mock

    # Cleanup does nothing
    mock.cleanup.return_value = None

    return mock


def create_mock_result(
    output_text: str = "[mock output]",
    structured_output: dict[str, Any] | None = None,
    success: bool = True,
) -> ExecutionResult:
    """Create a mock ExecutionResult for testing.

    Args:
        output_text: The text output (default: "[mock output]").
        structured_output: The structured output dict (default: empty dict).
        success: Whether the execution was successful (default: True).

    Returns:
        An ExecutionResult instance.

    Example:
        result = create_mock_result(
            output_text="Test completed",
            structured_output={"summary": "Test result"},
        )
    """
    return ExecutionResult(
        success=success,
        output_text=output_text,
        structured_output=structured_output or {},
    )


# =============================================================================
# Mock Provider Implementations
# =============================================================================


@dataclass
class FileModifyingMockProvider:
    r"""A mock provider that writes files during execution.

    This provider writes a test file to the working directory specified
    in the binding's cwd. This simulates an agent making file changes,
    useful for testing sandbox behavior and effect capture.

    Attributes:
        name: Provider name for identification.
        file_to_create: Filename to create during execution.
        file_content: Content to write to the file.
        calls: List of call records for debugging.
        cwd_used: The cwd that was used during execution.

    Example:
        provider = FileModifyingMockProvider(
            file_to_create="test_output.txt",
            file_content="Created by test\\n",
        )

        with Scope(root=True) as scope:
            scope.register_provider("default", provider, default=True)
            workspace = WorkspaceRef.from_path(repo_path)
            scope.bind("workspace", workspace)

            async with ExecutionLifecycle(scope, provider) as lifecycle:
                await lifecycle.execute("Create a file")

            # File was created in sandbox, not original workspace
            assert provider.cwd_used != repo_path
    """

    name: str = "file-modifier"
    mock: bool = True
    file_to_create: str = "agent_output.txt"
    file_content: str = "Created by agent\n"

    # Track for debugging
    calls: list[dict[str, Any]] = field(default_factory=list)
    cwd_used: Path | None = field(default=None, repr=False)

    @property
    def provider_id(self) -> str:
        return f"provider:mock:{self.name}"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type="mock",
            supports_streaming=False,
            supports_tools=True,
            supports_structured_output=False,
            supports_session=False,
            supports_fork_session=False,
            supports_images=False,
        )

    @property
    def formatter(self) -> Any:
        return None

    def validate_binding(self, binding: ProviderBinding) -> None:
        pass

    async def execute_sdk(
        self,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
    ) -> ExecutionResult:
        """Execute by writing a file to the cwd."""
        self.calls.append(
            {
                "prompt": prompt,
                "binding": binding,
                "task_name": runtime.task_name,
            }
        )

        # Get cwd from binding
        cwd = Path(binding.cwd) if binding and binding.cwd else Path.cwd()
        self.cwd_used = cwd

        # Write a file in the working directory
        output_file = cwd / self.file_to_create
        output_file.write_text(self.file_content)

        return ExecutionResult(
            success=True,
            output_text=f"Created {self.file_to_create}",
        )


# =============================================================================
# Mock Container Device
# =============================================================================


@dataclass
class MockSandbox:
    """Mock sandbox handle for testing container device logic.

    This is a lightweight sandbox that doesn't require Podman or OverlayFS.
    It creates temporary directories to simulate the overlay structure.

    Attributes:
        sandbox_id: Unique identifier for this sandbox.
        device_name: Always "mock-container".
        task_dir: Directory for task I/O files.
        overlays: Mock overlay configurations by binding name.
        context_states: Stored context states.
        files_created: Files that were "created" during execution (for testing).
        files_modified: Files that were "modified" during execution (for testing).
        parent_sandbox: Parent sandbox for overlay layering (if any).
    """

    sandbox_id: str
    device_name: str = "mock-container"
    task_dir: Path | None = None
    overlays: dict[str, "MockOverlay"] = field(default_factory=dict)
    context_states: dict[str, Any] = field(default_factory=dict)
    files_created: dict[str, str] = field(default_factory=dict)
    files_modified: dict[str, str] = field(default_factory=dict)
    parent_sandbox: "MockSandbox | None" = None
    _cleanup_fn: Any = None
    _metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, sandbox_id: str | None = None) -> "MockSandbox":
        """Create a new mock sandbox."""
        import uuid

        return cls(sandbox_id=sandbox_id or str(uuid.uuid4()))

    def get_workspace_layers(self) -> list[Path]:
        """Get workspace overlay layers for stacking."""
        if "workspace" in self.overlays:
            return [self.overlays["workspace"].upper]
        return []

    def simulate_file_create(self, path: str, content: str) -> None:
        """Simulate a file being created during execution."""
        self.files_created[path] = content

    def simulate_file_modify(self, path: str, content: str) -> None:
        """Simulate a file being modified during execution."""
        self.files_modified[path] = content


@dataclass
class MockOverlay:
    """Mock overlay mount for testing.

    Attributes:
        task_id: Task that owns this overlay.
        context_name: Binding name (e.g., "workspace").
        lower: Path to lower layer (original content).
        upper: Path to upper layer (changes).
        work: Path to work directory.
        merged: Path to merged view.
        original_host_path: Original path on host.
        is_vm_path: Whether paths are in a VM (always False for mock).
    """

    task_id: str
    context_name: str
    lower: Path
    upper: Path
    work: Path
    merged: Path
    original_host_path: Path | None = None
    is_vm_path: bool = False


@dataclass
class MockContainerDevice:
    """Mock container device for testing without Podman.

    This device simulates container execution for testing:
    - Creates temporary directories instead of OverlayFS mounts
    - Allows configuring expected outputs and effects
    - Tracks sandbox lifecycle calls for assertions
    - No external dependencies (no Podman, no containers)

    Use this device when:
    - Testing container orchestration logic
    - Testing effect extraction patterns
    - Running in CI without Podman installed
    - Unit testing device-dependent code paths

    Attributes:
        name: Device identifier ("mock-container").
        capabilities: Device capabilities (container isolation, overlay capture).
        default_output: Default ExecutionResult to return from execute().
        default_effects: Default effects to return from extract_effects().
        sandboxes_created: List of sandboxes created (for test assertions).
        sandboxes_cleaned: List of sandboxes cleaned up (for test assertions).
        execute_calls: List of (sandbox, spec) tuples from execute() calls.
        _temp_dirs: Temporary directories to clean up.

    Example:
        device = MockContainerDevice()

        # Configure expected output
        device.default_output = ExecutionResult(
            success=True,
            output_text="Task completed",
        )

        # Use in tests
        sandbox = await device.create_sandbox(scope, config)
        result = await device.execute(sandbox, spec)

        # Simulate file changes
        sandbox.simulate_file_create("new_file.py", "print('hello')")

        # Extract effects
        bundle = await device.extract_effects(sandbox, result)

        # Assertions
        assert len(device.sandboxes_created) == 1
        assert device.execute_calls[0][1].prompt == "Fix the bug"
    """

    name: str = "mock-container"
    capabilities: Any = field(default_factory=lambda: _make_mock_capabilities())

    # Configurable outputs
    default_output: Any = field(default_factory=lambda: _make_default_result())
    default_effects: list[Any] = field(default_factory=list)

    # Tracking for test assertions
    sandboxes_created: list[MockSandbox] = field(default_factory=list)
    sandboxes_cleaned: list[MockSandbox] = field(default_factory=list)
    execute_calls: list[tuple[MockSandbox, Any]] = field(default_factory=list)

    # Internal state
    _temp_dirs: list[Path] = field(default_factory=list, repr=False)

    def __del__(self) -> None:
        """Clean up temporary directories."""
        import shutil

        for temp_dir in self._temp_dirs:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    async def create_sandbox(
        self,
        scope: Any,
        config: Any,
    ) -> MockSandbox:
        """Create a mock sandbox.

        Creates temporary directories to simulate overlay structure.
        No actual OverlayFS mounts are created.

        Args:
            scope: Parent scope (stored but not used).
            config: Sandbox configuration with context states.

        Returns:
            MockSandbox handle.
        """
        import tempfile
        import uuid

        sandbox_id = str(uuid.uuid4())
        sandbox = MockSandbox.create(sandbox_id)

        # Create temp directory for task I/O
        temp_root = Path(tempfile.mkdtemp(prefix=f"mock-sandbox-{sandbox_id[:8]}-"))
        self._temp_dirs.append(temp_root)

        task_dir = temp_root / "task"
        task_dir.mkdir(parents=True)
        sandbox.task_dir = task_dir

        # Store context states
        sandbox.context_states = dict(config.context_states)

        # Create mock overlays for each context
        for binding_name, state in config.context_states.items():
            overlay_dir = temp_root / "overlays" / binding_name
            lower = overlay_dir / "lower"
            upper = overlay_dir / "upper"
            work = overlay_dir / "work"
            merged = overlay_dir / "merged"

            for d in [lower, upper, work, merged]:
                d.mkdir(parents=True, exist_ok=True)

            # Get base path from state if available
            base_path = None
            if hasattr(state, "path"):
                base_path = Path(state.path)
            elif isinstance(state, dict) and "path" in state:
                base_path = Path(state["path"])

            overlay = MockOverlay(
                task_id=sandbox_id,
                context_name=binding_name,
                lower=lower,
                upper=upper,
                work=work,
                merged=merged,
                original_host_path=base_path,
            )
            sandbox.overlays[binding_name] = overlay

        # Find parent sandbox from scope if available
        if hasattr(scope, "get_latest_sandbox_for_context"):
            parent = scope.get_latest_sandbox_for_context("workspace")
            sandbox.parent_sandbox = parent

        # Register with scope if method exists
        if hasattr(scope, "register_sandbox"):
            scope.register_sandbox(sandbox)

        self.sandboxes_created.append(sandbox)
        return sandbox

    async def execute(
        self,
        sandbox: MockSandbox,
        spec: Any,
    ) -> Any:
        """Execute within mock sandbox.

        Returns the configured default_output without running any container.

        Args:
            sandbox: Mock sandbox handle.
            spec: Execution specification.

        Returns:
            Configured ExecutionResult (default_output).
        """
        self.execute_calls.append((sandbox, spec))
        return self.default_output

    async def extract_effects(
        self,
        sandbox: MockSandbox,
        execution_result: Any,
    ) -> Any:
        """Extract effects from mock sandbox.

        Generates effects based on:
        1. Files in sandbox.files_created → FileCreated effects
        2. Files in sandbox.files_modified → FilePatch effects
        3. Configured default_effects

        Args:
            sandbox: Mock sandbox after execution.
            execution_result: Result from execute().

        Returns:
            EffectBundle with generated effects.
        """
        from shepherd_core.effects import FileCreate, FilePatch
        from shepherd_core.foundation.protocols.device import EffectBundle

        context_effects: dict[str, list[Any]] = {}

        # Generate effects from simulated file operations
        workspace_effects: list[Any] = []

        for path, content in sandbox.files_created.items():
            effect = FileCreate(
                path=path,
                content=content,
            )
            workspace_effects.append(effect)

        for path, content in sandbox.files_modified.items():
            effect = FilePatch(
                path=path,
                patch=f"@@ mock patch @@\n+{content}",
            )
            workspace_effects.append(effect)

        if workspace_effects:
            context_effects["workspace"] = workspace_effects

        # Add configured default effects
        lifecycle_effects = list(self.default_effects)

        return EffectBundle(
            context_effects=context_effects,
            lifecycle_effects=lifecycle_effects,
            execution_metadata={
                "sandbox_id": sandbox.sandbox_id,
                "mock": True,
            },
        )

    async def cleanup(
        self,
        sandbox: MockSandbox,
        *,
        force: bool = False,
        preserve: bool = False,
        preserve_overlays: bool = False,
    ) -> None:
        """Clean up mock sandbox.

        Removes temporary directories unless preserve=True.

        Args:
            sandbox: Mock sandbox to clean up.
            force: If True, clean up even if preserve is set.
            preserve: If True, keep temporary directories.
            preserve_overlays: If True, keep overlay directories.
        """
        import shutil

        self.sandboxes_cleaned.append(sandbox)

        if preserve and not force:
            return

        if sandbox.task_dir and sandbox.task_dir.exists():
            parent = sandbox.task_dir.parent
            if parent.exists() and parent.name.startswith("mock-sandbox-"):
                shutil.rmtree(parent, ignore_errors=True)
                if parent in self._temp_dirs:
                    self._temp_dirs.remove(parent)

    def reset(self) -> None:
        """Reset tracking state for reuse in multiple tests.

        Clears:
        - sandboxes_created
        - sandboxes_cleaned
        - execute_calls
        """
        self.sandboxes_created.clear()
        self.sandboxes_cleaned.clear()
        self.execute_calls.clear()


def _make_mock_capabilities() -> Any:
    """Create mock device capabilities."""
    from shepherd_core.foundation.protocols.device import DeviceCapabilities

    return DeviceCapabilities(
        isolation_level="container",
        effect_capture="overlay",
        supports_checkpoint=False,
        supports_restore=False,
        supports_dmtcp=False,
        supports_parallel=True,
    )


def _make_default_result() -> Any:
    """Create default execution result."""
    from shepherd_core.foundation.protocols.device import ExecutionResult

    return ExecutionResult(
        success=True,
        output_text="[mock container output]",
    )
