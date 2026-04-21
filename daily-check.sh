#!/bin/zsh
# Run once per day while paper-trading: status, counts, tail of log, optional full report.
#
# Optional — daily at 9am (Terminal must allow cron or use launchd instead):
#   0 9 * * * /Users/dubski/weatherbot/daily-check.sh >> /Users/dubski/weatherbot/logs/daily-check.log 2>&1
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== $(date) — weatherbot daily check ==="
echo

"$SCRIPT_DIR/status.sh"

echo
echo "--- last 25 lines of logs/weatherbot.out.log ---"
if [[ -f "$SCRIPT_DIR/logs/weatherbot.out.log" ]]; then
  tail -n 25 "$SCRIPT_DIR/logs/weatherbot.out.log"
else
  echo "(no log yet)"
fi

echo
echo "For full resolved breakdown run:"
echo "  cd $SCRIPT_DIR && .venv/bin/python bot_v2.py report"
