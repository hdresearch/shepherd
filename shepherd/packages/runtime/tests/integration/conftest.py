"""Shared fixtures for integration tests.

Provides fixtures for container/overlay testing:
- Podman availability detection
- PodmanSandboxManager instances
- Temporary workspace and overlay directories
- Unique task IDs for isolation
- Mock scope for ContainerDevice tests
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from shepherd_runtime.device.container.podman import PodmanSandboxManager

if TYPE_CHECKING:
    from collections.abc import Generator

# =============================================================================
# Podman Availability Helpers
# =============================================================================


def is_podman_available() -> bool:
    """Check if Podman is installed and running.

    Returns:
        True if Podman is available and responding, False otherwise.
    """
    try:
        result = subprocess.run(
            ["podman", "version"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def skip_if_podman_unavailable() -> pytest.MarkDecorator:
    """Return a skip marker if Podman is not available.

    Returns:
        pytest mark configured to skip when Podman is unavailable at test time.
    """
    return pytest.mark.usefixtures("_requires_podman")


# Convenience marker for tests requiring Podman
requires_podman = skip_if_podman_unavailable()


@pytest.fixture
def _requires_podman() -> None:
    if not is_podman_available():
        pytest.skip("Podman not available")


def fuse_workspace_unavailable_reason(image: str = "shepherd-sandbox") -> str | None:
    """Return why FUSE workspace e2e tests cannot run, or None when ready."""
    manager = PodmanSandboxManager(image=image)
    if not manager.is_podman_available():
        return "Podman not available"
    if not manager.is_image_available(image):
        return f"Container image {image!r} not available"

    probe = (
        "from pathlib import Path\n"
        "import shutil\n"
        "import sys\n"
        "missing = []\n"
        "if not Path('/dev/fuse').exists():\n"
        "    missing.append('/dev/fuse')\n"
        "for binary in ('fuse-overlayfs', 'fusermount3'):\n"
        "    if shutil.which(binary) is None:\n"
        "        missing.append(binary)\n"
        "if missing:\n"
        "    print('missing: ' + ', '.join(missing), file=sys.stderr)\n"
        "    sys.exit(2)\n"
    )
    try:
        result = subprocess.run(
            [
                "podman",
                "run",
                "--rm",
                "--device",
                "/dev/fuse",
                "--cap-add",
                "SYS_ADMIN",
                image,
                "python",
                "-c",
                probe,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return f"FUSE workspace probe failed: {exc}"
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        return f"FUSE workspace probe failed for {image!r}: {details or f'exit {result.returncode}'}"
    return None


requires_fuse_workspace = pytest.mark.usefixtures("_requires_fuse_workspace")


@pytest.fixture
def _requires_fuse_workspace() -> None:
    reason = fuse_workspace_unavailable_reason()
    if reason is not None:
        pytest.skip(reason)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def manager(temp_overlays: Path) -> PodmanSandboxManager:
    """Create a PodmanSandboxManager instance with temp overlays root.

    Args:
        temp_overlays: Temporary directory for overlay storage.

    Returns:
        Configured PodmanSandboxManager.
    """
    return PodmanSandboxManager(overlays_root=temp_overlays)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory.

    Uses pytest's tmp_path fixture for automatic cleanup.

    Returns:
        Path to the temporary workspace directory.
    """
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return workspace_dir


@pytest.fixture
def unique_task_id() -> str:
    """Generate a unique task ID for test isolation.

    Returns:
        UUID string for identifying test tasks.
    """
    return str(uuid.uuid4())


_OVERLAY_TMPFS = Path("/tmp/shepherd-test-overlays")


@pytest.fixture
def temp_overlays() -> Generator[Path, None, None]:
    """Create a temporary overlays root directory.

    Uses a tmpfs mount when available (set up by post-start.sh) so that
    OverlayFS mounts work even when the root filesystem is itself an overlay.

    The directory is cleaned up after the test completes.

    Yields:
        Path to the temporary overlays directory.
    """
    base = _OVERLAY_TMPFS if _OVERLAY_TMPFS.is_mount() else Path(tempfile.gettempdir())
    overlays_dir = tempfile.mkdtemp(prefix="shepherd-test-overlays-", dir=base)
    yield Path(overlays_dir)
    shutil.rmtree(overlays_dir, ignore_errors=True)


@pytest.fixture
def mock_scope() -> MagicMock:
    """Create a mock scope for ContainerDevice tests.

    Returns:
        MagicMock configured with basic scope attributes.
    """
    scope = MagicMock()
    scope.id = "test-scope"
    return scope
