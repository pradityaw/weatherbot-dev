from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LiveLedger:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.trades_path = self.root / "live_trades.jsonl"
        self.signals_path = self.root / "live_signals.jsonl"
        self.events_path = self.root / "live_events.jsonl"

    @staticmethod
    def _stamp(payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        body.setdefault("ts", datetime.now(timezone.utc).isoformat())
        return body

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
        line = json.dumps(self._stamp(payload), ensure_ascii=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def log_signal(self, payload: dict[str, Any]) -> None:
        self._append(self.signals_path, payload)

    def log_trade(self, payload: dict[str, Any]) -> None:
        self._append(self.trades_path, payload)

    def log_event(self, payload: dict[str, Any]) -> None:
        self._append(self.events_path, payload)
