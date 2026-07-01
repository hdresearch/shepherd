"""Guard tests for scripts/scaffold-substrate.sh.

The out-of-tree mode is exercised end-to-end into a tmp dir (hermetic); the
in-tree mode writes to fixed paths under vcs-core core, so it is guarded at the
template level (string assertions on the script) rather than executed here.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from vcs_core._command_projection import project_cli_command
from vcs_core.cli import main
from vcs_core.spi import CommandRequest, DriverContext, SubstrateStoreIdentity

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
VCS_CORE_ROOT = PACKAGE_ROOT.parents[1]
SCAFFOLD = VCS_CORE_ROOT / "scripts" / "scaffold-substrate.sh"


def test_out_of_tree_scaffold_generates_a_parseable_plugin_package(tmp_path: Path) -> None:
    target = tmp_path / "memory-state-substrate"
    subprocess.run(
        ["bash", str(SCAFFOLD), "--out-of-tree", "memory-state", str(target)],
        check=True,
        capture_output=True,
        text=True,
    )

    # Every generated Python file parses.
    py_files = sorted(target.rglob("*.py"))
    assert py_files, "out-of-tree scaffold generated no Python files"
    for path in py_files:
        ast.parse(path.read_text(encoding="utf-8"))

    # The package is registered through the entry-point group.
    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    assert '[project.entry-points."vcscore.substrate_plugins"]' in pyproject
    assert "plugin:PLUGIN" in pyproject

    # The driver imports only the public SPI surface and self-checks at import.
    driver = (target / "src" / "memory_state_substrate" / "driver.py").read_text(encoding="utf-8")
    assert "from vcs_core.spi import" in driver
    assert "from vcs_core._" not in driver
    assert "@command(" in driver
    assert "dispatch_decorated_command" in driver
    assert 'ParamSpec(type="dict"' not in driver
    assert "assert isinstance(" in driver  # structural self-check idiom

    # The plugin and tests use the public surfaces the guide teaches.
    plugin = (target / "src" / "memory_state_substrate" / "plugin.py").read_text(encoding="utf-8")
    assert "from vcs_core.manifest import SubstrateManifest, SubstratePlugin" in plugin
    assert 'implementation_kind="driver"' in plugin
    tests = (target / "tests" / "test_driver.py").read_text(encoding="utf-8")
    assert "from vcs_core.spi.testing import" in tests
    assert "assert_substrate_driver_conformant" in tests


def test_out_of_tree_scaffold_projects_decorated_command_to_sub_help(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "memory-state-substrate"
    subprocess.run(
        ["bash", str(SCAFFOLD), "--out-of-tree", "memory-state", str(target)],
        check=True,
        capture_output=True,
        text=True,
    )
    monkeypatch.syspath_prepend(str(target / "src"))
    sys.modules.pop("memory_state_substrate.driver", None)
    sys.modules.pop("memory_state_substrate", None)

    from memory_state_substrate.driver import MemoryStateSubstrateDriver

    driver = MemoryStateSubstrateDriver()
    schema = driver.describe()
    projection = project_cli_command("memory_state", schema, "checkpoint")
    assert tuple(param.param.name for param in projection.params) == ("payload",)

    ctx = DriverContext(
        operation_id="op-test",
        binding="memory_state",
        role="shepherd.MemoryState",
        store_identity=SubstrateStoreIdentity(
            store_id="store_memory_state",
            kind="acme.memory_state",
            resource_id="memory_state:test",
        ),
    )
    result = driver.prepare(ctx, CommandRequest(command="checkpoint", params={"payload": {"x": 1}}))
    assert result.transitions[0].payload["x"] == 1

    class _Record:
        binding_name = "memory_state"
        implementation_kind = "driver"

    monkeypatch.setattr("vcs_core._cli_sub._load_binding_records", lambda: (_Record(),))
    monkeypatch.setattr("vcs_core._cli_sub._load_schema", lambda name: schema)
    monkeypatch.setattr("vcs_core._cli_schema.resolve_exec_schema", lambda name: schema)

    help_result = CliRunner().invoke(main, ["sub", "memory_state", "checkpoint", "--help"])

    assert help_result.exit_code == 0, help_result.output
    assert "--payload" in help_result.output
    assert "canonical memory-state payload" in help_result.output


def test_in_tree_template_teaches_the_public_surface() -> None:
    """The in-tree generator (which writes to fixed repo paths, so not executed
    here) must template the public surface, not the private module."""
    script = SCAFFOLD.read_text(encoding="utf-8")

    # The generated driver/test templates import the stable SPI home.
    assert "from vcs_core.spi import" in script
    # assert_never comes from typing, not the private SPI module (the old bug).
    assert "from typing import Any, Mapping, assert_never" in script
    assert "from vcs_core._substrate_driver import" not in script
    assert 'ParamSpec(type="dict"' not in script
    # The adapter template subclasses the generic role adapter.
    assert "RoleSubstrateAdapter" in script
    # The test template leads with the conformance kit.
    assert "from vcs_core.spi.testing import assert_substrate_driver_conformant" in script


def test_scaffold_rejects_bad_invocation() -> None:
    # No name → usage error (exit 1).
    result = subprocess.run(["bash", str(SCAFFOLD)], check=False, capture_output=True, text=True)
    assert result.returncode != 0
    assert "usage:" in result.stderr


@pytest.mark.parametrize("name", ["memory-state", "taskspec"])
def test_out_of_tree_scaffold_round_trips_for_varied_names(tmp_path: Path, name: str) -> None:
    target = tmp_path / f"{name}-pkg"
    subprocess.run(
        ["bash", str(SCAFFOLD), "--out-of-tree", name, str(target)],
        check=True,
        capture_output=True,
        text=True,
    )
    snake = name.replace("-", "_")
    driver = target / "src" / f"{snake}_substrate" / "driver.py"
    assert driver.is_file()
    ast.parse(driver.read_text(encoding="utf-8"))
