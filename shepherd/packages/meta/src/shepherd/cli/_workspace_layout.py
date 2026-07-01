"""Workspace layout helpers used by the packaged Shepherd CLI."""

from __future__ import annotations

import sys
from functools import lru_cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path, PurePosixPath
from typing import Any, Literal

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


@lru_cache(maxsize=1)
def _repo_impl() -> Any | None:
    """Load the repo-owned helper when this module lives inside a source checkout."""
    current_file = Path(__file__).resolve()
    current = current_file.parent

    for parent in [current, *current.parents]:
        impl_path = parent / "scripts" / "_workspace_layout_impl.py"
        if not impl_path.is_file():
            continue
        if find_workspace_root(parent) != parent:
            continue

        expected_module_path = _expected_checkout_module_path(parent)
        if expected_module_path is None or expected_module_path != current_file:
            continue

        return _load_repo_impl_module(impl_path)

    return None


def _load_repo_impl_module(impl_path: Path) -> Any:
    module_name = "_workspace_layout_impl"
    resolved_impl_path = impl_path.resolve()
    existing = sys.modules.get(module_name)
    if existing is not None:
        existing_file = getattr(existing, "__file__", None)
        if existing_file is not None and Path(existing_file).resolve() == resolved_impl_path:
            return existing

    spec = spec_from_file_location(module_name, resolved_impl_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load workspace layout helper from {resolved_impl_path}.")

    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _delegate_or_raise(helper_name: str) -> object:
    impl = _repo_impl()
    if impl is None:
        raise _repo_only(helper_name)
    return getattr(impl, helper_name)


def _repo_only(helper_name: str) -> WorkspaceLayoutError:
    return WorkspaceLayoutError(f"{helper_name} is only available when running inside the source checkout.")


def _expected_checkout_module_path(root: Path) -> Path | None:
    """Return the canonical checkout path for this module under ``root``."""
    resolved_root = root.resolve()

    try:
        layout = _detect_layout_fallback(resolved_root)
    except WorkspaceLayoutError:
        return None

    if layout == "flat":
        return (resolved_root / "packages" / "shepherd" / "src" / "shepherd" / "cli" / "_workspace_layout.py").resolve()
    return (
        resolved_root / "shepherd" / "packages" / "meta" / "src" / "shepherd" / "cli" / "_workspace_layout.py"
    ).resolve()


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
    impl = _repo_impl()
    if impl is not None:
        return impl.require_workspace_root(start)

    root = find_workspace_root(start)
    if root is None:
        raise WorkspaceLayoutError(f"Could not locate a uv workspace root from {start or Path.cwd()}.")
    return root


def workspace_members(root: Path) -> tuple[str, ...]:
    """Return the declared uv workspace members from the root pyproject."""
    impl = _repo_impl()
    if impl is not None:
        return impl.workspace_members(root)

    return _workspace_members_fallback(root)


def _workspace_members_fallback(root: Path) -> tuple[str, ...]:
    """Return the declared uv workspace members without repo delegation."""
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
    impl = _repo_impl()
    if impl is not None:
        return impl.detect_layout(root)

    return _detect_layout_fallback(root)


def _detect_layout_fallback(root: Path) -> LayoutKind:
    """Detect layout without consulting the repo-owned implementation."""
    resolved_root = root.resolve()
    members = _workspace_members_fallback(resolved_root)

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


def new_shepherd_package_dir(root: Path, name: str) -> Path:
    """Return the target directory for a newly scaffolded Shepherd package."""
    impl = _repo_impl()
    if impl is not None:
        return impl.new_shepherd_package_dir(root, name)

    resolved_root = root.resolve()
    layout = detect_layout(resolved_root)
    if layout == "flat":
        return resolved_root / "packages" / f"shepherd-{name}"
    return resolved_root / "shepherd" / "extras" / name


def new_shepherd_workspace_member(root: Path, name: str) -> str:
    """Return the workspace member path for a newly scaffolded Shepherd package."""
    impl = _repo_impl()
    if impl is not None:
        return impl.new_shepherd_workspace_member(root, name)

    package_path = new_shepherd_package_dir(root, name)
    return package_path.relative_to(root.resolve()).as_posix()


def workspace_member_covers(root: Path, relative_path: str) -> bool:
    """Return whether an existing workspace member entry already covers a path."""
    impl = _repo_impl()
    if impl is not None:
        return impl.workspace_member_covers(root, relative_path)

    target = PurePosixPath(relative_path)
    return any(target.match(member) for member in workspace_members(root))


def iter_workspace_package_dirs(root: Path) -> tuple[Path, ...]:
    return _delegate_or_raise("iter_workspace_package_dirs")(root)


def workspace_collection_targets(root: Path) -> tuple[Path, ...]:
    return _delegate_or_raise("workspace_collection_targets")(root)


def package_dir_map(root: Path) -> dict[str, Path]:
    return _delegate_or_raise("package_dir_map")(root)


def package_dir(root: Path, distribution_name: str) -> Path:
    return _delegate_or_raise("package_dir")(root, distribution_name)


def integration_tests_dir(root: Path) -> Path | None:
    return _delegate_or_raise("integration_tests_dir")(root)


def project_docs_dir(root: Path, project: Literal["shepherd", "vcs-core"], kind: str) -> Path | None:
    return _delegate_or_raise("project_docs_dir")(root, project, kind)


_IMPL = _repo_impl()
if _IMPL is not None:
    WorkspaceLayoutError = _IMPL.WorkspaceLayoutError


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
