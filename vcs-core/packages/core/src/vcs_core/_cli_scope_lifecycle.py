"""Helpers for scope lifecycle CLI commands."""

from __future__ import annotations

import sys
from typing import Any

import click

from vcs_core import _cli_delegation
from vcs_core._admission.identifiers import ParseError, ScopeName, parse_optional_scope_name
from vcs_core._cli_errors import exit_app_error


def _exit_app_error(exc: Exception) -> None:
    exit_app_error(exc)


def run_branch(*, name: str, parent: str | None, isolated: bool) -> None:
    """Run the `branch` CLI flow with session-aware delegation."""
    try:
        name = str(ScopeName.parse(name, allow_ground=False))
        parent_name = parse_optional_scope_name(parent) or "ground"
    except ParseError as exc:
        click.echo(f"Error: cannot branch: {exc}")
        sys.exit(2)

    def _render_branch_result(result: dict[str, Any]) -> None:
        click.echo(f"Created scope '{name}' from '{parent_name}'")
        if result.get("isolated") and result.get("mount_path"):
            click.echo(f"  Overlay: {result['mount_path']}")

    def _fallback() -> None:
        from vcs_core._app import AppOpenMode, VcsCoreApp

        try:
            with VcsCoreApp.open_existing(".", mode=AppOpenMode.CONTROL) as app:
                app.branch(name=name, parent=parent_name, isolated=isolated)
        except Exception as exc:  # noqa: BLE001
            _exit_app_error(exc)
        click.echo(f"Created scope '{name}' from '{parent_name}'")

    _cli_delegation.with_session_result(
        "fork",
        {"name": name, "parent": parent_name, "isolated": isolated},
        on_result=_render_branch_result,
        on_fallback=_fallback,
    )


def run_merge(*, name: str) -> None:
    """Run the `merge` CLI flow with session-aware delegation."""
    try:
        name = str(ScopeName.parse(name))
    except ParseError as exc:
        click.echo(f"Error: cannot merge: {exc}")
        sys.exit(2)

    def _render_merge_result(result: dict[str, Any]) -> None:
        click.echo(f"Merged '{result.get('merged', name)}' into '{result.get('into', 'ground')}'")

    def _fallback() -> None:
        from vcs_core._app import AppOpenMode, VcsCoreApp

        try:
            with VcsCoreApp.open_existing(".", mode=AppOpenMode.CONTROL) as app:
                result = app.merge(name=name)
        except Exception as exc:  # noqa: BLE001
            _exit_app_error(exc)
        click.echo(f"Merged '{result.merged}' into '{result.into}'")

    _cli_delegation.with_session_result(
        "merge",
        {"name": name},
        on_result=_render_merge_result,
        on_fallback=_fallback,
    )


def run_discard(*, name: str) -> None:
    """Run the `discard` CLI flow with session-aware delegation."""
    try:
        name = str(ScopeName.parse(name))
    except ParseError as exc:
        click.echo(f"Error: cannot discard: {exc}")
        sys.exit(2)

    def _render_discard_result(_result: dict[str, Any]) -> None:
        click.echo(f"Discarded '{name}'")

    def _fallback() -> None:
        from vcs_core._app import AppOpenMode, VcsCoreApp

        try:
            with VcsCoreApp.open_existing(".", mode=AppOpenMode.CONTROL) as app:
                result = app.discard(name=name)
        except Exception as exc:  # noqa: BLE001
            _exit_app_error(exc)
        click.echo(f"Discarded '{result.discarded}'")

    _cli_delegation.with_session_result(
        "discard",
        {"name": name},
        on_result=_render_discard_result,
        on_fallback=_fallback,
    )
