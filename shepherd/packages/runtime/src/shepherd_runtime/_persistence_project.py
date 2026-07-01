"""Runtime-owned project identification and metadata for persistence."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProjectId:
    """Unique identifier for a project based on its canonical path."""

    canonical_path: str
    hash: str

    @classmethod
    def from_path(cls, path: Path) -> ProjectId:
        resolved = path.resolve()
        git_root = cls._find_git_root(resolved)
        canonical = str(git_root) if git_root else str(resolved)
        path_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return cls(canonical_path=canonical, hash=path_hash)

    @staticmethod
    def _find_git_root(path: Path) -> Path | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(path),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except (OSError, FileNotFoundError):
            pass
        return None

    def __repr__(self) -> str:
        return f"ProjectId({self.hash}, {self.canonical_path})"


@dataclass
class ProjectMetadata:
    """Metadata about a project stored in project.json."""

    canonical_path: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectMetadata:
        return cls(
            canonical_path=data["canonical_path"],
            created_at=datetime.fromisoformat(data["created_at"]),
            last_accessed=datetime.fromisoformat(data["last_accessed"]),
        )

    def save(self, path: Path) -> None:
        with open(path, "w") as file_handle:
            json.dump(self.to_dict(), file_handle, indent=2)

    @classmethod
    def load(cls, path: Path) -> ProjectMetadata:
        with open(path) as file_handle:
            data = json.load(file_handle)
        return cls.from_dict(data)

    def with_access_update(self) -> ProjectMetadata:
        return ProjectMetadata(
            canonical_path=self.canonical_path,
            created_at=self.created_at,
            last_accessed=datetime.now(timezone.utc),
        )


__all__ = [
    "ProjectId",
    "ProjectMetadata",
]
