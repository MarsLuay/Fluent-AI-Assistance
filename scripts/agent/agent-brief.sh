#!/usr/bin/env bash
# Thin wrapper: ./scripts/agent/agent-brief.sh --mode new-script
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/agent/agent-brief.py" "$@"
fi
exec python3 "$ROOT/scripts/agent/agent-brief.py" "$@"
