"""Regression checks for the documented package-local Makefile surface."""

from __future__ import annotations

import re
from pathlib import Path


def _makefile_text() -> str:
    return (Path(__file__).resolve().parents[2] / "Makefile").read_text()


def _target_block(text: str, target: str) -> str:
    match = re.search(rf"^{re.escape(target)}:\n(?:^\t.*\n?)*", text, re.MULTILINE)
    assert match is not None, f"expected Makefile target {target!r}"
    return match.group(0)


def test_smoke_target_is_the_dedicated_cli_smoke() -> None:
    text = _makefile_text()
    smoke_target = _target_block(text, "smoke")

    assert ".PHONY:" in text
    assert " smoke " in f" {text.splitlines()[0]} "
    assert "tests/integration/cli/test_smoke.py" in smoke_target
    assert "--disable-socket" in smoke_target


def test_guide_check_target_validates_the_store_first_guide() -> None:
    text = _makefile_text()
    guide_check_target = _target_block(text, "guide_check")

    assert " guide_check " in f" {text.splitlines()[0]} "
    assert "tests/integration/test_store_first_guide.py" in guide_check_target
    assert "tests/unit/test_docs_contract.py" in guide_check_target
    assert "--disable-socket" in guide_check_target
    assert "# Validate the endorsed store-first guide against the public package API." in text


def test_test_unit_target_remains_the_broad_non_container_selector() -> None:
    text = _makefile_text()
    test_unit_target = _target_block(text, "test_unit")

    assert "pytest tests/" in test_unit_target
    assert '-m "not container and not loopback"' in test_unit_target
    assert "--disable-socket" in test_unit_target
    assert "--allow-unix-socket" in test_unit_target
    assert "# Run the broader non-container package test target with network sockets disabled." in text
    assert "# Run unit tests only (no network, no container)" not in text


def test_loopback_target_runs_local_tcp_tests_without_socket_blocking() -> None:
    text = _makefile_text()
    loopback_target = _target_block(text, "test_loopback")

    assert " test_loopback " in f" {text.splitlines()[0]} "
    assert "pytest tests/" in loopback_target
    assert '-m "loopback and not container"' in loopback_target
    assert "--disable-socket" not in loopback_target
    assert "# Run loopback-local broker tests that intentionally open TCP sockets." in text


def test_test_installed_target_remains_the_explicit_installed_mode_smoke() -> None:
    text = _makefile_text()
    test_installed_target = _target_block(text, "test_installed")

    assert "tests/integration/cli/test_installed_cli.py" in test_installed_target
    assert "# Run the explicit installed-mode CLI smoke contract." in text


def test_test_container_target_mounts_repo_root_and_syncs_project_explicitly() -> None:
    text = _makefile_text()
    test_container_target = _target_block(text, "test_container")

    assert "REPO_ROOT := ../../.." in text
    assert "podman build -t $(CONTAINER_IMAGE) -f containers/Containerfile $(REPO_ROOT)" in test_container_target
    assert "-v $$(cd $(REPO_ROOT) && pwd):/workspace \\" in test_container_target
    assert "UV_PROJECT_ENVIRONMENT=/tmp/vcs-core-core-venv" in test_container_target
    assert "uv sync --project vcs-core/packages/core --all-extras --group test --quiet" in test_container_target
    assert (
        "uv run --project vcs-core/packages/core pytest vcs-core/packages/core/tests/ -m container -v"
        in test_container_target
    )


def test_podman_targets_route_through_the_shared_harness() -> None:
    text = _makefile_text()
    podman_up_target = _target_block(text, "podman_up")
    podman_exec_target = _target_block(text, "podman_exec")
    podman_exec_script_target = _target_block(text, "podman_exec_script")
    podman_demo_target = _target_block(text, "podman_demo")
    podman_capture_target = _target_block(text, "podman_capture_smoke")
    podman_shell_capture_target = _target_block(text, "podman_shell_capture_smoke")

    assert "podman_up" in text.splitlines()[0]
    assert "podman_exec_script" in text.splitlines()[0]
    assert "PODMAN_SCRIPT := ../../../scripts/vcs-core-podman.sh" in text
    assert "PODMAN_RUN_ENV =" in text
    assert "$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) up" in podman_up_target
    assert "$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) exec -- bash -c" in podman_exec_target
    assert "CMD='...'" in podman_exec_target
    assert "$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) exec -- bash" in podman_exec_script_target
    assert "SCRIPT=..." in podman_exec_script_target
    assert "RUN_NAME" in text
    assert "KEEP_RUN" in text
    assert "$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) demo" in podman_demo_target
    assert "$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) capture-smoke" in podman_capture_target
    assert "$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) shell-capture-smoke" in podman_shell_capture_target
