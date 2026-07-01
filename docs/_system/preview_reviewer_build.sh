#!/usr/bin/env bash
# Extra (reviewers only, NOT a ship step) — preview the FULL internal review build at
# http://localhost:8001, including scaffold/draft pages. This build is never deployed.
set -euo pipefail
cd "$(dirname "$0")"
exec uv run --no-project python run.py preview-internal
