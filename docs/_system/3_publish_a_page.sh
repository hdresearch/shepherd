#!/usr/bin/env bash
# Step 3 of 6 — publish a page you have written onto the PUBLIC site. Gated: it refuses any page
# that is not release-ready and prints exactly what is missing, so a draft can never leak.
# Usage:  ./3_publish_a_page.sh <page>      e.g.  ./3_publish_a_page.sh concepts/runs.md
set -euo pipefail
cd "$(dirname "$0")"
if [ "$#" -eq 0 ]; then
  echo "Usage: $(basename "$0") <page>      e.g. $(basename "$0") concepts/runs.md"
  echo "Run ./2_show_all_pages.sh to see every page and whether it is public."
  exit 2
fi
exec uv run --no-project python run.py promote "$@"
