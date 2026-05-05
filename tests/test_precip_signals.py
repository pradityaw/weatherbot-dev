from __future__ import annotations

from types import SimpleNamespace

from precip_engine import _evaluate_precip_gateway, _precip_trade_signal


class RecordingLedger:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def log_event(self, payload: dict) -> None:
        self.events.append(payload)


class RecordingGateway:
    def __init__(self, *, live: bool = False, dry_run: bool = True) -> None:
        self.cfg = SimpleNamespace(live_trading_enabled=live, dry_run_live=dry_run)
        self.ledger = RecordingLedger()
        self.received = []

    def mode_label(self) -> str:
        if self.cfg.live_trading_enabled and not self.cfg.dry_run_live:
            return "LIVE"
        if self.cfg.live_trading_enabled and self.cfg.dry_run_live:
            return "SHADOW"
        return "PAPER"

    def on_entry_signal(self, signal):
        self.received.append(signal)
        return {"submitted": False, "reason": "shadow_mode", "strategy": signal.strategy}


def _scored(**overrides):
    row = {
        "ts": "2026-05-04T20:00:00+00:00",
        "city": "new_york_city",
        "city_name": "New York City",
        "window": "May 2026",
        "market_id": "precip-market-1",
        "question": "Will NYC get over 3 inches of rain in May?",
        "threshold_inches": 3.0,
        "threshold_low_inches": None,
        "threshold_high_inches": None,
        "forecast_total_inches": 3.4,
        "probability": 0.64,
        "ev": 0.18,
        "kelly": 0.2,
        "ask": 0.32,
        "bid": 0.30,
        "spread": 0.02,
        "size_usdc": 1.6,
        "side": "YES",
        "token_id": "precip-token",
    }
    row.update(overrides)
    return row


def test_precip_trade_signal_maps_scored_row():
    signal = _precip_trade_signal(_scored())

    assert signal.kind == "entry"
    assert signal.strategy == "precipitation"
    assert signal.outcome_side == "YES"
    assert signal.market_id == "precip-market-1"
    assert signal.city == "new_york_city"
    assert signal.city_name == "New York City"
    assert signal.date == "May 2026"
    assert signal.bucket_low == 3.0
    assert signal.bucket_high == 3.0
    assert signal.forecast_source == "open_meteo_precip"
    assert signal.forecast_temp == 3.4
    assert signal.probability == 0.64
    assert signal.ask == 0.32
    assert signal.bid == 0.30
    assert signal.spread == 0.02
    assert signal.size_usdc == 1.6
    assert signal.shares == 5.0
    assert signal.token_id == "precip-token"


def test_precip_trade_signal_uses_range_bounds_and_no_side():
    signal = _precip_trade_signal(
        _scored(
            threshold_inches=None,
            threshold_low_inches=1.0,
            threshold_high_inches=2.0,
            side="NO",
            ask=0.25,
            size_usdc=1.0,
        )
    )

    assert signal.bucket_low == 1.0
    assert signal.bucket_high == 2.0
    assert signal.outcome_side == "NO"
    assert signal.shares == 4.0


def test_evaluate_precip_gateway_blocks_real_live_submission():
    gateway = RecordingGateway(live=True, dry_run=False)

    result = _evaluate_precip_gateway(_scored(), gateway)

    assert result == {
        "submitted": False,
        "reason": "precip_live_submission_disabled",
    }
    assert gateway.received == []
    assert gateway.ledger.events[0]["event"] == "precip_entry_skipped"
    assert gateway.ledger.events[0]["reason"] == "precip_live_submission_disabled"
    assert gateway.ledger.events[0]["mode"] == "LIVE"


def test_evaluate_precip_gateway_delegates_to_shadow_gateway():
    gateway = RecordingGateway(live=True, dry_run=True)

    result = _evaluate_precip_gateway(_scored(), gateway)

    assert result == {
        "submitted": False,
        "reason": "shadow_mode",
        "strategy": "precipitation",
    }
    assert len(gateway.received) == 1
    assert gateway.received[0].strategy == "precipitation"
    assert gateway.received[0].outcome_side == "YES"
