#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
LOG_DIR="$REPO_ROOT/artifacts/service"
LOG_FILE="$LOG_DIR/adb-service.log"
PATTERN="vivipi.services.adb_service --host 0.0.0.0 --port 8081"
MODE="${1:-start}"

mkdir -p "$LOG_DIR"

ensure_adb_transport() {
    if ! command -v adb >/dev/null 2>&1; then
        exit 0
    fi

    adb start-server >/dev/null 2>&1 || true
    adb reconnect offline >/dev/null 2>&1 || true
    adb devices -l >/dev/null 2>&1 || true
}

case "$MODE" in
    serve)
        exec "$PYTHON_BIN" -m vivipi.services.adb_service --host 0.0.0.0 --port 8081
        ;;
    ensure-adb)
        ensure_adb_transport
        ;;
    start)
        ensure_adb_transport
        if pgrep -f "$PATTERN" >/dev/null 2>&1; then
            exit 0
        fi
        nohup "$PYTHON_BIN" -m vivipi.services.adb_service --host 0.0.0.0 --port 8081 >>"$LOG_FILE" 2>&1 &
        ;;
    *)
        echo "Usage: $0 [start|serve|ensure-adb]" >&2
        exit 2
        ;;
esac