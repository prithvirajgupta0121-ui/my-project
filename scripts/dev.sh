#!/usr/bin/env bash
# LOCAL DEVELOPMENT: FastAPI on :8000 (auto-reload) + Vite dev server on :5173 (proxying /api and /ws)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
[ -f "$ROOT/.env" ] && set -a && . "$ROOT/.env" && set +a
export AEGIS_DATA_DIR="${AEGIS_DATA_DIR:-$ROOT/data}"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload &
API=$!
trap 'kill $API' EXIT
cd "$ROOT/frontend" && npm run dev
