"""Installed-wheel CLI smoke coverage."""

from __future__ import annotations

from pathlib import Path

from ...support.install import (
    assert_installed_provenance,
    assert_installed_public_modules,
    build_commons_vcs_wheel,
    build_wheel,
    create_installed_env,
    install_wheel,
    run_installed_cli,
    wheel_members,
)
from ...support.public_surface import PUBLIC_LOOKING_TOP_LEVEL_MODULES


def test_installed_wheel_cli_happy_path(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    commons_vcs_wheel = build_commons_vcs_wheel(dist_dir)
    wheel = build_wheel(dist_dir)
    assert "vcs_core/_native/fs_capture_shim.c" in wheel_members(wheel)
    env = create_installed_env(tmp_path / "venv")
    install_wheel(env, commons_vcs_wheel, wheel)
    assert_installed_provenance(env)
    assert_installed_public_modules(env, PUBLIC_LOOKING_TOP_LEVEL_MODULES)

    help_result = run_installed_cli(env, ["--help"])
    assert "vcs-core" in help_result.stdout

    demo_root = tmp_path / "demo-world"
    demo_root.mkdir()
    run_installed_cli(env, ["init", str(demo_root)])
    run_installed_cli(env, ["activate", "."], cwd=demo_root)
    run_installed_cli(env, ["branch", "task-1"], cwd=demo_root)
    payload = tmp_path / "hello.payload"
    payload.write_text("hello")
    run_installed_cli(
        env,
        ["exec", "filesystem", "write", "--scope", "task-1", "-p", "path=hello.txt", "-p", f"content=@{payload}"],
        cwd=demo_root,
    )
    run_installed_cli(env, ["merge", "task-1"], cwd=demo_root)
    run_installed_cli(env, ["push"], cwd=demo_root)

    checkout_dest = tmp_path / "installed-snap"
    checkout_result = run_installed_cli(env, ["checkout", "ground", "--dest", str(checkout_dest)], cwd=demo_root)
    assert "Extracted" in checkout_result.stdout
    assert (checkout_dest / "hello.txt").read_text() == "hello"

    status = run_installed_cli(env, ["status"], cwd=demo_root)
    assert "Commits ahead: 0" in status.stdout
