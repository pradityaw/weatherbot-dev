"""
Pattern stats for top wallets: entry price, timing vs event end, lead-lag proxies.

Usage:
  .venv/bin/python -m trader_research.analyze_patterns --top 25
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trader_research.common import RAW_DIR, RESEARCH_DATA_DIR, ensure_dirs
from trader_research.rank_traders import PROCESSED_DIR, load_jsonl, parse_end_dt

OUT_PATH = RESEARCH_DATA_DIR / "processed" / "pattern_summary.json"


def first_buy_entry(trades: list[dict[str, Any]], wallet: str) -> dict[str, Any] | None:
    w = wallet.lower()
    sorted_trades = sorted(
        [t for t in trades if str(t.get("proxyWallet", "")).lower() == w],
        key=lambda t: int(t.get("timestamp") or 0),
    )
    for t in sorted_trades:
        if str(t.get("side", "")).upper() != "BUY":
            continue
        return {
            "price": float(t.get("price") or 0),
            "outcome": str(t.get("outcome") or ""),
            "ts": int(t.get("timestamp") or 0),
        }
    return None


def run_analyze(top_n: int = 25) -> dict[str, Any]:
    ensure_dirs()
    rankings_path = PROCESSED_DIR / "rankings.json"
    if not rankings_path.exists():
        raise SystemExit(f"Missing {rankings_path}; run python -m trader_research.rank_traders first")

    rk = json.loads(rankings_path.read_text(encoding="utf-8"))
    top = [x["address"] for x in rk.get("top_traders", [])[:top_n]]

    markets = load_jsonl(RAW_DIR / "markets.jsonl")
    market_by_cid = {str(m.get("condition_id")): m for m in markets if m.get("condition_id")}
    top_set = set(w.lower() for w in top)

    entry_prices_yes: list[float] = []
    hours_before_end: list[float] = []

    # Stream trades grouped by condition (memory efficient)
    trades_path = RAW_DIR / "trades.jsonl"
    cur_cid: str | None = None
    cur_trades: list[dict[str, Any]] = []

    def flush_cid(cid: str, trades: list[dict[str, Any]]) -> None:
        meta = market_by_cid.get(cid, {})
        end_dt = parse_end_dt(meta)
        for w in top:
            fb = first_buy_entry(trades, w)
            if not fb or fb["outcome"].lower() != "yes":
                continue
            entry_prices_yes.append(fb["price"])
            if end_dt and fb["ts"]:
                t_trade = datetime.fromtimestamp(fb["ts"], tz=timezone.utc)
                delta_h = (end_dt - t_trade).total_seconds() / 3600.0
                if delta_h >= 0:
                    hours_before_end.append(delta_h)

    with trades_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except Exception:
                continue
            w = str(t.get("proxyWallet") or "").lower()
            if w not in top_set:
                continue
            cid = str(t.get("conditionId") or "")
            if not cid:
                continue
            if cid != cur_cid:
                if cur_cid and cur_trades:
                    flush_cid(cur_cid, cur_trades)
                cur_cid = cid
                cur_trades = []
            cur_trades.append(t)
    if cur_cid and cur_trades:
        flush_cid(cur_cid, cur_trades)

    def pct(xs: list[float], p: float) -> float:
        if not xs:
            return 0.0
        xs = sorted(xs)
        k = int(round((len(xs) - 1) * p))
        return round(xs[k], 4)

    summary = {
        "top_n": top_n,
        "wallets": top,
        "n_first_yes_buys": len(entry_prices_yes),
        "entry_price_yes_p50": pct(entry_prices_yes, 0.5),
        "entry_price_yes_p90": pct(entry_prices_yes, 0.9),
        "hours_before_end_p50": pct(hours_before_end, 0.5),
        "hours_before_end_p90": pct(hours_before_end, 0.9),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()
    s = run_analyze(top_n=args.top)
    print(json.dumps(s, indent=2))


if __name__ == "__main__":
    main()
