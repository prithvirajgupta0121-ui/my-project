#!/usr/bin/env bash
# Run backend (pytest) and frontend (vitest) test suites.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
(cd "$ROOT/backend" && python -m pytest -q)
(cd "$ROOT/frontend" && npx tsc -p . --noEmit && npm test)
