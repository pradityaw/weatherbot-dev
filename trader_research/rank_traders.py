"""
Aggregate realized PnL per wallet across resolved weather bucket markets.

Uses a streaming two-pass approach so it never loads the full 7 GB trades file into RAM:
  Pass 1: build set of resolved condition_ids from markets.jsonl
  Pass 2: stream trades.jsonl line-by-line, accumulating per-wallet share positions

Usage:
  .venv/bin/python -m trader_research.rank_traders
  .venv/bin/python -m trader_research.rank_traders --rolling-days 90
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trader_research.common import RAW_DIR, RESEARCH_DATA_DIR, ensure_dirs
from trader_research.pnl import TokenPosition, WalletStats

PROCESSED_DIR = RESEARCH_DATA_DIR / "processed"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def parse_end_dt(row: dict[str, Any]) -> datetime | None:
    raw = row.get("event_end_date") or row.get("end_date")
    if not raw:
        return None
    try:
        s = str(raw).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def run_rank(
    *,
    rolling_days: int | None = None,
    min_markets: int = 3,
    dust_pnl: float = 1e-6,
) -> dict[str, Any]:
    ensure_dirs()
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    cutoff = None
    if rolling_days is not None:
        from datetime import timedelta
        cutoff = now - timedelta(days=rolling_days)

    # --- Pass 1: index resolved markets (small, fits in RAM) ---
    print("[rank] Loading markets index...", flush=True)
    market_by_cid: dict[str, dict[str, Any]] = {}
    markets_path = RAW_DIR / "markets.jsonl"
    with markets_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except Exception:
                continue
            cid = str(m.get("condition_id") or "")
            if not cid:
                continue
            market_by_cid[cid] = m

    # Filter to resolved and within rolling window
    resolved: dict[str, bool] = {}  # cid -> yes_won
    for cid, meta in market_by_cid.items():
        yes_res = meta.get("yes_resolved")
        if yes_res is None:
            continue
        end_dt = parse_end_dt(meta)
        if cutoff is not None and end_dt is not None and end_dt < cutoff:
            continue
        resolved[cid] = bool(yes_res)

    print(f"[rank] {len(resolved)} resolved markets in window", flush=True)

    # --- Pass 2: stream trades (memory-efficient) ---
    # Accumulate per (cid, wallet) positions without holding all trades in RAM.
    # Structure: positions[cid][wallet] = TokenPosition
    print("[rank] Streaming trades...", flush=True)
    positions: dict[str, dict[str, TokenPosition]] = defaultdict(dict)
    trades_path = RAW_DIR / "trades.jsonl"
    n = 0
    with trades_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except Exception:
                continue
            cid = str(t.get("conditionId") or "")
            if cid not in resolved:
                continue
            w = str(t.get("proxyWallet") or "").lower()
            if not w:
                continue
            pos = positions[cid].setdefault(w, TokenPosition())
            pos.apply_trade(
                str(t.get("side") or ""),
                str(t.get("outcome") or ""),
                float(t.get("price") or 0),
                float(t.get("size") or 0),
            )
            n += 1
            if n % 500_000 == 0:
                print(f"[rank]   ...{n:,} trades processed", flush=True)

    print(f"[rank] {n:,} relevant trades processed across {len(positions)} conditions", flush=True)

    # --- Aggregate wallet stats ---
    wallets: dict[str, WalletStats] = {}
    for cid, wmap in positions.items():
        yes_won = resolved[cid]
        for w, pos in wmap.items():
            pnl = pos.resolved_pnl(yes_won)
            if abs(pnl) < dust_pnl:
                continue
            st = wallets.setdefault(w, WalletStats(address=w))
            st.total_pnl += pnl
            st.markets_traded += 1
            if pnl > 0:
                st.winning_markets += 1
            st.market_pnls.append(pnl)
            st.total_buy_notional += pos.buy_notional

    ranked = sorted(wallets.values(), key=lambda x: x.total_pnl, reverse=True)
    ranked = [x for x in ranked if x.markets_traded >= min_markets]

    out: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "rolling_days": rolling_days,
        "n_markets_resolved": len(resolved),
        "n_wallets": len(ranked),
        "top_traders": [
            {
                "address": x.address,
                "total_pnl": round(x.total_pnl, 4),
                "markets_traded": x.markets_traded,
                "win_rate": round(x.win_rate, 4),
                "roi": round(x.roi, 6),
                "total_buy_notional": round(x.total_buy_notional, 2),
            }
            for x in ranked[:200]
        ],
    }

    out_path = PROCESSED_DIR / "rankings.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def index_trades_by_condition(trades: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Helper used by analyze_patterns and backtest_edge for small in-memory trade lists."""
    by_c: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        cid = str(t.get("conditionId") or "")
        if cid:
            by_c[cid].append(t)
    return dict(by_c)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rolling-days", type=int, default=None, help="Only markets ending after now-N days")
    ap.add_argument("--min-markets", type=int, default=3)
    args = ap.parse_args()
    out = run_rank(rolling_days=args.rolling_days, min_markets=args.min_markets)
    print(json.dumps({"wrote": str(PROCESSED_DIR / "rankings.json"), "n_wallets": out["n_wallets"]}, indent=2))


if __name__ == "__main__":
    main()
