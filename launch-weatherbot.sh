#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

mkdir -p logs data/markets

# Rotate main log if it grows past 10 MiB (no sudo; keeps last 5 rotations)
MAX_LOG_BYTES=$((10 * 1024 * 1024))
OUT_LOG="$SCRIPT_DIR/logs/weatherbot.out.log"
rotate_if_large() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  local sz
  sz=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)
  if (( sz > MAX_LOG_BYTES )); then
    for i in 4 3 2 1; do
      [[ -f "${f}.$i" ]] && mv "${f}.$i" "${f}.$((i + 1))"
    done
    mv "$f" "${f}.1"
    : >"$f"
  fi
}
rotate_if_large "$OUT_LOG"
rotate_if_large "$SCRIPT_DIR/logs/weatherbot.err.log"

if [ -f ".env.weatherbot" ]; then
  set -a
  source ".env.weatherbot"
  set +a
fi

exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/bot_v2.py" run
