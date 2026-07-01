"""Shared CLI rendering for expected application errors."""

from __future__ import annotations

import sys
from typing import NoReturn

import click


def prefixed_error_message(message: str) -> str:
    """Return a CLI error message with exactly one leading ``Error:`` prefix."""
    if message.startswith("Error:"):
        return message
    return f"Error: {message}"


def emit_error_message(message: str, *, err: bool = False) -> None:
    """Print one expected-error message without double-prefixing app-rendered text."""
    click.echo(prefixed_error_message(message), err=err)


def exit_app_error(exc: Exception) -> NoReturn:
    """Exit for expected app-layer failures; re-raise unexpected bugs."""
    from vcs_core._app import AppError, render_app_error

    if not isinstance(exc, AppError):
        raise exc
    exit_code, lines = render_app_error(exc)
    for line in lines:
        click.echo(line)
    sys.exit(exit_code)
