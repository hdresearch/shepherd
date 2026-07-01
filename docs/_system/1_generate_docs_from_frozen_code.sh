#!/usr/bin/env bash
# Step 1 of 6 — regenerate the API/CLI reference from the (frozen) source code, then verify
# everything. Run this once the code is frozen, or any time the source changes. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"
exec uv run --no-project python run.py check --regen
