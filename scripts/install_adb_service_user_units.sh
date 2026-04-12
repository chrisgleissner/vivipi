#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_UNIT="$UNIT_DIR/vivipi-adb-service.service"
RECOVER_SERVICE_UNIT="$UNIT_DIR/vivipi-adb-recover.service"
RECOVER_TIMER_UNIT="$UNIT_DIR/vivipi-adb-recover.timer"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"

mkdir -p "$UNIT_DIR"

cat >"$SERVICE_UNIT" <<EOF
[Unit]
Description=ViviPi ADB-backed health service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_ROOT
ExecStart=$SCRIPT_DIR/run_adb_service.sh serve
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

cat >"$RECOVER_SERVICE_UNIT" <<EOF
[Unit]
Description=ViviPi ADB transport recovery

[Service]
Type=oneshot
WorkingDirectory=$REPO_ROOT
ExecStart=$SCRIPT_DIR/run_adb_service.sh ensure-adb
EOF

cat >"$RECOVER_TIMER_UNIT" <<EOF
[Unit]
Description=Periodically recover ViviPi ADB transport after boot or resume

[Timer]
OnBootSec=15s
OnUnitActiveSec=30s
AccuracySec=1s
Unit=vivipi-adb-recover.service

[Install]
WantedBy=timers.target
EOF

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Expected virtualenv Python at $PYTHON_BIN" >&2
    echo "Run ./build install first." >&2
    exit 2
fi

systemctl --user daemon-reload
systemctl --user enable --now vivipi-adb-service.service
systemctl --user enable --now vivipi-adb-recover.timer
systemctl --user start vivipi-adb-recover.service

echo "Installed user units:"
echo "  $SERVICE_UNIT"
echo "  $RECOVER_SERVICE_UNIT"
echo "  $RECOVER_TIMER_UNIT"
echo
echo "Current service status:"
systemctl --user --no-pager --full status vivipi-adb-service.service || true