"""
Collect Polymarket weather event metadata and per-bucket trade history.

Usage:
  .venv/bin/python -m trader_research.collect --start 2025-01-01 --end 2025-01-31 --cities nyc
  .venv/bin/python -m trader_research.collect --start 2025-01-01 --end 2025-01-31  # all bot cities
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Import bot city list without loading config (bot_v2 loads config at import)
from trader_research import common
from trader_research.common import (
    RAW_DIR,
    daterange,
    ensure_dirs,
    event_slug_for_date,
    fetch_all_trades_for_condition,
    fetch_event_by_slug,
    parse_iso_date,
    yes_won_from_market_payload,
)

try:
    from bot_v2 import LOCATIONS
except ImportError:
    LOCATIONS = {}

DEFAULT_CITIES = list(LOCATIONS.keys()) if LOCATIONS else [
    "nyc",
    "chicago",
    "miami",
    "london",
]


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_seen_trade_keys(path: Path) -> set[str]:
    seen: set[str] = set()
    if not path.exists():
        return seen
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            k = row.get("_dedupe_key")
            if k:
                seen.add(str(k))
        except Exception:
            continue
    return seen


def trade_dedupe_key(t: dict[str, Any], condition_id: str) -> str:
    return "|".join(
        [
            condition_id,
            str(t.get("transactionHash", "")),
            str(t.get("proxyWallet", "")),
            str(t.get("timestamp", "")),
            str(t.get("side", "")),
            str(t.get("outcome", "")),
            str(t.get("price", "")),
            str(t.get("size", "")),
        ]
    )


def run_collect(
    start: date,
    end: date,
    cities: list[str],
    *,
    trades_path: Path | None = None,
    markets_path: Path | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    ensure_dirs()
    trades_path = trades_path or RAW_DIR / "trades.jsonl"
    markets_path = markets_path or RAW_DIR / "markets.jsonl"

    seen = load_seen_trade_keys(trades_path)
    manifest: dict[str, Any] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cities": cities,
        "markets_written": 0,
        "trades_written": 0,
        "trades_skipped_dup": 0,
        "events_missing": 0,
    }

    for city in cities:
        for d in daterange(start, end):
            slug = event_slug_for_date(city, d)
            event = fetch_event_by_slug(slug)
            if not event:
                manifest["events_missing"] += 1
                if not quiet:
                    print(f"[skip] no event {slug}")
                continue

            event_meta = {
                "event_slug": slug,
                "city_slug": city,
                "date": d.isoformat(),
                "title": event.get("title"),
                "end_date": event.get("endDate"),
            }

            markets = event.get("markets") or []
            for m in markets:
                if not isinstance(m, dict):
                    continue
                mid = str(m.get("id", ""))
                cid = str(m.get("conditionId", "") or "")
                if not cid:
                    continue

                yes_won = yes_won_from_market_payload(m)
                record = {
                    **event_meta,
                    "event_end_date": event.get("endDate"),
                    "market_id": mid,
                    "condition_id": cid,
                    "question": m.get("question"),
                    "volume": float(m.get("volume") or 0),
                    "closed": bool(m.get("closed")),
                    "yes_resolved": yes_won,
                }
                append_jsonl(markets_path, record)
                manifest["markets_written"] += 1

                try:
                    trades = fetch_all_trades_for_condition(cid)
                except Exception as e:
                    if not quiet:
                        print(f"[warn] trades failed {cid}: {e}", file=sys.stderr)
                    continue

                for t in trades:
                    if not isinstance(t, dict):
                        continue
                    dk = trade_dedupe_key(t, cid)
                    if dk in seen:
                        manifest["trades_skipped_dup"] += 1
                        continue
                    seen.add(dk)
                    row = dict(t)
                    row["_dedupe_key"] = dk
                    row["_city_slug"] = city
                    row["_target_date"] = d.isoformat()
                    row["_market_id_gamma"] = mid
                    append_jsonl(trades_path, row)
                    manifest["trades_written"] += 1

    out_manifest = RAW_DIR / "collection_manifest.json"
    out_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="Collect weather Polymarket trades into data/trader_research/raw/")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    ap.add_argument(
        "--cities",
        default=",".join(DEFAULT_CITIES),
        help="Comma-separated city slugs (default: all LOCATIONS from bot_v2)",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    start = parse_iso_date(args.start)
    end = parse_iso_date(args.end)
    cities = [c.strip() for c in args.cities.split(",") if c.strip()]

    m = run_collect(start, end, cities, quiet=args.quiet)
    print(json.dumps(m, indent=2))


if __name__ == "__main__":
    main()
