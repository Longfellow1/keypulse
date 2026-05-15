#!/usr/bin/env bash
set -euo pipefail

APP_BIN="/Applications/KeyPulse.app/Contents/MacOS/KeyPulse"
if [[ ! -x "$APP_BIN" ]]; then
  echo "missing app binary: $APP_BIN" >&2
  exit 1
fi

TARGET_DATE="${1:-$(date -v-1d +%F)}"

env -i PATH=/usr/bin:/bin HOME="$HOME" "$APP_BIN" daily run --date "$TARGET_DATE"

RECORD_PATH="$HOME/.keypulse/run_records/${TARGET_DATE}.json"
if [[ ! -f "$RECORD_PATH" ]]; then
  echo "missing run record: $RECORD_PATH" >&2
  exit 1
fi

python3 - "$RECORD_PATH" <<'PY'
import json
import sys
from pathlib import Path

record_path = Path(sys.argv[1])
payload = json.loads(record_path.read_text(encoding="utf-8"))

stage_status = payload.get("stage_status")
if not isinstance(stage_status, dict) or not stage_status:
    raise SystemExit("stage_status missing")
if any(str(value) != "ok" for value in stage_status.values()):
    raise SystemExit(f"stage_status not all ok: {stage_status}")

artifact_paths = payload.get("artifact_paths") if isinstance(payload.get("artifact_paths"), dict) else {}
summary_path = Path(str(artifact_paths.get("daily_summary_json") or "")).expanduser()
if not summary_path.exists():
    raise SystemExit(f"daily-summary missing: {summary_path}")
if summary_path.stat().st_size <= 1000:
    raise SystemExit(f"daily-summary too small: {summary_path.stat().st_size}")
PY
