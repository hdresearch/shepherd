"""Tests for SimpleWorkspace sandbox integration.

Covers:
- SimpleWorkspace.requires_sandbox() returns True
- SimpleWorkspace sandbox factory is registered on import
- Factory creates CopySandbox
"""

from pathlib import Path

# Importing the SimpleWorkspace package triggers its sandbox-factory
# registration as an import side effect (see
# shepherd_contexts/simple_workspace/__init__.py). Import it at module scope so
# the factory is present on whichever worker runs these tests: pytest-xdist with
# `--dist loadscope` can place each test class on a different worker, and the
# assertions below rely on that registration having happened in-process.
import shepherd_contexts.simple_workspace  # noqa: F401
from shepherd_tests.runtime import create_sandbox_for_context, sandbox_factories

# =============================================================================
# Tests: SimpleWorkspace.requires_sandbox()
# =============================================================================


class TestSimpleWorkspaceRequiresSandbox:
    """Tests for SimpleWorkspace.requires_sandbox() method."""

    def test_requires_sandbox_returns_true(self) -> None:
        """SimpleWorkspace.requires_sandbox() should return True."""
        from shepherd_contexts.simple_workspace import SimpleWorkspace

        assert SimpleWorkspace.requires_sandbox() is True


# =============================================================================
# Tests: Factory Registration
# =============================================================================


class TestSimpleWorkspaceFactoryRegistration:
    """Tests for SimpleWorkspace sandbox factory registration."""

    def test_factory_registered_on_import(self) -> None:
        """SimpleWorkspace factory should be registered when module is imported."""

        assert "SimpleWorkspace" in sandbox_factories

    def test_factory_returns_copy_sandbox(self, tmp_path: Path) -> None:
        """Factory should return a CopySandbox instance."""
        from shepherd_contexts.simple_workspace import CopySandbox, SimpleWorkspace

        # Create a test directory with some content
        (tmp_path / "test.txt").write_text("test content")

        workspace = SimpleWorkspace.from_path(tmp_path)
        factory = sandbox_factories.get("SimpleWorkspace")

        assert factory is not None

        sandbox = factory(workspace)
        assert isinstance(sandbox, CopySandbox)


# =============================================================================
# Tests: Integration with _create_sandbox_for_context()
# =============================================================================


class TestSimpleWorkspaceCreateSandbox:
    """Tests for _create_sandbox_for_context() with SimpleWorkspace."""

    def test_create_sandbox_returns_copy_sandbox(self, tmp_path: Path) -> None:
        """_create_sandbox_for_context() should return CopySandbox for SimpleWorkspace."""
        from shepherd_contexts.simple_workspace import CopySandbox, SimpleWorkspace

        (tmp_path / "test.txt").write_text("test content")

        workspace = SimpleWorkspace.from_path(tmp_path)
        sandbox = create_sandbox_for_context(workspace)

        assert sandbox is not None
        assert isinstance(sandbox, CopySandbox)

    def test_create_sandbox_no_warning_for_simple_workspace(self, tmp_path: Path) -> None:
        """No warning should be emitted since factory is registered."""
        import warnings

        from shepherd_contexts.simple_workspace import SimpleWorkspace

        (tmp_path / "test.txt").write_text("test content")

        workspace = SimpleWorkspace.from_path(tmp_path)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sandbox = create_sandbox_for_context(workspace)

            assert sandbox is not None
            # Filter for sandbox-related warnings only
            sandbox_warnings = [x for x in w if "requires_sandbox" in str(x.message)]
            assert len(sandbox_warnings) == 0
