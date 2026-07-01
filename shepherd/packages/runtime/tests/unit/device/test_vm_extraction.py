"""Tests for VM effect extraction.

Unit tests use mocked subprocess/tar calls for fast, reliable testing.
Integration tests (marked @pytest.mark.integration) require a running
Podman Machine on macOS.

Note on private method tests:
-----------------------------
This file includes tests for private methods (_has_content, _parse_tar).
This is INTENTIONAL for the following reasons:

1. **Complex parsing logic**: The tar parsing handles multiple whiteout formats
   (prefix-based and character device), path normalization, and binary data.
   Testing this in isolation ensures correctness before integration.

2. **Difficult to test through public API**: The public API (read_upper_layer)
   requires VM access. Testing parsing logic separately allows fast unit tests
   that run without a Podman Machine.

3. **Better error diagnosis**: When parsing fails, tests at the _parse_tar
   level pinpoint the exact failure (e.g., "whiteout detection failed")
   rather than a generic "extraction failed" at the public API level.

These tests follow the "test at the lowest appropriate level" principle:
- Public API tests verify integration behavior
- Private method tests verify complex internal logic
"""

import io
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from shepherd_runtime.device.container.vm_extraction import (
    VMFileInfo,
    VMUpperLayerReader,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_runner():
    """Mock VMCommandRunner for unit tests."""
    runner = MagicMock()
    runner.run.return_value = MagicMock(
        returncode=0,
        stdout="some_file.txt",
        stderr="",
    )
    return runner


def create_test_tar(*files: tuple[str, bytes | None, bool]) -> bytes:
    """Create a tar archive with specified files.

    Args:
        files: Tuples of (name, content, is_dir).
               content is None for directories.

    Returns:
        Raw tar archive bytes.
    """
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:") as tar:
        for name, content, is_dir in files:
            info = tarfile.TarInfo(name=name)
            if is_dir:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                content = content or b""
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return tar_buffer.getvalue()


# =============================================================================
# VMUpperLayerReader Unit Tests
# =============================================================================


class TestVMUpperLayerReaderHasContent:
    """Tests for _has_content() method (private).

    Note: Testing a private method is intentional here. This method determines
    whether to attempt tar extraction, and testing it in isolation is simpler
    than mocking the full extraction pipeline. See module docstring.
    """

    def test_has_content_true(self, mock_runner):
        """Directory with content returns True."""
        mock_runner.run.return_value = MagicMock(
            returncode=0,
            stdout="file.txt\n",
            stderr="",
        )

        reader = VMUpperLayerReader(mock_runner)
        assert reader._has_content(Path("/var/overlays/task/upper")) is True

    def test_has_content_empty(self, mock_runner):
        """Empty directory returns False."""
        mock_runner.run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        reader = VMUpperLayerReader(mock_runner)
        assert reader._has_content(Path("/var/overlays/task/upper")) is False

    def test_has_content_not_exists(self, mock_runner):
        """Non-existent directory returns False."""
        mock_runner.run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="",
        )

        reader = VMUpperLayerReader(mock_runner)
        assert reader._has_content(Path("/nonexistent")) is False


