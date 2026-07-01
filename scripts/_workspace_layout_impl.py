# ruff: noqa: INP001
"""Canonical workspace layout helpers for repo-local tooling."""

from __future__ import annotations

from functools import cache
from pathlib import Path, PurePosixPath
from typing import Literal

import tomllib

LayoutKind = Literal["flat", "nested"]

_NESTED_PACKAGE_PARENTS = (
    Path("shepherd/packages"),
    Path("shepherd/extras"),
    Path("vcs-core/packages"),
    Path("vcs-core/extras"),
)


class WorkspaceLayoutError(RuntimeError):
    """Raised when the repository layout cannot be resolved."""


def find_workspace_root(start: Path | None = None) -> Path | None:
    """Return the nearest ancestor that looks like a uv workspace root."""
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent

    for parent in [current, *current.parents]:
        pyproject = parent / "pyproject.toml"
        if not pyproject.exists():
            continue
        try:
            contents = pyproject.read_text(encoding="utf-8")
        except OSError:
            continue
        if "[tool.uv.workspace]" in contents:
            return parent
    return None


def require_workspace_root(start: Path | None = None) -> Path:
    """Return the workspace root or raise if none can be found."""
    root = find_workspace_root(start)
    if root is None:
        raise WorkspaceLayoutError(f"Could not locate a uv workspace root from {start or Path.cwd()}.")
    return root


def workspace_members(root: Path) -> tuple[str, ...]:
    """Return the declared uv workspace members from the root pyproject."""
    pyproject = root.resolve() / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WorkspaceLayoutError(f"Could not read {pyproject}.") from exc

    members = data.get("tool", {}).get("uv", {}).get("workspace", {}).get("members")
    if not isinstance(members, list):
        raise WorkspaceLayoutError(f"{pyproject} does not define [tool.uv.workspace].members.")
    return tuple(str(member) for member in members)


def detect_layout(root: Path) -> LayoutKind:
    """Return the recognized repository layout for ``root``."""
    resolved_root = root.resolve()
    members = workspace_members(resolved_root)

    has_flat_members = any(member.startswith("packages/") for member in members)
    has_nested_members = any(member.startswith(("shepherd/", "vcs-core/")) for member in members)
    if has_flat_members and has_nested_members:
        raise WorkspaceLayoutError("Workspace members mix flat and nested layout entries.")
    if has_nested_members:
        return "nested"
    if has_flat_members:
        return "flat"

    flat = (resolved_root / "packages").is_dir()
    nested = any((resolved_root / relative_parent).is_dir() for relative_parent in _NESTED_PACKAGE_PARENTS)
    if flat and nested:
        raise WorkspaceLayoutError("Could not infer layout because both flat and nested package parents exist.")
    if nested:
        return "nested"
    if flat:
        return "flat"
    raise WorkspaceLayoutError(f"Could not detect a supported repo layout under {resolved_root}.")


def iter_workspace_package_dirs(root: Path) -> tuple[Path, ...]:
    """Return package directories that participate in the uv workspace."""
    resolved_root = root.resolve()
    package_dirs: list[Path] = []
    seen: set[Path] = set()

    for member in workspace_members(resolved_root):
        for path in _expand_member(resolved_root, member):
            if path in seen:
                continue
            seen.add(path)
            package_dirs.append(path)

    return tuple(package_dirs)


def workspace_collection_targets(root: Path) -> tuple[Path, ...]:
    """Return broad pytest collection roots for the active workspace layout."""
    resolved_root = root.resolve()
    targets: list[Path] = []
    seen: set[Path] = set()

    for member in workspace_members(resolved_root):
        collection_root = _collection_target_for_member(resolved_root, member)
        if collection_root is None or collection_root in seen or not collection_root.is_dir():
            continue
        seen.add(collection_root)
        targets.append(collection_root)

    return tuple(targets)


