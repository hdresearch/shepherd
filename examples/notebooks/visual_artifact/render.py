"""Command-line entry point for rendering visual-artifact HTML previews."""

# ruff: noqa: INP001

from shepherd_usecases.visual_artifact.render import *  # noqa: F403
from shepherd_usecases.visual_artifact.render import main as _main

if __name__ == "__main__":
    raise SystemExit(_main())
