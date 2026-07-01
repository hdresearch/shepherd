"""Overlay backend doubles for vcs-core tests."""

from __future__ import annotations

from pathlib import Path

from vcs_core.types import FileState, normalize_git_filemode


class MockOverlayBackend:
    """In-memory overlay backend for FilesystemSubstrate tests."""

    def __init__(self) -> None:
        self.layers: dict[str, dict[str, FileState | None]] = {}
        self.committed: list[tuple[str, str | None]] = []
        self.discarded: list[str] = []
        self.pushed: list[str | None] = []
        self.deactivated = False

    def create_layer(self, scope_id: str, *, parent_scope_id: str | None) -> None:
        del parent_scope_id
        self.layers.setdefault(scope_id, {})

    def has_layer(self, scope_id: str) -> bool:
        return scope_id in self.layers

    def push_layer(self, scope_id: str | None = None) -> None:
        self.pushed.append(scope_id)

    def working_path(self, scope_id: str) -> Path:
        return Path("/virtual") / scope_id

    def diff_layer(self, scope_id: str) -> list[tuple[str, bytes | None, int]]:
        layer = self.layers.get(scope_id, {})
        return [
            (path, state.content, state.mode) if state is not None else (path, None, 0) for path, state in layer.items()
        ]

    def commit_layer(self, scope_id: str, *, into_scope_id: str | None) -> None:
        self.committed.append((scope_id, into_scope_id))

    def discard_layer(self, scope_id: str) -> None:
        self.discarded.append(scope_id)
        self.layers.pop(scope_id, None)

    def read_file(self, scope_id: str, path: str) -> bytes:
        return self.read_file_state(scope_id, path).content

    def read_file_state(self, scope_id: str, path: str) -> FileState:
        state = self.layers[scope_id][path]
        assert state is not None
        return state

    def write_file(self, scope_id: str, path: str, content: bytes, *, mode: int = 0o100644) -> None:
        self.layers.setdefault(scope_id, {})[path] = FileState(content, normalize_git_filemode(mode))

    def delete_file(self, scope_id: str, path: str) -> None:
        self.layers.setdefault(scope_id, {})[path] = None

    def deactivate(self) -> None:
        self.deactivated = True


class NoOpOverlayBackend:
    """Minimal backend used for authority reporting tests."""

    def create_layer(self, scope_id: str, *, parent_scope_id: str | None) -> None:
        del scope_id, parent_scope_id

    def has_layer(self, scope_id: str) -> bool:
        del scope_id
        return True

    def read_file(self, scope_id: str, path: str) -> bytes:
        del scope_id, path
        return b""

    def read_file_state(self, scope_id: str, path: str) -> FileState:
        del scope_id, path
        return FileState(b"")

    def write_file(self, scope_id: str, path: str, content: bytes, *, mode: int = 0o100644) -> None:
        del scope_id, path, content, mode

    def delete_file(self, scope_id: str, path: str) -> None:
        del scope_id, path

    def diff_layer(self, scope_id: str) -> list[tuple[str, bytes | None, int]]:
        del scope_id
        return []

    def commit_layer(self, scope_id: str, *, into_scope_id: str | None) -> None:
        del scope_id, into_scope_id

    def discard_layer(self, scope_id: str) -> None:
        del scope_id

    def push_layer(self, scope_id: str | None = None) -> None:
        del scope_id

    def working_path(self, scope_id: str) -> Path:
        return Path("/virtual") / scope_id

    def deactivate(self) -> None:
        pass
