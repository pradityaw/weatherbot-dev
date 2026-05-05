import json
from datetime import datetime, timezone
from unittest.mock import patch

from execution import ExecutionConfig, ExecutionGateway
from signals import TradeSignal


def _make_gateway(tmp_path, **cfg_overrides):
    defaults = {
        "dry_run_live": True,
        "live_trading_enabled": False,
        "max_live_usdc": 100.0,
        "per_trade_usdc_cap": 2.0,
        "max_open_exposure_usdc": 25.0,
        "max_daily_loss_usdc": 15.0,
        "max_new_trades_per_day": 5,
        "max_price": 0.45,
        "max_slippage": 0.03,
        "min_top_ask_size": 1.0,
        "kill_switch_file": "data/pause_live",
    }
    defaults.update(cfg_overrides)
    return ExecutionGateway(tmp_path, ExecutionConfig(**defaults))


def _signal(**overrides):
    defaults = {
        "kind": "entry",
        "ts": datetime.now(timezone.utc).isoformat(),
        "city": "nyc",
        "city_name": "New York City",
        "date": "2026-05-04",
        "market_id": "market-1",
        "question": "Will NYC be hot?",
        "bucket_low": 70.0,
        "bucket_high": 71.0,
        "forecast_temp": 70.5,
        "forecast_source": "ecmwf",
        "probability": 0.7,
        "ev": 0.1,
        "kelly": 0.05,
        "ask": 0.3,
        "bid": 0.28,
        "spread": 0.02,
        "size_usdc": 1.0,
        "shares": 3.3333,
        "reason": "test",
        "strategy": "temperature",
        "outcome_side": "YES",
        "token_id": None,
    }
    defaults.update(overrides)
    return TradeSignal(**defaults)


def _snapshot(
    *,
    ask=0.30,
    bid=0.28,
    ask_size=5.0,
    token_id="tok-yes",
    synthetic_no=False,
    no_token_id="tok-no",
    no_ask=0.70,
    no_bid=0.68,
    no_ask_size=5.0,
):
    return {
        "market_id": "market-1",
        "yes": {
            "token_id": token_id,
            "bid": bid,
            "ask": ask,
            "spread": round(ask - bid, 4) if ask is not None and bid is not None else None,
            "ask_size": ask_size,
        },
        "no": {
            "token_id": no_token_id,
            "bid": no_bid,
            "ask": no_ask,
            "spread": round(no_ask - no_bid, 4)
            if no_ask is not None and no_bid is not None
            else None,
            "ask_size": no_ask_size,
        },
        "synthetic_no": synthetic_no,
    }


def _evaluate_with_snapshot(tmp_path, snapshot, signal=None, **cfg_overrides):
    gateway = _make_gateway(tmp_path, **cfg_overrides)
    with patch("execution.get_clob_market_snapshot", return_value=snapshot):
        return gateway.evaluate_entry(signal or _signal())


def test_mode_label_reports_paper_shadow_and_live(tmp_path):
    assert _make_gateway(
        tmp_path, live_trading_enabled=False, dry_run_live=True
    ).mode_label() == "PAPER"
    assert _make_gateway(
        tmp_path, live_trading_enabled=True, dry_run_live=True
    ).mode_label() == "SHADOW"
    assert _make_gateway(
        tmp_path, live_trading_enabled=True, dry_run_live=False
    ).mode_label() == "LIVE"


def test_evaluate_entry_rejects_invalid_side_before_snapshot(tmp_path):
    gateway = _make_gateway(tmp_path)
    ok, reason, details = gateway.evaluate_entry(_signal(outcome_side="MAYBE"))
    assert ok is False
    assert reason == "invalid_outcome_side"
    assert details["outcome_side"] == "MAYBE"


def test_evaluate_entry_rejects_kill_switch(tmp_path):
    gateway = _make_gateway(tmp_path)
    gateway.kill_switch_path().parent.mkdir(parents=True, exist_ok=True)
    gateway.kill_switch_path().write_text("paused\n", encoding="utf-8")
    with patch("execution.get_clob_market_snapshot", return_value=_snapshot()):
        ok, reason, _ = gateway.evaluate_entry(_signal())
    assert ok is False
    assert reason == "kill_switch_active"


def test_evaluate_entry_rejects_missing_token_id(tmp_path):
    ok, reason, _ = _evaluate_with_snapshot(tmp_path, _snapshot(token_id=None))
    assert ok is False
    assert reason == "missing_token_id"


def test_evaluate_entry_rejects_synthetic_no(tmp_path):
    ok, reason, details = _evaluate_with_snapshot(
        tmp_path,
        _snapshot(synthetic_no=True, no_token_id="tok-no"),
        signal=_signal(outcome_side="NO"),
    )
    assert ok is False
    assert reason == "synthetic_no_not_tradeable"
    assert details["outcome_side"] == "NO"