class TestVMUpperLayerReaderParseTar:
    """Tests for _parse_tar() method (private).

    Note: Testing this private method is intentional. The tar parsing handles:
    - Multiple whiteout formats (.wh.* prefix and char device 0,0)
    - Path normalization (leading ./ removal)
    - Binary content extraction
    - Directory vs file distinction

    Testing this complex parsing logic in isolation allows thorough coverage
    without needing a running VM. See module docstring.
    """

    def test_parse_regular_file(self, mock_runner):
        """Regular files are extracted with content."""
        tar_data = create_test_tar(
            ("test.py", b"print('hello')", False),
        )

        reader = VMUpperLayerReader(mock_runner)
        files = list(reader._parse_tar(tar_data))

        assert len(files) == 1
        assert files[0].relative_path == Path("test.py")
        assert files[0].content == b"print('hello')"
        assert not files[0].is_whiteout
        assert not files[0].is_directory

    def test_parse_multiple_files(self, mock_runner):
        """Multiple files are all extracted."""
        tar_data = create_test_tar(
            ("file1.py", b"content1", False),
            ("file2.py", b"content2", False),
            ("subdir/file3.py", b"content3", False),
        )

        reader = VMUpperLayerReader(mock_runner)
        files = list(reader._parse_tar(tar_data))

        assert len(files) == 3
        paths = {str(f.relative_path) for f in files}
        assert paths == {"file1.py", "file2.py", "subdir/file3.py"}

    def test_parse_directory(self, mock_runner):
        """Directories are identified correctly."""
        tar_data = create_test_tar(
            ("subdir", None, True),
        )

        reader = VMUpperLayerReader(mock_runner)
        files = list(reader._parse_tar(tar_data))

        assert len(files) == 1
        assert files[0].relative_path == Path("subdir")
        assert files[0].is_directory
        assert files[0].content is None

    def test_parse_whiteout(self, mock_runner):
        """Whiteout files are detected and path stripped."""
        tar_data = create_test_tar(
            (".wh.deleted.py", b"", False),
        )

        reader = VMUpperLayerReader(mock_runner)
        files = list(reader._parse_tar(tar_data))

        assert len(files) == 1
        assert files[0].relative_path == Path("deleted.py")  # .wh. stripped
        assert files[0].is_whiteout
        assert not files[0].is_directory

    def test_parse_whiteout_in_subdir(self, mock_runner):
        """Whiteout in subdirectory preserves directory path."""
        tar_data = create_test_tar(
            ("subdir/.wh.removed.txt", b"", False),
        )

        reader = VMUpperLayerReader(mock_runner)
        files = list(reader._parse_tar(tar_data))

        assert len(files) == 1
        assert files[0].relative_path == Path("subdir/removed.txt")
        assert files[0].is_whiteout

    def test_parse_chardev_whiteout(self, mock_runner):
        """Character device (0,0) whiteouts are detected (D9)."""
        # Create tar with character device entry
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:") as tar:
            info = tarfile.TarInfo(name="deleted_file.txt")
            info.type = tarfile.CHRTYPE
            info.devmajor = 0
            info.devminor = 0
            tar.addfile(info)

        reader = VMUpperLayerReader(mock_runner)
        files = list(reader._parse_tar(tar_buffer.getvalue()))

        assert len(files) == 1
        assert files[0].relative_path == Path("deleted_file.txt")
        assert files[0].is_whiteout
        assert not files[0].is_directory
        assert files[0].content is None

    def test_parse_chardev_whiteout_in_subdir(self, mock_runner):
        """Character device whiteout in subdirectory preserves path."""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:") as tar:
            info = tarfile.TarInfo(name="subdir/removed.py")
            info.type = tarfile.CHRTYPE
            info.devmajor = 0
            info.devminor = 0
            tar.addfile(info)

        reader = VMUpperLayerReader(mock_runner)
        files = list(reader._parse_tar(tar_buffer.getvalue()))

        assert len(files) == 1
        assert files[0].relative_path == Path("subdir/removed.py")
        assert files[0].is_whiteout

    def test_parse_skips_root_dot(self, mock_runner):
        """Root '.' entry is skipped."""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:") as tar:
            info = tarfile.TarInfo(name=".")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
            info = tarfile.TarInfo(name="file.txt")
            info.size = 4
            tar.addfile(info, io.BytesIO(b"test"))

        reader = VMUpperLayerReader(mock_runner)
        files = list(reader._parse_tar(tar_buffer.getvalue()))

        # Should only have file.txt, not '.'
        assert len(files) == 1
        assert files[0].relative_path == Path("file.txt")

    def test_parse_normalizes_leading_dot_slash(self, mock_runner):
        """Paths starting with ./ are normalized."""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:") as tar:
            info = tarfile.TarInfo(name="./file.txt")
            info.size = 4
            tar.addfile(info, io.BytesIO(b"test"))

        reader = VMUpperLayerReader(mock_runner)
        files = list(reader._parse_tar(tar_buffer.getvalue()))

        assert len(files) == 1
        assert files[0].relative_path == Path("file.txt")

    def test_parse_empty_tar(self, mock_runner):
        """Empty tar yields no files."""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:") as tar:
            pass  # Empty archive

        reader = VMUpperLayerReader(mock_runner)
        files = list(reader._parse_tar(tar_buffer.getvalue()))

        assert len(files) == 0

    def test_parse_invalid_tar(self, mock_runner):
        """Invalid tar data yields no files (with warning)."""
        reader = VMUpperLayerReader(mock_runner)
        files = list(reader._parse_tar(b"not a tar file"))

        assert len(files) == 0


