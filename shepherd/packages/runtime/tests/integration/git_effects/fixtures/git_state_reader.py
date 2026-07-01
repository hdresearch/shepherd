"""GitStateReader Prototype.

Extracted from spike_git_folder_observation.py (Spike A1).

Key Findings (validated by A1):
- Direct file reading is 46x faster than subprocess (1ms vs 46ms)
- Refs are simple text files: refs/heads/<name> contains SHA
- Objects can be parsed with zlib.decompress() + text parsing
- Packed refs are handled as fallback (though A4 confirmed they're rare in containers)

This prototype implementation should move to production in Phase 1.
Production code should pass the same tests.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GitStateSnapshot:
    """Snapshot of git repository state.

    Immutable dataclass for capturing git state at a point in time.
    Used for before/after diffing to detect git operations.
    """

    head_ref: str | None  # Current branch name (None if detached HEAD)
    head_commit: str  # HEAD commit SHA (40 chars)
    branches: dict[str, str]  # branch_name -> commit_sha
    tags: dict[str, str]  # tag_name -> commit_sha

    def diff_branches(self, other: GitStateSnapshot) -> dict[str, str]:
        """Return branches that exist in other but not in self."""
        return {name: sha for name, sha in other.branches.items() if name not in self.branches}


class GitStateReader:
    """Read git state directly from .git directory.

    Design Decision D1: Use direct file reading as primary approach.
    46x faster than subprocess (1ms vs 46ms per Spike A1).

    Usage:
        reader = GitStateReader(repo_path / ".git")
        snapshot = reader.snapshot()  # ~1ms
        commit = reader.read_commit(snapshot.head_commit)
    """

    def __init__(self, git_dir: Path):
        """Initialize reader with .git directory path.

        Args:
            git_dir: Path to .git directory (not the repo root).
        """
        self.git_dir = Path(git_dir)

    def snapshot(self) -> GitStateSnapshot:
        """Capture current git state (~1ms).

        Returns:
            GitStateSnapshot with current HEAD, branches, and tags.
        """
        return GitStateSnapshot(
            head_ref=self._read_head_ref(),
            head_commit=self._read_head_commit(),
            branches=self._read_refs("heads"),
            tags=self._read_refs("tags"),
        )

    def _read_head_ref(self) -> str | None:
        """Read current branch name from HEAD.

        Returns:
            Branch name if on a branch, None if detached HEAD.
        """
        head_file = self.git_dir / "HEAD"
        if not head_file.exists():
            return None

        head = head_file.read_text().strip()
        if head.startswith("ref: refs/heads/"):
            return head[16:]  # Strip "ref: refs/heads/"
        return None  # Detached HEAD

    def _read_head_commit(self) -> str:
        """Resolve HEAD to commit SHA.

        Returns:
            40-character commit SHA.
        """
        head_file = self.git_dir / "HEAD"
        if not head_file.exists():
            return ""

        head = head_file.read_text().strip()

        if head.startswith("ref: "):
            # Symbolic ref - resolve to actual SHA
            ref_path = self.git_dir / head[5:]
            if ref_path.exists():
                return ref_path.read_text().strip()
            # Check packed-refs as fallback
            packed_sha = self._read_packed_ref(head[5:])
            return packed_sha or ""

        # Already a SHA (detached HEAD)
        return head

    def _read_refs(self, namespace: str) -> dict[str, str]:
        """Read all refs in a namespace (heads or tags).

        Args:
            namespace: Either "heads" for branches or "tags" for tags.

        Returns:
            Dict mapping ref names to commit SHAs.
        """
        refs: dict[str, str] = {}
        refs_dir = self.git_dir / "refs" / namespace

        # Loose refs (individual files)
        if refs_dir.exists():
            for ref_file in refs_dir.rglob("*"):
                if ref_file.is_file():
                    name = str(ref_file.relative_to(refs_dir))
                    refs[name] = ref_file.read_text().strip()

        # Packed refs (fallback for refs not in loose form)
        packed = self._read_all_packed_refs()
        prefix = f"refs/{namespace}/"
        for ref, sha in packed.items():
            if ref.startswith(prefix):
                name = ref[len(prefix) :]
                if name not in refs:  # Loose refs take precedence
                    refs[name] = sha

        return refs

    def _read_packed_ref(self, ref: str) -> str | None:
        """Read a single ref from packed-refs.

        Args:
            ref: Full ref path (e.g., "refs/heads/main").

        Returns:
            SHA if found, None otherwise.
        """
        return self._read_all_packed_refs().get(ref)

    def _read_all_packed_refs(self) -> dict[str, str]:
        """Read all packed refs.

        Returns:
            Dict mapping full ref paths to SHAs.
        """
        packed_file = self.git_dir / "packed-refs"
        if not packed_file.exists():
            return {}

        refs: dict[str, str] = {}
        for line in packed_file.read_text().split("\n"):
            line = line.strip()
            # Skip comments and peeled refs (annotated tag targets)
            if line and not line.startswith("#") and not line.startswith("^"):
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    sha, ref = parts
                    refs[ref] = sha
        return refs

    def read_commit(self, sha: str) -> dict[str, Any] | None:
        """Parse a commit object directly from .git/objects.

        This reads the loose object file and parses the commit content.
        Falls back to None if the object is packed or doesn't exist.

        Design Decision D1: Direct parsing is preferred over subprocess.

        Args:
            sha: 40-character commit SHA.

        Returns:
            Dict with commit fields, or None if not found/parseable.
            Fields: type, sha, tree, parents, author, committer, message
        """
        if len(sha) < 4:
            return None

        obj_path = self.git_dir / "objects" / sha[:2] / sha[2:]
        if obj_path.exists():
            try:
                # Git objects are zlib-compressed
                content = zlib.decompress(obj_path.read_bytes())

                # Format: "<type> <size>\0<content>"
                null_idx = content.index(b"\0")
                header = content[:null_idx].decode("ascii")
                body = content[null_idx + 1 :].decode("utf-8", errors="replace")

                obj_type, _ = header.split(" ", 1)
                if obj_type != "commit":
                    return None

                # Parse commit body
                return self._parse_commit_body(sha, body)

            except Exception:
                pass  # Fall through to git cat-file

        # Fallback: use git log for packed/alternate objects
        try:
            import subprocess

            result = subprocess.run(
                ["git", "log", "-1", "--format=%H%n%P%n%an%n%s%n%b", sha],
                cwd=self.git_dir.parent,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None

            lines = result.stdout.strip().split("\n")
            if len(lines) < 4:
                return None

            commit_sha = lines[0]
            parents = [p for p in lines[1].split() if p]
            author = lines[2]
            subject = lines[3]
            body = "\n".join(lines[4:]) if len(lines) > 4 else ""
            message = f"{subject}\n{body}".strip()

            return {
                "type": "commit",
                "sha": commit_sha,
                "parents": parents,
                "author": author,
                "message": message,
            }

        except Exception:
            return None

    def _parse_commit_body(self, sha: str, body: str) -> dict[str, Any]:
        """Parse commit object body into structured dict.

        Args:
            sha: Commit SHA (for inclusion in result).
            body: Decoded commit body text.

        Returns:
            Dict with commit fields.
        """
        lines = body.split("\n")
        result: dict[str, Any] = {
            "type": "commit",
            "sha": sha,
            "parents": [],
        }

        in_message = False
        message_lines: list[str] = []

        for line in lines:
            if in_message:
                message_lines.append(line)
            elif line == "":
                in_message = True
            elif line.startswith("tree "):
                result["tree"] = line[5:]
            elif line.startswith("parent "):
                result["parents"].append(line[7:])
            elif line.startswith("author "):
                result["author"] = line[7:]
            elif line.startswith("committer "):
                result["committer"] = line[10:]

        result["message"] = "\n".join(message_lines).strip()
        return result

    def read_tree(self, sha: str) -> list[dict[str, str]] | None:
        """Parse a tree object to get file entries.

        Args:
            sha: 40-character tree SHA.

        Returns:
            List of entry dicts with mode, name, sha; or None if not found.
        """
        if len(sha) < 4:
            return None

        obj_path = self.git_dir / "objects" / sha[:2] / sha[2:]
        if not obj_path.exists():
            return None

        try:
            content = zlib.decompress(obj_path.read_bytes())
            null_idx = content.index(b"\0")
            header = content[:null_idx].decode("ascii")
            body = content[null_idx + 1 :]

            obj_type, _ = header.split(" ", 1)
            if obj_type != "tree":
                return None

            return self._parse_tree_body(body)

        except Exception:
            return None

    def _parse_tree_body(self, body: bytes) -> list[dict[str, str]]:
        """Parse tree object body into list of entries.

        Args:
            body: Raw tree content bytes.

        Returns:
            List of entry dicts with mode, name, sha.
        """
        entries: list[dict[str, str]] = []
        idx = 0

        while idx < len(body):
            # Format: "<mode> <name>\0<20-byte sha>"
            space_idx = body.index(b" ", idx)
            null_idx = body.index(b"\0", space_idx)

            mode = body[idx:space_idx].decode("ascii")
            name = body[space_idx + 1 : null_idx].decode("utf-8", errors="replace")
            sha_bytes = body[null_idx + 1 : null_idx + 21]
            sha = sha_bytes.hex()

            entries.append({"mode": mode, "name": name, "sha": sha})
            idx = null_idx + 21

        return entries

    def get_branch_sha(self, branch_name: str) -> str | None:
        """Get the SHA for a specific branch.

        Args:
            branch_name: Branch name (without refs/heads/ prefix).

        Returns:
            40-character SHA, or None if branch doesn't exist.
        """
        # Check loose ref first
        ref_path = self.git_dir / "refs" / "heads" / branch_name
        if ref_path.exists():
            return ref_path.read_text().strip()

        # Fall back to packed refs
        return self._read_packed_ref(f"refs/heads/{branch_name}")

    def list_loose_objects(self, limit: int = 100) -> list[str]:
        """List loose object SHAs in the repository.

        Args:
            limit: Maximum number of objects to return.

        Returns:
            List of 40-character SHAs.
        """
        objects_dir = self.git_dir / "objects"
        if not objects_dir.exists():
            return []

        shas: list[str] = []
        for subdir in objects_dir.iterdir():
            if subdir.is_dir() and len(subdir.name) == 2:
                for obj_file in subdir.iterdir():
                    if obj_file.is_file():
                        sha = subdir.name + obj_file.name
                        shas.append(sha)
                        if len(shas) >= limit:
                            return shas
        return shas
