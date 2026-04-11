#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_CONFIG="${BUILD_CONFIG:-$ROOT_DIR/config/build-deploy.local.yaml}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-$ROOT_DIR/artifacts/vivipulse}"
DURATION="${DURATION:-30m}"
FIRMWARE_TRACE="${FIRMWARE_TRACE:-}"

args=(
  --build-config "$BUILD_CONFIG"
  --mode soak
  --duration "$DURATION"
  --parity-mode
  --artifacts-dir "$ARTIFACTS_DIR"
  --json
)

if [[ -n "$FIRMWARE_TRACE" ]]; then
  args+=(--firmware-trace "$FIRMWARE_TRACE")
fi

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
fi

OUTPUT_JSON="$("$ROOT_DIR/scripts/vivipulse" "${args[@]}")"
printf '%s\n' "$OUTPUT_JSON"

python3 - "$OUTPUT_JSON" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
outcome = payload.get("outcome") or {}
artifact_dir = payload.get("artifacts_dir", "-")
transport_failures = int(outcome.get("transport_failures", 0))
unexpected_exceptions = int(outcome.get("unexpected_exceptions", 0))
blocked_hosts = tuple(outcome.get("blocked_host_keys", ()))
parity = payload.get("parity")

failed = False
reasons = []
if transport_failures != 0:
    failed = True
    reasons.append(f"transport_failures={transport_failures}")
if unexpected_exceptions != 0:
    failed = True
    reasons.append(f"unexpected_exceptions={unexpected_exceptions}")
if blocked_hosts:
    failed = True
    reasons.append(f"blocked_hosts={','.join(blocked_hosts)}")
if parity is not None:
    if not parity.get("ordering_match", False):
        failed = True
        reasons.append("parity_ordering_mismatch")
    if not parity.get("lifecycle_match", False):
        failed = True
        reasons.append("parity_lifecycle_mismatch")
    if not parity.get("timing_within_tolerance", False):
        failed = True
        reasons.append("parity_timing_out_of_tolerance")

if failed:
    print(f"FAIL artifacts={artifact_dir} reasons={' '.join(reasons)}")
    raise SystemExit(1)

print(f"PASS artifacts={artifact_dir}")
PY