@cache
def package_dir_map(root: Path) -> dict[str, Path]:
    """Map distribution names to their containing package directories."""
    resolved_root = root.resolve()
    mapping: dict[str, Path] = {}
    for package_dir in iter_workspace_package_dirs(resolved_root):
        project_name = _project_name_from_pyproject(package_dir / "pyproject.toml")
        mapping[project_name] = package_dir
    return mapping


def package_dir(root: Path, distribution_name: str) -> Path:
    """Return the package dir for a workspace distribution name."""
    try:
        return package_dir_map(root.resolve())[distribution_name]
    except KeyError as exc:
        raise WorkspaceLayoutError(f"Workspace package {distribution_name!r} was not found.") from exc


def integration_tests_dir(root: Path) -> Path | None:
    """Return the integration-tests directory for the active layout."""
    resolved_root = root.resolve()
    layout = detect_layout(resolved_root)
    candidate = resolved_root / "integration-tests" if layout == "flat" else resolved_root / "shepherd" / "integration-tests"
    return candidate if candidate.is_dir() else None


def project_docs_dir(root: Path, project: Literal["shepherd", "vcs-core"], kind: str) -> Path | None:
    """Return a project-specific docs/design/examples/eval directory when one exists."""
    resolved_root = root.resolve()
    layout = detect_layout(resolved_root)
    if layout == "nested":
        candidate = resolved_root / project / kind
        return candidate if candidate.is_dir() else None

    if project == "vcs-core" and kind == "design":
        candidate = resolved_root / "design" / "vcs-core"
        return candidate if candidate.is_dir() else None

    return None


def new_shepherd_package_dir(root: Path, name: str) -> Path:
    """Return the target directory for a newly scaffolded Shepherd package."""
    resolved_root = root.resolve()
    layout = detect_layout(resolved_root)
    if layout == "flat":
        return resolved_root / "packages" / f"shepherd-{name}"
    return resolved_root / "shepherd" / "extras" / name


def new_shepherd_workspace_member(root: Path, name: str) -> str:
    """Return the workspace member path for a newly scaffolded Shepherd package."""
    package_path = new_shepherd_package_dir(root, name)
    return package_path.relative_to(root.resolve()).as_posix()


def workspace_member_covers(root: Path, relative_path: str) -> bool:
    """Return whether an existing workspace member entry already covers a path."""
    target = PurePosixPath(relative_path)
    return any(target.match(member) for member in workspace_members(root))


def _expand_member(root: Path, member: str) -> tuple[Path, ...]:
    if any(char in member for char in "*?[]"):
        return tuple(
            path.resolve()
            for path in sorted(root.glob(member))
            if path.is_dir() and (path / "pyproject.toml").is_file()
        )

    candidate = (root / Path(member)).resolve()
    if candidate.is_dir() and (candidate / "pyproject.toml").is_file():
        return (candidate,)
    return ()


def _collection_target_for_member(root: Path, member: str) -> Path | None:
    relative = Path(member)
    parts = relative.parts
    if not parts:
        return None
    if parts[0] == "packages":
        return (root / "packages").resolve()
    if len(parts) >= 2 and parts[0] in {"shepherd", "vcs-core"} and parts[1] in {"packages", "extras"}:
        return (root / parts[0] / parts[1]).resolve()
    return None


def _project_name_from_pyproject(pyproject_path: Path) -> str:
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WorkspaceLayoutError(f"Could not read {pyproject_path}.") from exc

    name = data.get("project", {}).get("name")
    if not isinstance(name, str) or not name:
        raise WorkspaceLayoutError(f"{pyproject_path} is missing project.name.")
    return name


__all__ = [
    "LayoutKind",
    "WorkspaceLayoutError",
    "detect_layout",
    "find_workspace_root",
    "integration_tests_dir",
    "iter_workspace_package_dirs",
    "new_shepherd_package_dir",
    "new_shepherd_workspace_member",
    "package_dir",
    "package_dir_map",
    "project_docs_dir",
    "require_workspace_root",
    "workspace_collection_targets",
    "workspace_member_covers",
    "workspace_members",
]
