"""Tests for capability system (v2 Architecture).

These tests validate the capability system:
1. WorkspaceRef capability factory methods (readonly, writable)
2. Capability modifier methods (with_bash, without_bash, etc.)
3. Capability properties (can_read, can_write, can_bash)
4. CapabilityError exception
5. ProviderBinding capability composition
6. Capability preservation across operations
"""

from shepherd_contexts import WorkspaceRef
from shepherd_core.errors import CapabilityError
from shepherd_core.types import (
    CAPABILITY_TOOL_MAP,
    ExecutionResult,
    ProviderBinding,
    capability_for_tool,
    tools_for_capabilities,
)

# =============================================================================
# WorkspaceRef Factory Methods Tests
# =============================================================================


class TestWorkspaceRefFactoryMethods:
    """Test WorkspaceRef.readonly() and WorkspaceRef.writable()."""

    def test_readonly_has_only_read_capability(self, git_workspace):
        """WorkspaceRef.readonly() should have only read capability."""
        workspace = WorkspaceRef.readonly(git_workspace)
        assert workspace.capabilities == frozenset({"read"})

    def test_readonly_can_read(self, git_workspace):
        """Read-only workspace should be able to read."""
        workspace = WorkspaceRef.readonly(git_workspace)
        assert workspace.can_read is True

    def test_readonly_cannot_write(self, git_workspace):
        """Read-only workspace should not be able to write."""
        workspace = WorkspaceRef.readonly(git_workspace)
        assert workspace.can_write is False

    def test_readonly_cannot_bash(self, git_workspace):
        """Read-only workspace should not be able to bash."""
        workspace = WorkspaceRef.readonly(git_workspace)
        assert workspace.can_bash is False

    def test_writable_has_read_and_write(self, git_workspace):
        """WorkspaceRef.writable() should have read and write capabilities."""
        workspace = WorkspaceRef.writable(git_workspace)
        assert workspace.capabilities == frozenset({"read", "write"})

    def test_writable_can_read(self, git_workspace):
        """Writable workspace should be able to read."""
        workspace = WorkspaceRef.writable(git_workspace)
        assert workspace.can_read is True

    def test_writable_can_write(self, git_workspace):
        """Writable workspace should be able to write."""
        workspace = WorkspaceRef.writable(git_workspace)
        assert workspace.can_write is True

    def test_writable_cannot_bash_by_default(self, git_workspace):
        """Writable workspace should not be able to bash by default."""
        workspace = WorkspaceRef.writable(git_workspace)
        assert workspace.can_bash is False

    def test_from_path_same_as_writable(self, git_workspace):
        """from_path() should have same capabilities as writable()."""
        ws_from_path = WorkspaceRef.from_path(git_workspace)
        ws_writable = WorkspaceRef.writable(git_workspace)
        assert ws_from_path.capabilities == ws_writable.capabilities


# =============================================================================
# Capability Modifier Methods Tests
# =============================================================================


