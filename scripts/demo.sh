#!/usr/bin/env bash
# OFFLINE DEMO: build the UI once, then serve UI + API from one local process on :8000.
# No internet is required after dependencies are installed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ ! -d "$ROOT/frontend/dist" ]; then (cd "$ROOT/frontend" && npm run build); fi
cd "$ROOT/backend"
export AEGIS_DATA_DIR="${AEGIS_DATA_DIR:-$ROOT/data-demo}" AEGIS_DEMO_MODE_ENABLED=true AEGIS_ONLINE_TILES_ENABLED=false
echo "AEGIS offline demo → http://127.0.0.1:8000  (click ENTER DEMO MODE)"
exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
