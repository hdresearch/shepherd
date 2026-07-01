from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "shepherd2"


def _module_name(path: Path) -> tuple[str, bool]:
    relative = path.relative_to(SRC).with_suffix("")
    parts = ["shepherd2", *relative.parts]
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _import_targets(path: Path) -> set[str]:
    module_name, is_package = _module_name(path)
    tree = ast.parse(path.read_text())
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            targets.add(_resolve_import_from(module_name, is_package, node.level, node.module))
    return {target for target in targets if target}


def _resolve_import_from(current_module: str, is_package: bool, level: int, module: str | None) -> str:
    if level == 0:
        return module or ""
    package_parts = current_module.split(".") if is_package else current_module.split(".")[:-1]
    if level > len(package_parts):
        return module or ""
    base = package_parts[: len(package_parts) - level + 1]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _targets_under(package: str) -> set[str]:
    targets: set[str] = set()
    for path in (SRC / package).rglob("*.py"):
        targets.update(_import_targets(path))
    return targets


def _assert_no_import_prefix(targets: set[str], *prefixes: str) -> None:
    bad = sorted(
        target for target in targets for prefix in prefixes if target == prefix or target.startswith(f"{prefix}.")
    )
    assert bad == []


def test_kernel_ring_does_not_import_outer_rings() -> None:
    _assert_no_import_prefix(
        _targets_under("kernel"),
        "shepherd2.schemas",
        "shepherd2.runtime",
        "shepherd2.vnext",
        "shepherd2.trace_store",
    )


def test_schema_ring_does_not_import_runtime_or_vnext() -> None:
    _assert_no_import_prefix(
        _targets_under("schemas"),
        "shepherd2.runtime",
        "shepherd2.vnext",
    )


def test_vnext_substrate_protocols_do_not_import_runtime() -> None:
    _assert_no_import_prefix(
        _targets_under("vnext"),
        "shepherd2.runtime",
    )


def test_trace_store_does_not_import_vnext() -> None:
    _assert_no_import_prefix(
        _import_targets(SRC / "trace_store.py"),
        "shepherd2.vnext",
    )


def test_run_output_schema_import_does_not_load_vcs_core() -> None:
    code = (
        "import sys\n"
        "import shepherd2.schemas.run_outputs\n"
        "print('\\n'.join(sorted(m for m in sys.modules if m == 'vcs_core' or m.startswith('vcs_core.'))))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert proc.stdout == "\n"


def test_kernel_facts_import_does_not_load_runtime_rings() -> None:
    """The retention-facade guard: a kernel-ring import must be runtime-free.

    The AST checks above prove the kernel ring's *modules* import nothing outer; this proves
    the *package* delivers that isolation at import time — `import shepherd2.kernel.facts`
    executes the parent package's `__init__`, where the outer rings (runtime/vnext/trace_store)
    are lazy exports (landed 2026-06-10; see trace-identity-dual-domain.md §6).
    Runs in a fresh interpreter because this suite has already imported shepherd2.
    """
    code = (
        "import sys\n"
        "import shepherd2.kernel.facts\n"
        "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('shepherd2'))))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded = set(proc.stdout.split())
    forbidden = sorted(
        m for m in loaded if m.startswith(("shepherd2.runtime", "shepherd2.vnext", "shepherd2.trace_store"))
    )
    assert forbidden == [], f"kernel-ring import transitively loaded outer rings: {forbidden}"


def test_lazy_submodule_attribute_access_preserved() -> None:
    """The eager imports used to bind the outer rings as package attributes
    (`import shepherd2; shepherd2.runtime`); the lazy facade keeps that surface,
    resolving the submodule on first attribute access instead of at import.
    Runs in a fresh interpreter because this suite has already imported shepherd2.
    """
    code = "import shepherd2\nprint(shepherd2.runtime.__name__, shepherd2.vnext.__name__, shepherd2.trace_store.__name__)\n"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert proc.stdout.split() == ["shepherd2.runtime", "shepherd2.vnext", "shepherd2.trace_store"]
