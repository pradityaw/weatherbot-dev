#!/bin/zsh
# One-shot daily review for Paper Trading Watch.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
if [[ -f ".env.weatherbot" ]]; then
  set -a
  source ".env.weatherbot"
  set +a
fi
exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/paper_watch_review.py" "$@"