class TestCapabilityModifiers:
    """Test with_bash, without_bash, with_capabilities, without_capabilities."""

    def test_with_bash_adds_bash(self, git_workspace):
        """with_bash() should add bash capability."""
        workspace = WorkspaceRef.writable(git_workspace)
        with_bash = workspace.with_bash()
        assert with_bash.can_bash is True
        assert with_bash.capabilities == frozenset({"read", "write", "bash"})

    def test_with_bash_preserves_other_capabilities(self, git_workspace):
        """with_bash() should not change other capabilities."""
        workspace = WorkspaceRef.writable(git_workspace)
        with_bash = workspace.with_bash()
        assert with_bash.can_read is True
        assert with_bash.can_write is True

    def test_with_bash_returns_new_instance(self, git_workspace):
        """with_bash() should return a new instance."""
        workspace = WorkspaceRef.writable(git_workspace)
        with_bash = workspace.with_bash()
        assert with_bash is not workspace

    def test_without_bash_removes_bash(self, git_workspace):
        """without_bash() should remove bash capability."""
        workspace = WorkspaceRef.writable(git_workspace).with_bash()
        without_bash = workspace.without_bash()
        assert without_bash.can_bash is False

    def test_without_bash_on_workspace_without_bash(self, git_workspace):
        """without_bash() should be idempotent."""
        workspace = WorkspaceRef.writable(git_workspace)
        without_bash = workspace.without_bash()
        assert without_bash.can_bash is False
        assert without_bash.capabilities == workspace.capabilities

    def test_with_capabilities_adds_multiple(self, git_workspace):
        """with_capabilities() should add multiple capabilities."""
        workspace = WorkspaceRef.readonly(git_workspace)
        upgraded = workspace.with_capabilities("write", "bash")
        assert upgraded.capabilities == frozenset({"read", "write", "bash"})

    def test_without_capabilities_removes_multiple(self, git_workspace):
        """without_capabilities() should remove multiple capabilities."""
        workspace = WorkspaceRef.writable(git_workspace).with_bash()
        downgraded = workspace.without_capabilities("write", "bash")
        assert downgraded.capabilities == frozenset({"read"})

    def test_chaining_modifiers(self, git_workspace):
        """Capability modifiers should be chainable."""
        workspace = WorkspaceRef.readonly(git_workspace).with_capabilities("write").with_bash()
        assert workspace.capabilities == frozenset({"read", "write", "bash"})

    def test_modifier_preserves_context_id(self, git_workspace):
        """Capability modifiers should preserve context_id."""
        workspace = WorkspaceRef.writable(git_workspace)
        original_id = workspace.context_id
        with_bash = workspace.with_bash()
        assert with_bash.context_id == original_id


# =============================================================================
# Capability Preservation Tests
# =============================================================================


class TestCapabilityPreservation:
    """Test that capabilities are preserved across operations."""

    def test_context_prepare_preserves_capabilities(self, git_workspace):
        """prepare() should preserve capabilities."""
        workspace = WorkspaceRef.readonly(git_workspace)
        prepared = workspace.prepare()
        assert prepared.capabilities == frozenset({"read"})

    def test_context_capture_preserves_capabilities(self, git_workspace):
        """extract_effects() and apply_effect() should preserve capabilities."""
        workspace = WorkspaceRef.writable(git_workspace).with_bash()
        prepared = workspace.prepare()

        # Create a change
        (git_workspace / "new_file.py").write_text("# New file")

        result = ExecutionResult(success=True, output_text="done")
        effects = prepared.extract_effects(None, result)

        # Apply effects to derive new state
        new_workspace = prepared
        for effect in effects:
            new_workspace = new_workspace.apply_effect(effect)

        assert new_workspace.capabilities == frozenset({"read", "write", "bash"})


# =============================================================================
# CapabilityError Tests
# =============================================================================


class TestCapabilityError:
    """Test CapabilityError exception."""

    def test_error_attributes(self):
        """CapabilityError should have correct attributes."""
        error = CapabilityError(
            tool_name="Write",
            required_capability="write",
            context_id="workspace:/repo:abc123",
            available_capabilities=frozenset({"read"}),
        )
        assert error.tool_name == "Write"
        assert error.required_capability == "write"
        assert error.context_id == "workspace:/repo:abc123"
        assert error.available_capabilities == frozenset({"read"})

    def test_error_message_format(self):
        """CapabilityError should have informative message."""
        error = CapabilityError(
            tool_name="Bash",
            required_capability="bash",
            context_id="workspace:/repo:abc123",
            available_capabilities=frozenset({"read", "write"}),
        )
        message = str(error)
        assert "Bash" in message or "bash" in message

    def test_error_is_exception(self):
        """CapabilityError should be an Exception."""
        error = CapabilityError(
            tool_name="Write",
            required_capability="write",
            context_id="ctx",
        )
        assert isinstance(error, Exception)


# =============================================================================
# Capability Mapping Tests
# =============================================================================


