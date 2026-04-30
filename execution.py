from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clob_market import get_clob_market_snapshot
from live_ledger import LiveLedger
from signals import ExitSignal, TradeSignal


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except Exception:
        return default


@dataclass(slots=True)
class ExecutionConfig:
    dry_run_live: bool = True
    live_trading_enabled: bool = False
    max_live_usdc: float = 100.0
    per_trade_usdc_cap: float = 2.0
    max_open_exposure_usdc: float = 25.0
    max_daily_loss_usdc: float = 15.0
    max_new_trades_per_day: int = 5
    max_price: float = 0.45
    max_slippage: float = 0.03
    min_top_ask_size: float = 1.0
    kill_switch_file: str = "data/pause_live"

    @classmethod
    def from_env(cls) -> "ExecutionConfig":
        return cls(
            dry_run_live=_env_bool("WEATHERBOT_DRY_RUN_LIVE", True),
            live_trading_enabled=_env_bool("WEATHERBOT_LIVE_TRADING", False),
            max_live_usdc=_env_float("WEATHERBOT_MAX_LIVE_USDC", 100.0),
            per_trade_usdc_cap=_env_float("WEATHERBOT_LIVE_TRADE_CAP_USDC", 2.0),
            max_open_exposure_usdc=_env_float("WEATHERBOT_MAX_OPEN_EXPOSURE_USDC", 25.0),
            max_daily_loss_usdc=_env_float("WEATHERBOT_MAX_DAILY_LOSS_USDC", 15.0),
            max_new_trades_per_day=int(_env_float("WEATHERBOT_MAX_NEW_TRADES_PER_DAY", 5)),
            max_price=_env_float("WEATHERBOT_LIVE_MAX_PRICE", 0.45),
            max_slippage=_env_float("WEATHERBOT_LIVE_MAX_SLIPPAGE", 0.03),
            min_top_ask_size=_env_float("WEATHERBOT_LIVE_MIN_TOP_ASK_SIZE", 1.0),
            kill_switch_file=os.getenv("WEATHERBOT_KILL_SWITCH_FILE", "data/pause_live"),
        )


