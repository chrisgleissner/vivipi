#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
LOG_DIR="$REPO_ROOT/artifacts/service"
LOG_FILE="$LOG_DIR/adb-service.log"
PATTERN="vivipi.services.adb_service --host 0.0.0.0 --port 8081"

mkdir -p "$LOG_DIR"

if pgrep -f "$PATTERN" >/dev/null 2>&1; then
    exit 0
fi

nohup "$PYTHON_BIN" -m vivipi.services.adb_service --host 0.0.0.0 --port 8081 >>"$LOG_FILE" 2>&1 &