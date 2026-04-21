#!/bin/zsh
# Quick snapshot: paper status + JSON-derived counts (no jq required).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  echo "Missing venv — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.lock.txt"
  exit 1
fi

"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/bot_v2.py" status

"$SCRIPT_DIR/.venv/bin/python" <<'PY'
import json
from pathlib import Path

root = Path.cwd()
markets_dir = root / "data" / "markets"
cal_file = root / "data" / "calibration.json"

open_n = resolved_n = 0
if markets_dir.is_dir():
    for p in markets_dir.glob("*.json"):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        pos = m.get("position") or {}
        if pos.get("status") == "open":
            open_n += 1
        if m.get("status") == "resolved":
            resolved_n += 1

cal_keys = 0
if cal_file.is_file():
    try:
        cal = json.loads(cal_file.read_text(encoding="utf-8"))
        cal_keys = len(cal) if isinstance(cal, dict) else 0
    except Exception:
        cal_keys = 0

print()
print("--- counts (from data/) ---")
print(f"  JSON markets with open position:  {open_n}")
print(f"  JSON markets resolved:            {resolved_n}")
print(f"  calibration.json entries:        {cal_keys}")
PY
