"""
Offline evaluation of simple trader-derived signals vs outcomes.

This does not fetch forecasts (see data/markets/*.json for a full bot replay).
Outputs processed/backtest_summary.json.

Usage:
  .venv/bin/python -m trader_research.backtest_edge --top 15
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from trader_research.common import RAW_DIR, RESEARCH_DATA_DIR, ensure_dirs
from trader_research.pnl import TokenPosition
from trader_research.rank_traders import PROCESSED_DIR, load_jsonl

SUMMARY_PATH = RESEARCH_DATA_DIR / "processed" / "backtest_summary.json"


def run_backtest(top_n: int = 15, min_yes_shares: float = 1.0) -> dict[str, Any]:
    ensure_dirs()
    rankings_path = PROCESSED_DIR / "rankings.json"
    if not rankings_path.exists():
        raise SystemExit(f"Missing {rankings_path}; run python -m trader_research.rank_traders first")

    rk = json.loads(rankings_path.read_text(encoding="utf-8"))
    top = [x["address"].lower() for x in rk.get("top_traders", [])[:top_n]]
    top_set = set(top)

    markets = load_jsonl(RAW_DIR / "markets.jsonl")
    market_by_cid = {str(m.get("condition_id")): m for m in markets if m.get("condition_id")}
    resolved_cids = {cid for cid, m in market_by_cid.items() if m.get("yes_resolved") is not None}

    # Stream trades, accumulating per (cid, wallet) positions for resolved cids only
    print("[backtest] Streaming trades...", flush=True)
    positions: dict[str, dict[str, TokenPosition]] = {}
    with (RAW_DIR / "trades.jsonl").open(encoding="utf-8") as f:
        n = 0
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except Exception:
                continue
            cid = str(t.get("conditionId") or "")
            if cid not in resolved_cids:
                continue
            w = str(t.get("proxyWallet") or "").lower()
            if w not in top_set:
                continue
            if cid not in positions:
                positions[cid] = {}
            pos = positions[cid].setdefault(w, TokenPosition())
            pos.apply_trade(
                str(t.get("side") or ""),
                str(t.get("outcome") or ""),
                float(t.get("price") or 0),
                float(t.get("size") or 0),
            )
            n += 1
            if n % 500_000 == 0:
                print(f"[backtest]   ...{n:,} trades", flush=True)

    print(f"[backtest] {n:,} trades processed", flush=True)

    # --- Strategy: consensus among top wallets (net YES inventory) ---
    consensus_preds: list[bool] = []
    consensus_actuals: list[bool] = []
    veto_skips = 0
    strength_hits: list[tuple[float, int]] = []

    for cid, meta in market_by_cid.items():
        yes_res = meta.get("yes_resolved")
        if yes_res is None:
            continue
        actual_yes = bool(yes_res)
        wmap = positions.get(cid, {})

        long_yes = 0
        short_yes = 0
        strong_no_veto = False
        score = 0.0
        for w in top:
            pos = wmap.get(w, TokenPosition())
            if pos.yes_shares >= min_yes_shares:
                long_yes += 1
            elif pos.yes_shares <= -min_yes_shares:
                short_yes += 1
            if pos.no_shares >= min_yes_shares and pos.yes_shares < 1:
                strong_no_veto = True
            score += pos.yes_shares - pos.no_shares

        strength_hits.append((score, 1 if yes_res else 0))

        active = long_yes + short_yes > 0
        if not active:
            continue

        if strong_no_veto:
            veto_skips += 1
            pred = False
        else:
            pred = long_yes > short_yes

        consensus_preds.append(pred)
        consensus_actuals.append(actual_yes)

    def accuracy(pred: list[bool], act: list[bool]) -> float:
        if not pred:
            return 0.0
        return sum(1 for a, b in zip(pred, act) if a == b) / len(pred)

    strength_hits.sort(key=lambda x: x[0])

    def bucket_accuracy(quartile: int) -> float:
        if not strength_hits:
            return 0.0
        n = len(strength_hits)
        chunk = max(1, n // 4)
        start = min(quartile * chunk, n - 1)
        end = min((quartile + 1) * chunk, n)
        sl = strength_hits[start:end]
        if not sl:
            return 0.0
        return sum(1 for sc, y in sl if (sc > 0) == bool(y)) / len(sl)

    out: dict[str, Any] = {
        "top_n": top_n,
        "n_consensus_markets": len(consensus_preds),
        "consensus_accuracy": round(accuracy(consensus_preds, consensus_actuals), 4),
        "veto_skips": veto_skips,
        "score_quadrant_accuracy": [round(bucket_accuracy(i), 4) for i in range(4)],
        "note": "Consensus predicts YES when more top wallets end net-long YES than net-short; veto uses large NO token inventory proxy.",
    }

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--min-yes-shares", type=float, default=1.0)
    args = ap.parse_args()
    o = run_backtest(top_n=args.top, min_yes_shares=args.min_yes_shares)
    print(json.dumps(o, indent=2))


if __name__ == "__main__":
    main()
