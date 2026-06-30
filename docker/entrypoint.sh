#!/bin/bash
set -e

# Runtime-computed var (not baked into the image ENV block).
# CF sets $PORT dynamically; standalone Docker/OCI leaves it unset so we fall back to 8765.
export SERVER_PORT="${SERVER_PORT:-${PORT:-8765}}"

exec /app/.venv/bin/python /app/server.py
