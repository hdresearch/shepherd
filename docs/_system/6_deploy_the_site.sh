#!/usr/bin/env bash
# Step 6 of 6 — run the full check and, ONLY if it is ALL GREEN, publish the public site to
# GitHub Pages (mkdocs gh-deploy pushes to the gh-pages branch). Needs git push access.
set -euo pipefail
cd "$(dirname "$0")"
exec uv run --no-project python run.py deploy