class TestVMUpperLayerReaderReadUpperLayer:
    """Tests for read_upper_layer() method."""

    def test_empty_upper_layer(self, mock_runner):
        """Empty upper layer yields no files."""
        mock_runner.run.return_value = MagicMock(
            returncode=1,  # Directory doesn't exist or empty
            stdout="",
            stderr="",
        )

        reader = VMUpperLayerReader(mock_runner)
        files = list(reader.read_upper_layer(Path("/var/overlays/task/upper")))

        assert files == []

    def test_reads_via_ssh_tar(self, mock_runner):
        """Upper layer is read via SSH + tar command."""
        # First call: _has_content check
        # Second call: tar extraction (mocked via subprocess)
        mock_runner.run.return_value = MagicMock(
            returncode=0,
            stdout="file.txt\n",
            stderr="",
        )

        tar_data = create_test_tar(("file.txt", b"content", False))

        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.return_value = MagicMock(
                returncode=0,
                stdout=tar_data,
                stderr=b"",
            )

            reader = VMUpperLayerReader(mock_runner)
            files = list(reader.read_upper_layer(Path("/var/overlays/task/upper")))

            assert len(files) == 1
            assert files[0].relative_path == Path("file.txt")

            # Verify tar command was called
            mock_subprocess.assert_called_once()
            cmd = mock_subprocess.call_args[0][0]
            assert cmd[0:3] == ["podman", "machine", "ssh"]
            assert "tar" in cmd[3]


# =============================================================================
# OverlayEffectExtractor VM Path Tests
# =============================================================================


class TestOverlayEffectExtractorVMRouting:
    """Tests for VM path routing in OverlayEffectExtractor."""

    def test_routes_vm_path_to_extract_from_vm(self):
        """VM paths route to _extract_from_vm."""
        from shepherd_runtime.device.container.effect_collector import EffectCollector
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
        from shepherd_runtime.device.container.podman import OverlayMount

        mock_runner = MagicMock()
        extractor = OverlayEffectExtractor(vm_runner=mock_runner)

        overlay = OverlayMount(
            task_id="test-task",
            context_name="workspace",
            lower=Path("/Users/test/project"),
            upper=Path("/var/shepherd/overlays/test-task/workspace/upper"),
            work=Path("/var/shepherd/overlays/test-task/workspace/work"),
            merged=Path("/var/shepherd/overlays/test-task/workspace/merged"),
            is_vm_path=True,
            original_host_path=Path("/Users/test/project"),
        )
        collector = EffectCollector()

        with patch.object(extractor, "_extract_from_vm", return_value=[]) as mock_method:
            extractor.extract(overlay, collector)
            mock_method.assert_called_once()

    def test_routes_local_path_to_extract_from_upper(self, tmp_path):
        """Local paths with upper content route to _extract_from_upper."""
        from shepherd_runtime.device.container.effect_collector import EffectCollector
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
        from shepherd_runtime.device.container.podman import OverlayMount

        # Create upper directory with content
        upper = tmp_path / "upper"
        upper.mkdir()
        (upper / "file.txt").write_text("content")

        extractor = OverlayEffectExtractor()

        overlay = OverlayMount(
            task_id="test-task",
            context_name="workspace",
            lower=tmp_path / "lower",
            upper=upper,
            work=tmp_path / "work",
            merged=tmp_path / "merged",
            is_vm_path=False,
        )
        collector = EffectCollector()

        with patch.object(extractor, "_extract_from_upper", return_value=[]) as mock_method:
            extractor.extract(overlay, collector)
            mock_method.assert_called_once()

    def test_vm_extraction_without_runner_raises(self):
        """VM extraction without vm_runner raises RuntimeError."""
        from shepherd_runtime.device.container.effect_collector import EffectCollector
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
        from shepherd_runtime.device.container.podman import OverlayMount

        extractor = OverlayEffectExtractor()  # No vm_runner

        overlay = OverlayMount(
            task_id="test-task",
            context_name="workspace",
            lower=Path("/Users/test"),
            upper=Path("/var/overlays/upper"),
            work=Path("/var/overlays/work"),
            merged=Path("/var/overlays/merged"),
            is_vm_path=True,
        )
        collector = EffectCollector()

        with pytest.raises(RuntimeError, match="vm_runner"):
            extractor.extract(overlay, collector)


