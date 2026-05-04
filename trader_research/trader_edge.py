"""
Shadow-only trader-edge context for live bot runs.

Set WEATHERBOT_TRADER_EDGE=1 to log consensus features for the matched bucket.
Set WEATHERBOT_TRADER_EDGE_STRENGTH > 0 to opt into a small probability nudge.

Requires prior offline pipeline:
  python -m trader_research.collect ...
  python -m trader_research.rank_traders
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from trader_research.common import fetch_all_trades_for_condition
from trader_research.pnl import position_for_market

_TRADES_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL_S = 300.0


def _env_bool(key: str) -> bool:
    return os.getenv(key, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_rankings() -> dict[str, Any] | None:
    path = Path(os.getenv("WEATHERBOT_TRADER_RANKINGS", "data/trader_research/processed/rankings.json"))
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _top_wallets(n: int) -> list[str]:
    rk = _load_rankings()
    if not rk:
        return []
    out: list[str] = []
    for row in rk.get("top_traders", [])[:n]:
        a = str(row.get("address", "")).lower()
        if a:
            out.append(a)
    return out


def _cached_trades(condition_id: str) -> list[dict[str, Any]]:
    now = time.time()
    ent = _TRADES_CACHE.get(condition_id)
    if ent and now - ent[0] < _CACHE_TTL_S:
        return ent[1]
    trades = fetch_all_trades_for_condition(condition_id)
    _TRADES_CACHE[condition_id] = (now, trades)
    return trades


def compute_edge_features(
    condition_id: str,
    top_wallets: list[str],
    *,
    min_yes_shares: float = 1.0,
) -> dict[str, Any]:
    trades = _cached_trades(condition_id)
    long_yes = 0
    short_yes = 0
    strong_no_veto = False
    per_wallet: dict[str, dict[str, float]] = {}

    for w in top_wallets:
        pos = position_for_market(trades, w)
        per_wallet[w] = {"yes_shares": pos.yes_shares, "no_shares": pos.no_shares}
        if pos.yes_shares >= min_yes_shares:
            long_yes += 1
        elif pos.yes_shares <= -min_yes_shares:
            short_yes += 1
        if pos.no_shares >= min_yes_shares and pos.yes_shares < 1:
            strong_no_veto = True

    active = long_yes + short_yes > 0
    if strong_no_veto:
        consensus_pred = False
    else:
        consensus_pred = long_yes > short_yes

    score = 0.0
    for w in top_wallets:
        pw = per_wallet.get(w, {})
        score += float(pw.get("yes_shares", 0)) - float(pw.get("no_shares", 0))

    return {
        "condition_id": condition_id,
        "n_trades": len(trades),
        "top_wallets_considered": len(top_wallets),
        "long_yes_wallets": long_yes,
        "short_yes_wallets": short_yes,
        "strong_no_veto": strong_no_veto,
        "consensus_pred_yes": consensus_pred,
        "active_top_traders": active,
        "net_yes_minus_no_score": round(score, 4),
    }


def maybe_log_trader_edge(
    exec_gateway: Any,
    *,
    city_slug: str,
    date_str: str,
    matched_outcome: dict[str, Any],
    best_signal: dict[str, Any],
) -> dict[str, Any] | None:
    """Log trader-edge snapshot to live_events.jsonl and return features for optional sizing nudges."""
    if not _env_bool("WEATHERBOT_TRADER_EDGE"):
        return None

    cid = str(matched_outcome.get("condition_id") or "").strip()
    if not cid:
        exec_gateway.ledger.log_event(
            {
                "event": "trader_edge_skip",
                "reason": "missing_condition_id",
                "city": city_slug,
                "date": date_str,
                "market_id": best_signal.get("market_id"),
            }
        )
        return None

    try:
        top_n = int(os.getenv("WEATHERBOT_TRADER_EDGE_TOP_N", "15"))
    except ValueError:
        top_n = 15
    tops = _top_wallets(top_n)
    if not tops:
        exec_gateway.ledger.log_event(
            {
                "event": "trader_edge_skip",
                "reason": "no_rankings_file_or_empty",
                "city": city_slug,
                "date": date_str,
                "condition_id": cid,
            }
        )
        return None

    try:
        feats = compute_edge_features(cid, tops)
    except Exception as exc:
        exec_gateway.ledger.log_event(
            {
                "event": "trader_edge_error",
                "reason": str(exc),
                "city": city_slug,
                "date": date_str,
                "condition_id": cid,
            }
        )
        return None

    exec_gateway.ledger.log_event(
        {
            "event": "trader_edge_snapshot",
            "city": city_slug,
            "date": date_str,
            "market_id": best_signal.get("market_id"),
            "question": matched_outcome.get("question"),
            "forecast_temp": best_signal.get("forecast_temp"),
            "strategy": "temperature",
            **feats,
        }
    )
    return feats


def trader_edge_prob_nudge(
    p: float,
    edge_features: dict[str, Any],
    *,
    strength: float | None = None,
) -> float:
    """Optionally nudge probability when WEATHERBOT_TRADER_EDGE_STRENGTH is positive."""
    s = strength
    if s is None:
        try:
            s = float(os.getenv("WEATHERBOT_TRADER_EDGE_STRENGTH", "0"))
        except ValueError:
            s = 0.0
    if s <= 0:
        return p
    if not edge_features.get("active_top_traders"):
        return p
    score = float(edge_features.get("net_yes_minus_no_score", 0))
    # squash score roughly into [-1,1]
    import math

    delta = math.tanh(score / 50.0) * s
    return max(0.01, min(0.99, p + delta))