class ExecutionGateway:
    def __init__(self, root: Path, config: ExecutionConfig | None = None) -> None:
        self.root = root
        self.cfg = config or ExecutionConfig.from_env()
        self.ledger = LiveLedger(self.root / "data")
        self._client: Any = None
        self._client_ready = False
        self._last_error: str | None = None

    def mode_label(self) -> str:
        if self.cfg.live_trading_enabled and not self.cfg.dry_run_live:
            return "LIVE"
        if self.cfg.live_trading_enabled and self.cfg.dry_run_live:
            return "SHADOW"
        return "PAPER"

    def kill_switch_path(self) -> Path:
        return self.root / self.cfg.kill_switch_file

    def _has_kill_switch(self) -> bool:
        return self.kill_switch_path().exists()

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _daily_stats(self) -> tuple[int, float]:
        trades = 0
        pnl = 0.0
        path = self.ledger.trades_path
        if not path.exists():
            return trades, pnl
        day = self._today()
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = __import__("json").loads(line)
            except Exception:
                continue
            if not str(row.get("ts", "")).startswith(day):
                continue
            if row.get("event") == "entry_submitted":
                trades += 1
            if row.get("event") == "exit_closed":
                pnl += float(row.get("realized_pnl", 0.0) or 0.0)
            elif row.get("event") == "exit_needs_manual_close":
                # Live exits are manual in the first rollout. Count the bot's
                # exit signal PnL so the daily-loss guard still has a stop input.
                pnl += float(row.get("expected_pnl", 0.0) or 0.0)
        return trades, pnl

    def _open_exposure(self) -> float:
        total = 0.0
        path = self.ledger.events_path
        if not path.exists():
            return total
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = __import__("json").loads(line)
            except Exception:
                continue
            if row.get("event") == "open_exposure_snapshot":
                total = float(row.get("open_exposure", total) or total)
        return total

    def _ensure_client(self) -> bool:
        if self._client_ready:
            return True
        try:
            from py_clob_client.client import ClobClient  # type: ignore
            from py_clob_client.clob_types import ApiCreds  # type: ignore
        except Exception as exc:
            self._last_error = f"py-clob-client unavailable: {exc}"
            return False

        private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
        api_key = os.getenv("POLYMARKET_API_KEY", "").strip()
        api_secret = os.getenv("POLYMARKET_SECRET", "").strip()
        api_passphrase = os.getenv("POLYMARKET_PASSPHRASE", "").strip()
        funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "").strip() or None

        if not all([private_key, api_key, api_secret, api_passphrase]):
            self._last_error = "Missing POLYMARKET_* credentials for live orders"
            return False

        try:
            creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase)
            self._client = ClobClient(
                host="https://clob.polymarket.com",
                key=private_key,
                chain_id=137,
                creds=creds,
                funder=funder,
            )
            self._client_ready = True
            return True
        except Exception as exc:
            self._last_error = f"CLOB client init failed: {exc}"
            return False

    def evaluate_entry(self, signal: TradeSignal) -> tuple[bool, str, dict[str, Any]]:
        side = str(getattr(signal, "outcome_side", "YES") or "YES").upper()
        if side not in {"YES", "NO"}:
            return False, "invalid_outcome_side", {"mode": self.mode_label(), "outcome_side": side}

        snapshot = get_clob_market_snapshot(signal.market_id)
        side_key = side.lower()
        side_book = snapshot.get(side_key) or {}
        token_id = getattr(signal, "token_id", None) or side_book.get("token_id")
        bid = side_book.get("bid")
        ask = side_book.get("ask")
        spread = side_book.get("spread")
        ask_size = float(side_book.get("ask_size") or 0.0)

        details = {
            "token_id": token_id,
            "clob_bid": bid,
            "clob_ask": ask,
            "clob_spread": spread,
            "clob_ask_size": ask_size,
            "order_shares": round(signal.size_usdc / ask, 4) if ask else 0.0,
            "outcome_side": side,
            "synthetic_no": bool(snapshot.get("synthetic_no")),
            "mode": self.mode_label(),
        }

        if self._has_kill_switch():
            return False, "kill_switch_active", details
        if token_id is None:
            return False, "missing_token_id", details
        if side == "NO" and details["synthetic_no"]:
            return False, "synthetic_no_not_tradeable", details
        if ask is None or bid is None:
            return False, "missing_orderbook_top", details
        if ask > self.cfg.max_price:
            return False, "ask_above_max_price", details
        if spread is not None and spread > self.cfg.max_slippage:
            return False, "spread_above_max_slippage", details
        if ask_size < self.cfg.min_top_ask_size:
            return False, "top_ask_size_below_min", details
        if ask_size < details["order_shares"]:
            return False, "top_ask_size_below_order_size", details
        if signal.size_usdc > self.cfg.per_trade_usdc_cap:
            return False, "size_above_per_trade_cap", details

        new_trades_today, daily_pnl = self._daily_stats()
        if new_trades_today >= self.cfg.max_new_trades_per_day:
            return False, "max_daily_trades_reached", details
        if daily_pnl <= -abs(self.cfg.max_daily_loss_usdc):
            return False, "max_daily_loss_hit", details

        open_exposure = self._open_exposure()
        if open_exposure + signal.size_usdc > self.cfg.max_open_exposure_usdc:
            return False, "max_open_exposure_reached", details

        return True, "ok", details

    def on_entry_signal(self, signal: TradeSignal) -> dict[str, Any]:
        payload = signal.to_dict()
        self.ledger.log_signal({**payload, "event": "entry_signal"})

        ok, reason, details = self.evaluate_entry(signal)
        event_base = {**payload, **details, "event": "entry_evaluated", "decision": reason}
        self.ledger.log_event(event_base)
        if not ok:
            return {"submitted": False, "reason": reason, **details}

        if not self.cfg.live_trading_enabled or self.cfg.dry_run_live:
            self.ledger.log_trade({**payload, **details, "event": "entry_shadow_pass"})
            return {"submitted": False, "reason": "shadow_mode", **details}

        if not self._ensure_client():
            self.ledger.log_trade(
                {**payload, **details, "event": "entry_failed", "reason": self._last_error or "client_init"}
            )
            return {"submitted": False, "reason": self._last_error or "client_init", **details}

        # NOTE: guarded live path. We intentionally submit limit orders only.
        try:
            order_resp = self._client.create_and_post_order(  # type: ignore[attr-defined]
                token_id=details["token_id"],
                side="BUY",
                price=details["clob_ask"],
                size=details["order_shares"],
                order_type="GTC",
            )
            self.ledger.log_trade(
                {
                    **payload,
                    **details,
                    "event": "entry_submitted",
                    "order_response": order_resp,
                }
            )
            return {"submitted": True, "reason": "live_submitted", "order_response": order_resp, **details}
        except Exception as exc:
            self.ledger.log_trade(
                {
                    **payload,
                    **details,
                    "event": "entry_failed",
                    "reason": f"order_submit_error: {exc}",
                }
            )
            return {"submitted": False, "reason": f"order_submit_error: {exc}", **details}

    def on_exit_signal(self, signal: ExitSignal) -> dict[str, Any]:
        payload = signal.to_dict()
        self.ledger.log_signal({**payload, "event": "exit_signal"})
        if not self.cfg.live_trading_enabled or self.cfg.dry_run_live:
            self.ledger.log_trade(
                {
                    **payload,
                    "event": "exit_closed",
                    "reason": "shadow_mode",
                    "realized_pnl": payload.get("expected_pnl", 0.0),
                }
            )
            return {"submitted": False, "reason": "shadow_mode"}

        if self._has_kill_switch():
            self.ledger.log_trade({**payload, "event": "exit_blocked", "reason": "kill_switch_active"})
            return {"submitted": False, "reason": "kill_switch_active"}

        # We keep the live exit path conservative; on first rollout this path is
        # used as an auditable signal and can be upgraded to active close orders.
        self.ledger.log_trade({**payload, "event": "exit_needs_manual_close"})
        return {"submitted": False, "reason": "manual_close_phase"}