def test_evaluate_entry_rejects_missing_orderbook_top(tmp_path):
    ok, reason, _ = _evaluate_with_snapshot(
        tmp_path, _snapshot(ask=None, bid=0.28)
    )
    assert ok is False
    assert reason == "missing_orderbook_top"


def test_evaluate_entry_rejects_ask_above_max_price(tmp_path):
    ok, reason, _ = _evaluate_with_snapshot(tmp_path, _snapshot(ask=0.46, bid=0.44))
    assert ok is False
    assert reason == "ask_above_max_price"


def test_evaluate_entry_rejects_spread_above_max_slippage(tmp_path):
    ok, reason, _ = _evaluate_with_snapshot(tmp_path, _snapshot(ask=0.30, bid=0.25))
    assert ok is False
    assert reason == "spread_above_max_slippage"


def test_evaluate_entry_rejects_top_ask_size_below_min(tmp_path):
    ok, reason, _ = _evaluate_with_snapshot(tmp_path, _snapshot(ask_size=0.5))
    assert ok is False
    assert reason == "top_ask_size_below_min"


def test_evaluate_entry_rejects_top_ask_size_below_order_size(tmp_path):
    ok, reason, details = _evaluate_with_snapshot(
        tmp_path,
        _snapshot(ask=0.30, bid=0.28, ask_size=2.0),
        signal=_signal(size_usdc=1.0),
    )
    assert ok is False
    assert reason == "top_ask_size_below_order_size"
    assert details["order_shares"] > details["clob_ask_size"]


def test_evaluate_entry_rejects_size_above_per_trade_cap(tmp_path):
    ok, reason, _ = _evaluate_with_snapshot(
        tmp_path,
        _snapshot(ask_size=50.0),
        signal=_signal(size_usdc=3.0),
        per_trade_usdc_cap=2.0,
    )
    assert ok is False
    assert reason == "size_above_per_trade_cap"


def test_evaluate_entry_rejects_max_daily_trades_reached(tmp_path):
    gateway = _make_gateway(tmp_path, max_new_trades_per_day=1)
    gateway.ledger.log_trade({"event": "entry_submitted"})
    with patch("execution.get_clob_market_snapshot", return_value=_snapshot()):
        ok, reason, _ = gateway.evaluate_entry(_signal())
    assert ok is False
    assert reason == "max_daily_trades_reached"


def test_evaluate_entry_rejects_max_daily_loss_hit(tmp_path):
    gateway = _make_gateway(tmp_path, max_daily_loss_usdc=10.0)
    gateway.ledger.log_trade({"event": "exit_closed", "realized_pnl": -10.0})
    with patch("execution.get_clob_market_snapshot", return_value=_snapshot()):
        ok, reason, _ = gateway.evaluate_entry(_signal())
    assert ok is False
    assert reason == "max_daily_loss_hit"


def test_evaluate_entry_rejects_max_open_exposure_reached(tmp_path):
    gateway = _make_gateway(tmp_path, max_open_exposure_usdc=5.0)
    gateway.ledger.log_event({"event": "open_exposure_snapshot", "open_exposure": 4.5})
    with patch("execution.get_clob_market_snapshot", return_value=_snapshot()):
        ok, reason, _ = gateway.evaluate_entry(_signal(size_usdc=1.0))
    assert ok is False
    assert reason == "max_open_exposure_reached"


def test_evaluate_entry_accepts_when_all_guards_clear(tmp_path):
    ok, reason, details = _evaluate_with_snapshot(tmp_path, _snapshot())
    assert ok is True
    assert reason == "ok"
    assert details["token_id"] == "tok-yes"


def test_on_entry_signal_paper_mode_records_shadow_pass(tmp_path):
    gateway = _make_gateway(tmp_path, live_trading_enabled=False, dry_run_live=True)
    with patch("execution.get_clob_market_snapshot", return_value=_snapshot()):
        result = gateway.on_entry_signal(_signal())
    assert result["submitted"] is False
    assert result["reason"] == "shadow_mode"

    rows = [
        json.loads(line)
        for line in gateway.ledger.trades_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["event"] == "entry_shadow_pass"


def test_on_entry_signal_shadow_mode_records_shadow_pass(tmp_path):
    gateway = _make_gateway(tmp_path, live_trading_enabled=True, dry_run_live=True)
    with patch("execution.get_clob_market_snapshot", return_value=_snapshot()):
        result = gateway.on_entry_signal(_signal())
    assert result["submitted"] is False
    assert result["reason"] == "shadow_mode"

    rows = [
        json.loads(line)
        for line in gateway.ledger.trades_path.read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["event"] == "entry_shadow_pass"
