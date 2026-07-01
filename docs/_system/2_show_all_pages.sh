#!/usr/bin/env bash
# Step 2 of 6 — list every documentation page: its status, whether it is public, and what backs
# it. Read-only; changes nothing. Use it to see what you have and what will ship.
set -euo pipefail
cd "$(dirname "$0")"
exec uv run --no-project python run.py pages "$@"
