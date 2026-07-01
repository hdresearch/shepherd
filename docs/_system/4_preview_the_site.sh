#!/usr/bin/env bash
# Step 4 of 6 — preview the PUBLIC site locally at http://localhost:8000 (Ctrl-C to stop).
# This is the "look at it" command; it serves exactly what will ship.
set -euo pipefail
cd "$(dirname "$0")"
exec uv run --no-project python run.py preview
