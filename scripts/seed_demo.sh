#!/usr/bin/env bash
# Load (or top up) demo accounts and data. Safe to run repeatedly.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
PYTHONPATH=. "$ROOT/.venv/bin/python" "$ROOT/scripts/seed_demo.py"
