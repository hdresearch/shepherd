#!/usr/bin/env bash
# Step 5 of 6 — the "is it safe to ship?" gate. Run it after any change. "ALL GREEN" means good
# to deploy; anything red stops and names the one thing to fix (and the command to fix it).
set -euo pipefail
cd "$(dirname "$0")"
exec uv run --no-project python run.py check