class TestOverlayEffectExtractorVMExtraction:
    """Tests for _extract_from_vm method."""

    def test_extracts_new_file(self, tmp_path):
        """New file in VM upper produces FileCreate effect."""
        from shepherd_core.effects import FileCreate
        from shepherd_runtime.device.container.effect_collector import EffectCollector
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
        from shepherd_runtime.device.container.podman import OverlayMount

        # Create empty lower layer on "host"
        lower = tmp_path / "project"
        lower.mkdir()

        mock_runner = MagicMock()
        extractor = OverlayEffectExtractor(vm_runner=mock_runner)

        overlay = OverlayMount(
            task_id="test-task",
            context_name="workspace",
            lower=Path("/Users/test/project"),
            upper=Path("/var/overlays/upper"),
            work=Path("/var/overlays/work"),
            merged=Path("/var/overlays/merged"),
            is_vm_path=True,
            original_host_path=lower,
        )
        collector = EffectCollector()

        # Mock VMUpperLayerReader - set _vm_reader directly
        mock_vm_reader = MagicMock()
        mock_vm_reader.read_upper_layer.return_value = iter(
            [
                VMFileInfo(
                    relative_path=Path("new_file.py"),
                    is_whiteout=False,
                    is_directory=False,
                    content=b"print('new')",
                ),
            ]
        )
        extractor._vm_reader = mock_vm_reader

        effects = extractor._extract_from_vm(overlay, caused_by="intent-123")

        assert len(effects) == 1
        assert isinstance(effects[0], FileCreate)
        assert effects[0].path == "new_file.py"
        assert effects[0].content == "print('new')"
        assert effects[0].caused_by == "intent-123"

    def test_extracts_modified_file(self, tmp_path):
        """Modified file in VM upper produces FilePatch effect."""
        from shepherd_core.effects import FilePatch
        from shepherd_runtime.device.container.effect_collector import EffectCollector
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
        from shepherd_runtime.device.container.podman import OverlayMount

        # Create lower layer with existing file on "host"
        lower = tmp_path / "project"
        lower.mkdir()
        (lower / "existing.py").write_text("original content")

        mock_runner = MagicMock()
        extractor = OverlayEffectExtractor(vm_runner=mock_runner)

        overlay = OverlayMount(
            task_id="test-task",
            context_name="workspace",
            lower=Path("/Users/test/project"),
            upper=Path("/var/overlays/upper"),
            work=Path("/var/overlays/work"),
            merged=Path("/var/overlays/merged"),
            is_vm_path=True,
            original_host_path=lower,
        )
        collector = EffectCollector()

        # Mock VMUpperLayerReader
        mock_vm_reader = MagicMock()
        mock_vm_reader.read_upper_layer.return_value = iter(
            [
                VMFileInfo(
                    relative_path=Path("existing.py"),
                    is_whiteout=False,
                    is_directory=False,
                    content=b"modified content",
                ),
            ]
        )
        extractor._vm_reader = mock_vm_reader

        effects = extractor._extract_from_vm(overlay, caused_by="intent-456")

        assert len(effects) == 1
        assert isinstance(effects[0], FilePatch)
        assert effects[0].path == "existing.py"
        assert effects[0].old_content == "original content"
        assert effects[0].new_content == "modified content"
        assert effects[0].caused_by == "intent-456"

    def test_extracts_deleted_file(self, tmp_path):
        """Whiteout in VM upper produces FileDelete effect."""
        from shepherd_core.effects import FileDelete
        from shepherd_runtime.device.container.effect_collector import EffectCollector
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
        from shepherd_runtime.device.container.podman import OverlayMount

        # Create lower layer with file to be deleted
        lower = tmp_path / "project"
        lower.mkdir()
        (lower / "to_delete.py").write_text("delete me")

        mock_runner = MagicMock()
        extractor = OverlayEffectExtractor(vm_runner=mock_runner)

        overlay = OverlayMount(
            task_id="test-task",
            context_name="workspace",
            lower=Path("/Users/test/project"),
            upper=Path("/var/overlays/upper"),
            work=Path("/var/overlays/work"),
            merged=Path("/var/overlays/merged"),
            is_vm_path=True,
            original_host_path=lower,
        )
        collector = EffectCollector()

        # Mock VMUpperLayerReader
        mock_vm_reader = MagicMock()
        mock_vm_reader.read_upper_layer.return_value = iter(
            [
                VMFileInfo(
                    relative_path=Path("to_delete.py"),
                    is_whiteout=True,
                    is_directory=False,
                    content=None,
                ),
            ]
        )
        extractor._vm_reader = mock_vm_reader

        effects = extractor._extract_from_vm(overlay, caused_by="intent-789")

        assert len(effects) == 1
        assert isinstance(effects[0], FileDelete)
        assert effects[0].path == "to_delete.py"
        assert effects[0].had_content == "delete me"
        assert effects[0].caused_by == "intent-789"

    def test_skips_directories(self, tmp_path):
        """Directories in VM upper are skipped."""
        from shepherd_runtime.device.container.effect_collector import EffectCollector
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
        from shepherd_runtime.device.container.podman import OverlayMount

        lower = tmp_path / "project"
        lower.mkdir()

        mock_runner = MagicMock()
        extractor = OverlayEffectExtractor(vm_runner=mock_runner)

        overlay = OverlayMount(
            task_id="test-task",
            context_name="workspace",
            lower=Path("/Users/test/project"),
            upper=Path("/var/overlays/upper"),
            work=Path("/var/overlays/work"),
            merged=Path("/var/overlays/merged"),
            is_vm_path=True,
            original_host_path=lower,
        )
        collector = EffectCollector()

        # Mock VMUpperLayerReader
        mock_vm_reader = MagicMock()
        mock_vm_reader.read_upper_layer.return_value = iter(
            [
                VMFileInfo(
                    relative_path=Path("new_dir"),
                    is_whiteout=False,
                    is_directory=True,
                    content=None,
                ),
            ]
        )
        extractor._vm_reader = mock_vm_reader

        effects = extractor._extract_from_vm(overlay, caused_by=None)

        assert len(effects) == 0


