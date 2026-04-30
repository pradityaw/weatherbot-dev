from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class TradeSignal:
    kind: str  # "entry"
    ts: str
    city: str
    city_name: str
    date: str
    market_id: str
    question: str
    bucket_low: float
    bucket_high: float
    forecast_temp: float
    forecast_source: str
    probability: float
    ev: float
    kelly: float
    ask: float
    bid: float
    spread: float
    size_usdc: float
    shares: float
    reason: str
    strategy: str = "temperature"
    outcome_side: str = "YES"
    token_id: str | None = None

    @classmethod
    def from_best_signal(
        cls,
        *,
        city: str,
        city_name: str,
        date: str,
        best_signal: dict[str, Any],
    ) -> "TradeSignal":
        return cls(
            kind="entry",
            ts=_utc_now_iso(),
            city=city,
            city_name=city_name,
            date=date,
            market_id=str(best_signal.get("market_id", "")),
            question=str(best_signal.get("question", "")),
            bucket_low=float(best_signal.get("bucket_low", 0.0)),
            bucket_high=float(best_signal.get("bucket_high", 0.0)),
            forecast_temp=float(best_signal.get("forecast_temp", 0.0)),
            forecast_source=str(best_signal.get("forecast_src", "unknown")),
            probability=float(best_signal.get("p", 0.0)),
            ev=float(best_signal.get("ev", 0.0)),
            kelly=float(best_signal.get("kelly", 0.0)),
            ask=float(best_signal.get("entry_price", 0.0)),
            bid=float(best_signal.get("bid_at_entry", best_signal.get("entry_price", 0.0))),
            spread=float(best_signal.get("spread", 0.0)),
            size_usdc=float(best_signal.get("cost", 0.0)),
            shares=float(best_signal.get("shares", 0.0)),
            reason="best_signal",
            strategy=str(best_signal.get("strategy", "temperature")),
            outcome_side=str(best_signal.get("side", "YES")).upper(),
            token_id=best_signal.get("token_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExitSignal:
    kind: str  # "exit"
    ts: str
    city: str
    city_name: str
    date: str
    market_id: str
    entry_price: float
    current_price: float
    shares: float
    close_reason: str
    hours_left: float
    expected_pnl: float

    @classmethod
    def from_position(
        cls,
        *,
        city: str,
        city_name: str,
        date: str,
        market_id: str,
        position: dict[str, Any],
        current_price: float,
        close_reason: str,
        hours_left: float = 0.0,
    ) -> "ExitSignal":
        entry_price = float(position.get("entry_price", 0.0))
        shares = float(position.get("shares", 0.0))
        expected_pnl = round((float(current_price) - entry_price) * shares, 2)
        return cls(
            kind="exit",
            ts=_utc_now_iso(),
            city=city,
            city_name=city_name,
            date=date,
            market_id=str(market_id),
            entry_price=entry_price,
            current_price=float(current_price),
            shares=shares,
            close_reason=close_reason,
            hours_left=float(hours_left),
            expected_pnl=expected_pnl,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
