"""Static boundary tests for Shepherd's vcs-core imports."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
ACTIVE_SHEPHERD_SRC_PARENTS = (
    REPO_ROOT / "shepherd" / "packages",
    REPO_ROOT / "shepherd" / "extras",
)

ALLOWED_PRIVATE_VCS_CORE_IMPORTS: dict[str, set[str]] = {}


def _active_shepherd_python_files() -> list[Path]:
    files: list[Path] = []
    for parent in ACTIVE_SHEPHERD_SRC_PARENTS:
        for src_root in sorted(parent.glob("*/src")):
            files.extend(sorted(src_root.rglob("*.py")))
    return files


def _import_targets(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            targets.add(node.module)
    return targets


def test_private_vcs_core_imports_stay_concentrated() -> None:
    """Keep private vcs-core imports confined to the known bridge files."""
    actual: dict[str, set[str]] = {}
    for path in _active_shepherd_python_files():
        private_imports = {
            target for target in _import_targets(path) if target == "vcs_core._" or target.startswith("vcs_core._")
        }
        if private_imports:
            actual[path.relative_to(REPO_ROOT).as_posix()] = private_imports

    assert actual == ALLOWED_PRIVATE_VCS_CORE_IMPORTS
