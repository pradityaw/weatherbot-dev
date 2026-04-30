"""
Continuous monitor for top Polymarket weather traders.

Polls each top wallet on an interval, captures new weather-market trades,
cross-references with the bot's active markets, and appends structured
records under data/trader_research/live/ for later analysis or live integration.

Usage:
  .venv/bin/python -m trader_research.monitor                    # default settings
  .venv/bin/python -m trader_research.monitor --top 25 --interval 120
  .venv/bin/python -m trader_research.monitor --once             # single pass

Outputs:
  data/trader_research/live/state.json              -- last seen ts per wallet
  data/trader_research/live/wallet_activity.jsonl   -- new trades by top wallets
  data/trader_research/live/overlap_alerts.jsonl    -- bot-vs-trader market overlap
  data/trader_research/live/monitor.log.jsonl       -- heartbeat/status events

Rankings are auto-refreshed every --refresh-hours (default 24) by re-running
the collector + ranker over a rolling window.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from trader_research.common import RESEARCH_DATA_DIR, ensure_dirs

LIVE_DIR = RESEARCH_DATA_DIR / "live"
STATE_PATH = LIVE_DIR / "state.json"
ACTIVITY_PATH = LIVE_DIR / "wallet_activity.jsonl"
ALERTS_PATH = LIVE_DIR / "overlap_alerts.jsonl"
LOG_PATH = LIVE_DIR / "monitor.log.jsonl"

RANKINGS_PATH = RESEARCH_DATA_DIR / "processed" / "rankings.json"
DATA_API = "https://data-api.polymarket.com/trades"

WEATHER_SLUG_PREFIXES = ("highest-temperature-in-",)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def load_top_wallets(n: int) -> list[dict[str, Any]]:
    if not RANKINGS_PATH.exists():
        return []
    rk = json.loads(RANKINGS_PATH.read_text(encoding="utf-8"))
    return rk.get("top_traders", [])[:n]


def fetch_recent_wallet_trades(
    wallet: str,
    *,
    since_ts: int,
    limit: int = 100,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    """Fetch wallet trades newest-first; stop when we cross since_ts."""
    out: list[dict[str, Any]] = []
    offset = 0
    for _ in range(max_pages):
        params = {"user": wallet, "limit": limit, "offset": offset}
        r = requests.get(f"{DATA_API}?{urlencode(params)}", timeout=(10, 25))
        if r.status_code != 200:
            break
        chunk = r.json()
        if not isinstance(chunk, list) or not chunk:
            break
        keep_going = True
        for t in chunk:
            ts = int(t.get("timestamp") or 0)
            if ts <= since_ts:
                keep_going = False
                continue
            out.append(t)
        if not keep_going or len(chunk) < limit:
            break
        offset += limit
        time.sleep(0.1)
    return out


def is_weather_trade(trade: dict[str, Any]) -> bool:
    slug = str(trade.get("eventSlug") or "")
    return any(slug.startswith(p) for p in WEATHER_SLUG_PREFIXES)


def load_active_bot_markets() -> list[dict[str, Any]]:
    """Return list of bot's open weather markets with their conditionIds."""
    out: list[dict[str, Any]] = []
    markets_dir = Path("data/markets")
    if not markets_dir.exists():
        return out
    for f in markets_dir.glob("*.json"):
        try:
            mkt = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if mkt.get("status") == "resolved":
            continue
        outcomes = mkt.get("all_outcomes") or []
        cids = [str(o.get("condition_id") or "") for o in outcomes if o.get("condition_id")]
        if not cids:
            continue
        out.append(
            {
                "city": mkt.get("city"),
                "city_name": mkt.get("city_name"),
                "date": mkt.get("date"),
                "condition_ids": cids,
                "position_market_id": (mkt.get("position") or {}).get("market_id"),
            }
        )
    return out


def overlap_for_trade(
    trade: dict[str, Any],
    bot_markets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    cid = str(trade.get("conditionId") or "")
    if not cid:
        return None
    for m in bot_markets:
        if cid in m["condition_ids"]:
            return m
    return None


def maybe_refresh_rankings(
    *,
    last_refresh_ts: float,
    refresh_seconds: int,
    rolling_days: int,
    cities: str | None,
) -> tuple[float, bool]:
    """Re-run collect + rank if the rankings file is older than refresh_seconds."""
    now = time.time()
    if now - last_refresh_ts < refresh_seconds:
        return last_refresh_ts, False

    today = datetime.now(timezone.utc).date()
    start = today.replace(year=today.year if today.month > 1 or rolling_days < 30 else today.year - 1)
    # Start from rolling_days ago (simple)
    from datetime import timedelta as _td

    start = today - _td(days=rolling_days)

    cmd = [
        ".venv/bin/python",
        "-m",
        "trader_research.collect",
        "--start",
        start.isoformat(),
        "--end",
        today.isoformat(),
        "--quiet",
    ]
    if cities:
        cmd.extend(["--cities", cities])

    append_jsonl(LOG_PATH, {"ts": utc_now_iso(), "event": "refresh_collect_start", "cmd": cmd})
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 60 * 6)
    append_jsonl(
        LOG_PATH,
        {
            "ts": utc_now_iso(),
            "event": "refresh_collect_done",
            "exit_code": proc.returncode,
            "stdout_tail": proc.stdout[-500:],
            "stderr_tail": proc.stderr[-500:],
        },
    )
    if proc.returncode != 0:
        return now, False

    rank_cmd = [
        ".venv/bin/python",
        "-m",
        "trader_research.rank_traders",
        "--rolling-days",
        str(rolling_days),
    ]
    proc2 = subprocess.run(rank_cmd, capture_output=True, text=True, timeout=60 * 30)
    append_jsonl(
        LOG_PATH,
        {
            "ts": utc_now_iso(),
            "event": "refresh_rank_done",
            "exit_code": proc2.returncode,
            "stdout_tail": proc2.stdout[-500:],
            "stderr_tail": proc2.stderr[-500:],
        },
    )
    return now, proc2.returncode == 0