class TestUnreadableLowerFile:
    """Tests for handling unreadable lower files."""

    def test_modified_file_unreadable_lower_still_produces_patch(self, tmp_path):
        """When lower file exists but is unreadable, still emit FilePatch."""
        from shepherd_core.effects import FilePatch
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
        from shepherd_runtime.device.container.podman import OverlayMount

        # Create lower layer with unreadable file
        lower = tmp_path / "project"
        lower.mkdir()
        unreadable_file = lower / "secret.py"
        unreadable_file.write_text("original secret")
        unreadable_file.chmod(0o000)  # Make unreadable

        try:
            mock_runner = MagicMock()
            extractor = OverlayEffectExtractor(vm_runner=mock_runner)

            overlay = OverlayMount(
                task_id="test-task",
                context_name="workspace",
                lower=Path("/Users/test/project"),
                upper=Path("/var/overlays/upper"),
                work=Path("/var/overlays/work"),
                merged=Path("/var/overlays/merged"),
                is_vm_path=True,
                original_host_path=lower,
            )

            # Mock VMUpperLayerReader
            mock_vm_reader = MagicMock()
            mock_vm_reader.read_upper_layer.return_value = iter(
                [
                    VMFileInfo(
                        relative_path=Path("secret.py"),
                        is_whiteout=False,
                        is_directory=False,
                        content=b"modified secret",
                    ),
                ]
            )
            extractor._vm_reader = mock_vm_reader

            effects = extractor._extract_from_vm(overlay, caused_by="intent-123")

            # Should be FilePatch (not FileCreate) with empty old_content
            assert len(effects) == 1
            assert isinstance(effects[0], FilePatch)
            assert effects[0].path == "secret.py"
            assert effects[0].old_content == ""  # Empty because unreadable
            assert effects[0].new_content == "modified secret"
            assert effects[0].caused_by == "intent-123"

        finally:
            # Restore permissions for cleanup
            unreadable_file.chmod(0o644)

    def test_deleted_file_unreadable_lower_still_produces_delete(self, tmp_path):
        """When deleted file's original is unreadable, still emit FileDelete."""
        from shepherd_core.effects import FileDelete
        from shepherd_runtime.device.container.overlay_extractor import OverlayEffectExtractor
        from shepherd_runtime.device.container.podman import OverlayMount

        # Create lower layer with unreadable file
        lower = tmp_path / "project"
        lower.mkdir()
        unreadable_file = lower / "secret.py"
        unreadable_file.write_text("secret content")
        unreadable_file.chmod(0o000)

        try:
            mock_runner = MagicMock()
            extractor = OverlayEffectExtractor(vm_runner=mock_runner)

            overlay = OverlayMount(
                task_id="test-task",
                context_name="workspace",
                lower=Path("/Users/test/project"),
                upper=Path("/var/overlays/upper"),
                work=Path("/var/overlays/work"),
                merged=Path("/var/overlays/merged"),
                is_vm_path=True,
                original_host_path=lower,
            )

            # Mock VMUpperLayerReader
            mock_vm_reader = MagicMock()
            mock_vm_reader.read_upper_layer.return_value = iter(
                [
                    VMFileInfo(
                        relative_path=Path("secret.py"),
                        is_whiteout=True,
                        is_directory=False,
                        content=None,
                    ),
                ]
            )
            extractor._vm_reader = mock_vm_reader

            effects = extractor._extract_from_vm(overlay, caused_by="intent-456")

            # Should be FileDelete with empty had_content
            assert len(effects) == 1
            assert isinstance(effects[0], FileDelete)
            assert effects[0].path == "secret.py"
            assert effects[0].had_content == ""  # Empty because unreadable
            assert effects[0].caused_by == "intent-456"

        finally:
            unreadable_file.chmod(0o644)