class TestCapabilityMapping:
    """Test capability-to-tool mappings."""

    def test_capability_for_tool_write(self):
        """Write tool should require 'write' capability."""
        assert capability_for_tool("Write") == "write"

    def test_capability_for_tool_edit(self):
        """Edit tool should require 'write' capability."""
        assert capability_for_tool("Edit") == "write"

    def test_capability_for_tool_bash(self):
        """Bash tool should require 'bash' capability."""
        assert capability_for_tool("Bash") == "bash"

    def test_capability_for_tool_read(self):
        """Read tool should require 'read' capability or None."""
        result = capability_for_tool("Read")
        assert result == "read" or result is None

    def test_capability_for_unknown_tool(self):
        """Unknown tools should return None."""
        assert capability_for_tool("UnknownTool") is None

    def test_tools_for_capabilities(self):
        """tools_for_capabilities should return correct tool sets."""
        tools = tools_for_capabilities(frozenset({"read", "write"}))
        assert "Read" in tools or len(tools) > 0

    def test_capability_tool_map_structure(self):
        """CAPABILITY_TOOL_MAP should have expected structure."""
        assert "read" in CAPABILITY_TOOL_MAP or "write" in CAPABILITY_TOOL_MAP
        assert isinstance(CAPABILITY_TOOL_MAP, dict)


# =============================================================================
# ProviderBinding Capability Composition Tests
# =============================================================================


class TestProviderBindingCapabilities:
    """Test ProviderBinding capability composition."""

    def test_binding_with_capabilities(self):
        """ProviderBinding should accept capabilities."""
        binding = ProviderBinding(
            context_id="test:ctx",
            capabilities=frozenset({"read", "write"}),
        )
        assert binding.capabilities == frozenset({"read", "write"})

    def test_binding_compose_capabilities_intersection(self):
        """Composed bindings should intersect capabilities."""
        binding1 = ProviderBinding(
            context_id="ctx1",
            capabilities=frozenset({"read", "write", "bash"}),
        )
        binding2 = ProviderBinding(
            context_id="ctx2",
            capabilities=frozenset({"read", "write"}),
        )

        composed = ProviderBinding.compose(binding1, binding2)
        # Intersection: most restrictive wins
        assert composed.capabilities == frozenset({"read", "write"})

    def test_binding_compose_blocked_tools_union(self):
        """Composed bindings should union blocked tools."""
        binding1 = ProviderBinding(
            context_id="ctx1",
            blocked_tools=frozenset({"Bash"}),
        )
        binding2 = ProviderBinding(
            context_id="ctx2",
            blocked_tools=frozenset({"Write"}),
        )

        composed = ProviderBinding.compose(binding1, binding2)
        # Union: all blocks apply
        assert composed.blocked_tools == frozenset({"Bash", "Write"})


# =============================================================================
# Real World Scenarios
# =============================================================================


class TestRealWorldScenarios:
    """Test real-world usage patterns for capabilities."""

    def test_readonly_analysis_task(self, git_workspace):
        """Simulate a read-only code analysis task."""
        workspace = WorkspaceRef.readonly(git_workspace)

        # Should be able to read
        assert workspace.can_read

        # Should NOT be able to write
        assert not workspace.can_write

        # Configure returns binding with read-only capabilities
        binding = workspace.configure(frozenset({"read"}))
        assert "read" in binding.capabilities or binding.capabilities == frozenset()

    def test_write_only_no_bash_task(self, git_workspace):
        """Simulate a task that can write but not run commands."""
        workspace = WorkspaceRef.writable(git_workspace)  # No bash

        # Should be able to write
        assert workspace.can_write

        # Should NOT be able to bash
        assert not workspace.can_bash

    def test_full_capability_task(self, git_workspace):
        """Simulate a task with full capabilities."""
        workspace = WorkspaceRef.writable(git_workspace).with_bash()

        # All capabilities should be available
        assert workspace.can_read
        assert workspace.can_write
        assert workspace.can_bash

    def test_capability_upgrade_workflow(self, git_workspace):
        """Simulate upgrading capabilities mid-workflow."""
        # Start with readonly for analysis
        workspace = WorkspaceRef.readonly(git_workspace)
        assert not workspace.can_write

        # Upgrade for modification phase
        workspace = workspace.with_capabilities("write")
        assert workspace.can_write
        assert not workspace.can_bash

        # Upgrade for execution phase
        workspace = workspace.with_bash()
        assert workspace.can_bash