def run_one_pass(
    *,
    top_n: int,
    state: dict[str, Any],
) -> dict[str, Any]:
    wallets = load_top_wallets(top_n)
    if not wallets:
        append_jsonl(LOG_PATH, {"ts": utc_now_iso(), "event": "no_rankings"})
        return {"wallets": 0, "new_trades": 0, "weather_trades": 0, "overlaps": 0}

    bot_markets = load_active_bot_markets()
    new_trades = 0
    weather_trades = 0
    overlaps = 0

    wallet_state = state.setdefault("wallets", {})
    for w in wallets:
        addr = str(w.get("address") or "").lower()
        if not addr:
            continue
        last_seen = int(wallet_state.get(addr, {}).get("last_seen_ts") or 0)
        try:
            trades = fetch_recent_wallet_trades(addr, since_ts=last_seen)
        except Exception as e:
            append_jsonl(
                LOG_PATH,
                {"ts": utc_now_iso(), "event": "fetch_error", "wallet": addr, "error": str(e)},
            )
            continue

        if not trades:
            continue

        max_ts = last_seen
        for t in trades:
            ts = int(t.get("timestamp") or 0)
            if ts > max_ts:
                max_ts = ts
            new_trades += 1
            row = {
                "ts": utc_now_iso(),
                "wallet": addr,
                "wallet_rank_pnl": w.get("total_pnl"),
                "wallet_markets_traded": w.get("markets_traded"),
                "trade_ts": ts,
                "trade_dt": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
                "side": t.get("side"),
                "outcome": t.get("outcome"),
                "price": t.get("price"),
                "size": t.get("size"),
                "condition_id": t.get("conditionId"),
                "event_slug": t.get("eventSlug"),
                "title": t.get("title"),
                "is_weather": is_weather_trade(t),
                "tx_hash": t.get("transactionHash"),
            }
            if row["is_weather"]:
                weather_trades += 1
                append_jsonl(ACTIVITY_PATH, row)
                ov = overlap_for_trade(t, bot_markets)
                if ov:
                    overlaps += 1
                    append_jsonl(
                        ALERTS_PATH,
                        {
                            **row,
                            "event": "trader_bot_overlap",
                            "bot_city": ov["city"],
                            "bot_date": ov["date"],
                            "bot_position_market_id": ov.get("position_market_id"),
                        },
                    )

        wallet_state[addr] = {
            "last_seen_ts": max_ts,
            "last_poll_ts": utc_now_iso(),
            "rank_pnl": w.get("total_pnl"),
        }

    state["wallets"] = wallet_state
    state["last_pass_ts"] = utc_now_iso()
    state["last_pass_summary"] = {
        "wallets": len(wallets),
        "new_trades": new_trades,
        "weather_trades": weather_trades,
        "overlaps": overlaps,
    }
    save_state(state)
    return state["last_pass_summary"]


_stop = False


def _handle_sigterm(signum, frame):
    global _stop
    _stop = True


def main() -> None:
    ensure_dirs()
    LIVE_DIR.mkdir(parents=True, exist_ok=True)

    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=int(os.getenv("TRADER_MONITOR_TOP", "25")))
    ap.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("TRADER_MONITOR_INTERVAL", "180")),
        help="Seconds between polls",
    )
    ap.add_argument(
        "--refresh-hours",
        type=float,
        default=float(os.getenv("TRADER_MONITOR_REFRESH_HOURS", "24")),
        help="How often to re-collect + re-rank wallets",
    )
    ap.add_argument(
        "--rolling-days",
        type=int,
        default=int(os.getenv("TRADER_MONITOR_ROLLING_DAYS", "120")),
        help="Lookback window for the ranking refresh",
    )
    ap.add_argument(
        "--refresh-cities",
        default=os.getenv("TRADER_MONITOR_REFRESH_CITIES") or None,
        help="Comma-separated city slugs for the periodic refresh (default: bot LOCATIONS)",
    )
    ap.add_argument("--once", action="store_true", help="Run a single pass and exit")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _handle_sigterm)
    signal.signal(signal.SIGTERM, _handle_sigterm)

    state = load_state()
    last_refresh = float(state.get("last_refresh_ts") or 0.0)
    refresh_seconds = int(args.refresh_hours * 3600)

    append_jsonl(
        LOG_PATH,
        {
            "ts": utc_now_iso(),
            "event": "monitor_start",
            "top": args.top,
            "interval": args.interval,
            "refresh_hours": args.refresh_hours,
            "rolling_days": args.rolling_days,
            "once": bool(args.once),
        },
    )

    try:
        while not _stop:
            last_refresh, refreshed = maybe_refresh_rankings(
                last_refresh_ts=last_refresh,
                refresh_seconds=refresh_seconds,
                rolling_days=args.rolling_days,
                cities=args.refresh_cities,
            )
            if refreshed:
                state["last_refresh_ts"] = last_refresh
                save_state(state)

            summary = run_one_pass(top_n=args.top, state=state)
            append_jsonl(
                LOG_PATH,
                {"ts": utc_now_iso(), "event": "pass_done", **summary},
            )
            print(f"[monitor] {utc_now_iso()} {summary}", flush=True)

            if args.once:
                break

            for _ in range(args.interval):
                if _stop:
                    break
                time.sleep(1)
    finally:
        append_jsonl(LOG_PATH, {"ts": utc_now_iso(), "event": "monitor_stop"})


if __name__ == "__main__":
    main()
