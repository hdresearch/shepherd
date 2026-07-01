"""Beta hardening for scaffolded out-of-tree substrate projection."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from vcs_core import discovery
from vcs_core.cli import main
from vcs_core.manifest import SubstrateManifest

from ...support.cli import init_repo

if TYPE_CHECKING:
    from collections.abc import Iterator


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
VCS_CORE_ROOT = PACKAGE_ROOT.parents[1]
SCAFFOLD = VCS_CORE_ROOT / "scripts" / "scaffold-substrate.sh"
GENERATED_SCHEMA = "acme/memory-state-revision/v1"


def _scaffold_memory_state(tmp_path: Path) -> Path:
    target = tmp_path / "memory-state-substrate"
    subprocess.run(
        ["bash", str(SCAFFOLD), "--out-of-tree", "memory-state", str(target)],
        check=True,
        capture_output=True,
        text=True,
    )
    return target


def _configured_repo(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    runner = CliRunner()
    init_repo(runner, workspace)
    (workspace / "vcscore.toml").write_text(
        '[bindings.memory_state]\ntype = "acme.memory_state"\n',
        encoding="utf-8",
    )
    return workspace


def _subprocess_env(*, uv_cache_dir: Path, pythonpath: tuple[Path, ...] = ()) -> dict[str, str]:
    env = dict(os.environ)
    env["UV_CACHE_DIR"] = str(uv_cache_dir)
    if pythonpath:
        env["PYTHONPATH"] = os.pathsep.join(str(path) for path in pythonpath)
    else:
        env.pop("PYTHONPATH", None)
    return env


def _install_scaffolded_package(target: Path, site_packages: Path, *, uv_cache_dir: Path) -> None:
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--target",
            str(site_packages),
            "--no-deps",
            "--no-build-isolation",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_subprocess_env(uv_cache_dir=uv_cache_dir),
    )


@contextmanager
def _chdir(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _assert_sub_exec_parity() -> None:
    runner = CliRunner()
    help_result = runner.invoke(main, ["sub", "memory_state", "checkpoint", "--help"])
    assert help_result.exit_code == 0, help_result.output
    assert "--payload" in help_result.output
    assert "canonical memory-state payload" in help_result.output

    sub_result = runner.invoke(
        main,
        ["sub", "memory_state", "checkpoint", "--json", "--payload", '{"x":1}'],
    )
    raw_result = runner.invoke(
        main,
        ["exec", "memory_state", "checkpoint", "--json", "-p", 'payload={"x":1}'],
    )

    assert sub_result.exit_code == 0, sub_result.output
    assert raw_result.exit_code == 0, raw_result.output
    sub_payload = json.loads(sub_result.output)
    raw_payload = json.loads(raw_result.output)
    assert sorted(sub_payload) == ["oids", "value"]
    assert sorted(raw_payload) == ["oids", "value"]
    assert sub_payload["value"] == raw_payload["value"]
    transition = sub_payload["value"]["transitions"][0]
    assert transition["payload"] == {"schema": GENERATED_SCHEMA, "x": 1}


def _assert_installed_scaffold_kit_passes(target: Path, site_packages: Path, *, tmp_path: Path) -> None:
    uv_cache_dir = tmp_path / "uv-cache"
    pythonpath = (site_packages, PACKAGE_ROOT / "src")
    env = _subprocess_env(uv_cache_dir=uv_cache_dir, pythonpath=pythonpath)

    provenance_probe = textwrap.dedent(
        f"""
        from __future__ import annotations

        from pathlib import Path

        import memory_state_substrate

        module_path = Path(memory_state_substrate.__file__).resolve()
        site_packages = Path({str(site_packages)!r}).resolve()
        assert site_packages in module_path.parents, (
            f"memory_state_substrate imported from {{module_path}}, not installed target {{site_packages}}"
        )
        """
    )
    probe_result = subprocess.run(
        [sys.executable, "-c", provenance_probe],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=PACKAGE_ROOT,
    )
    assert probe_result.returncode == 0, probe_result.stdout + probe_result.stderr

    pytest_config = tmp_path / "empty-pytest.ini"
    pytest_config.write_text("[pytest]\n", encoding="utf-8")
    kit_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            str(pytest_config),
            str(target / "tests"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=PACKAGE_ROOT,
    )
    assert kit_result.returncode == 0, kit_result.stdout + kit_result.stderr


def _installed_entry_point_probe(workspace: Path) -> str:
    return textwrap.dedent(
        f"""
        from __future__ import annotations

        import json
        import os
        from pathlib import Path

        from click.testing import CliRunner
        from vcs_core.cli import main
        from vcs_core.discovery import discover_plugin_registrations

        GENERATED_SCHEMA = {GENERATED_SCHEMA!r}


        def _assert_sub_exec_parity() -> None:
            runner = CliRunner()
            help_result = runner.invoke(main, ["sub", "memory_state", "checkpoint", "--help"])
            assert help_result.exit_code == 0, help_result.output
            assert "--payload" in help_result.output
            assert "canonical memory-state payload" in help_result.output

            sub_result = runner.invoke(
                main,
                ["sub", "memory_state", "checkpoint", "--json", "--payload", '{{"x":1}}'],
            )
            raw_result = runner.invoke(
                main,
                ["exec", "memory_state", "checkpoint", "--json", "-p", 'payload={{"x":1}}'],
            )

            assert sub_result.exit_code == 0, sub_result.output
            assert raw_result.exit_code == 0, raw_result.output
            sub_payload = json.loads(sub_result.output)
            raw_payload = json.loads(raw_result.output)
            assert sorted(sub_payload) == ["oids", "value"]
            assert sorted(raw_payload) == ["oids", "value"]
            assert sub_payload["value"] == raw_payload["value"]
            transition = sub_payload["value"]["transitions"][0]
            assert transition["payload"] == {{"schema": GENERATED_SCHEMA, "x": 1}}


        registrations = discover_plugin_registrations()
        registration = registrations["acme.memory_state"]
        assert registration.module_name == "memory_state_substrate.driver"
        assert registration.implementation_kind == "driver"

        previous = Path.cwd()
        os.chdir({str(workspace)!r})
        try:
            _assert_sub_exec_parity()
        finally:
            os.chdir(previous)
        """
    )


def test_scaffolded_substrate_projects_and_matches_raw_exec_with_patched_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _scaffold_memory_state(tmp_path)
    workspace = _configured_repo(tmp_path)
    monkeypatch.syspath_prepend(str(target / "src"))
    sys.modules.pop("memory_state_substrate.driver", None)
    sys.modules.pop("memory_state_substrate", None)

    real_discover = discovery.discover_plugin_registrations

    def patched_discover(*, strict: bool = True) -> dict[str, discovery.DiscoveredSubstrate]:
        available = dict(real_discover(strict=strict))
        available["acme.memory_state"] = discovery.DiscoveredSubstrate(
            name="acme.memory_state",
            module_name="memory_state_substrate.driver",
            class_name="MemoryStateSubstrateDriver",
            source="plugin",
            manifest=SubstrateManifest(name="acme.memory_state"),
            entry_point_name="acme.memory_state",
            implementation_kind="driver",
        )
        return available

    monkeypatch.setattr(discovery, "discover_plugin_registrations", patched_discover)

    with _chdir(workspace):
        _assert_sub_exec_parity()


@pytest.mark.integration
@pytest.mark.slow
def test_scaffolded_substrate_installed_entry_point_projects_and_matches_raw_exec(tmp_path: Path) -> None:
    target = _scaffold_memory_state(tmp_path)
    site_packages = tmp_path / "installed-site"
    uv_cache_dir = tmp_path / "uv-cache"
    workspace = _configured_repo(tmp_path)

    _install_scaffolded_package(target, site_packages, uv_cache_dir=uv_cache_dir)
    _assert_installed_scaffold_kit_passes(target, site_packages, tmp_path=tmp_path)

    result = subprocess.run(
        [sys.executable, "-c", _installed_entry_point_probe(workspace)],
        check=False,
        capture_output=True,
        text=True,
        env=_subprocess_env(uv_cache_dir=uv_cache_dir, pythonpath=(site_packages, PACKAGE_ROOT / "src")),
        cwd=PACKAGE_ROOT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
