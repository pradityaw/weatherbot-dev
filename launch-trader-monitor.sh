#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

mkdir -p logs data/trader_research/live

MAX_LOG_BYTES=$((10 * 1024 * 1024))
OUT_LOG="$SCRIPT_DIR/logs/trader-monitor.out.log"
ERR_LOG="$SCRIPT_DIR/logs/trader-monitor.err.log"

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
rotate_if_large "$ERR_LOG"

if [ -f ".env.weatherbot" ]; then
  set -a
  source ".env.weatherbot"
  set +a
fi

if [ -f ".env.trader-monitor" ]; then
  set -a
  source ".env.trader-monitor"
  set +a
fi

# Foreground by default; pass --background to launch detached
if [[ "${1:-}" == "--background" ]]; then
  shift
  nohup "$SCRIPT_DIR/.venv/bin/python" -m trader_research.monitor "$@" \
    >>"$OUT_LOG" 2>>"$ERR_LOG" &
  echo "[trader-monitor] started pid=$! logs: $OUT_LOG"
  exit 0
fi

exec "$SCRIPT_DIR/.venv/bin/python" -m trader_research.monitor "$@"
