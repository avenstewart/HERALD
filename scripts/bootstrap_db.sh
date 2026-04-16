#!/usr/bin/env bash
# Bootstrap databases for HERALD.
#
# Reads configuration from .env in the project root. Works identically against
# bundled docker-compose containers and externally-managed Postgres/TimescaleDB
# instances — just change *_HOST values in .env.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found. Copy .env.example to .env and fill in secrets:" >&2
    echo "    cp .env.example .env" >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: 'uv' not found on PATH. Install from https://docs.astral.sh/uv/" >&2
    exit 1
fi

echo "▶ Syncing Python dependencies (uv sync)..."
uv sync --quiet

echo "▶ Running bootstrap..."
uv run python scripts/bootstrap_db.py "$@"
