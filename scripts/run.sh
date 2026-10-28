#!/usr/bin/env bash
# Start AgriTrust AI (seeds demo data on first run).
#
#   ./scripts/run.sh                     localhost only, http://127.0.0.1:8000
#   HOST=0.0.0.0 ./scripts/run.sh        reachable from your phone / other devices
#   PORT=8001 ./scripts/run.sh           a different port
#
# 0.0.0.0 means "every network interface", so the same build is also served at
# http://<your-lan-ip>:8000 - handy for testing on a real phone.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

# HOST/PORT may live in .env, so the address is configured in one place.
# An explicit environment variable always wins:  HOST=0.0.0.0 ./scripts/run.sh
[ -n "${HOST:-}" ] || HOST=""
[ -n "${PORT:-}" ] || PORT=""
if [ -f "$ROOT/.env" ]; then
  if [ -z "$HOST" ]; then
    HOST="$(grep -E '^HOST=' "$ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"'[:space:]' || true)"
  fi
  if [ -z "$PORT" ]; then
    PORT="$(grep -E '^PORT=' "$ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"'[:space:]' || true)"
  fi
fi
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
PYTHON="$ROOT/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "The virtual environment is missing. Create it first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [ ! -f agritrust.db ]; then
  PYTHONPATH=. "$PYTHON" "$ROOT/scripts/seed_demo.py"
fi

# A second copy of the server cannot bind the same port, and the raw
# "address already in use" from uvicorn is easy to miss. Say what to do instead.
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${PORT}\b"; then
  echo "Port ${PORT} is already in use, so the server cannot start." >&2
  echo "Either open http://${HOST}:${PORT} (it may already be running)," >&2
  echo "or stop the other copy and try again:" >&2
  echo "  pkill -f '[u]vicorn app.main'" >&2
  echo "or use a different port:" >&2
  echo "  PORT=8001 ./scripts/run.sh" >&2
  exit 1
fi

# tell the farmer the address that will actually work in a browser
if [ "$HOST" = "0.0.0.0" ]; then
  LAN_IP="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' | grep -v '^127\.' | head -1 || true)"
  echo "AgriTrust AI starting. Open it at:"
  echo "  http://127.0.0.1:${PORT}          (this computer)"
  [ -n "$LAN_IP" ] && echo "  http://${LAN_IP}:${PORT}          (your phone / other devices on this wifi)"
else
  echo "AgriTrust AI starting on http://${HOST}:${PORT}"
  echo "  (use HOST=0.0.0.0 to reach it from a phone or another device)"
fi

exec env PYTHONPATH=. "$ROOT/.venv/bin/uvicorn" app.main:app --reload --host "$HOST" --port "$PORT"
