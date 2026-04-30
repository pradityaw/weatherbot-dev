#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Summarize data/precip_log.jsonl for go/no-go after the 24h paper window."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "data" / "precip_log.jsonl"


def _parse_ts(line: dict) -> datetime | None:
    raw = line.get("ts") or ""
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def load_entries_since(hours: float = 24.0) -> list[dict]:
    if not LOG_PATH.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: list[dict] = []
    for ln in LOG_PATH.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
        except Exception:
            continue
        ts = _parse_ts(row)
        if ts is None or ts < cutoff:
            continue
        out.append(row)
    return out


def edge_histogram(evs: list[float]) -> dict[str, int]:
    h = {"0-0.05": 0, "0.05-0.10": 0, "0.10-0.15": 0, "0.15-0.20": 0, "0.20+": 0}
    for ev in evs:
        if ev < 0.05:
            h["0-0.05"] += 1
        elif ev < 0.10:
            h["0.05-0.10"] += 1
        elif ev < 0.15:
            h["0.10-0.15"] += 1
        elif ev < 0.20:
            h["0.15-0.20"] += 1
        else:
            h["0.20+"] += 1
    return h


def summarize(hours: float = 24.0) -> dict:
    entries = load_entries_since(hours=hours)
    if not entries:
        return {
            "hours": hours,
            "count": 0,
            "signals_per_hour": 0.0,
            "mean_ev": None,
            "median_ev": None,
            "mean_spread": None,
            "side_mix": {},
            "per_city": {},
            "would_pass_gateway": 0,
            "histogram": {},
        }

    evs = [float(e.get("ev") or 0) for e in entries]
    spreads = [float(e.get("spread") or 0) for e in entries if e.get("spread") is not None]
    sides = [str(e.get("side") or "?") for e in entries]
    cities = [str(e.get("city") or "?") for e in entries]
    wp = sum(1 for e in entries if e.get("would_pass_gateway"))

    hist = edge_histogram(evs)
    duration_h = max(hours, 1e-6)

    return {
        "hours": hours,
        "count": len(entries),
        "signals_per_hour": round(len(entries) / duration_h, 4),
        "mean_ev": round(statistics.mean(evs), 4) if evs else None,
        "median_ev": round(statistics.median(evs), 4) if evs else None,
        "mean_spread": round(statistics.mean(spreads), 4) if spreads else None,
        "side_mix": dict(Counter(sides)),
        "per_city": dict(Counter(cities)),
        "would_pass_gateway": wp,
        "histogram": hist,
    }


def print_summary(hours: float = 24.0) -> None:
    s = summarize(hours=hours)
    print(f"precip_log.jsonl — last {s['hours']}h")
    print(f"  entries:           {s['count']}")
    print(f"  signals/hour:      {s['signals_per_hour']}")
    print(f"  mean_ev:           {s.get('mean_ev')}")
    print(f"  median_ev:         {s.get('median_ev')}")
    print(f"  mean_spread:       {s.get('mean_spread')}")
    print(f"  would_pass_gateway:{s['would_pass_gateway']}")
    print(f"  side_mix:          {s['side_mix']}")
    print(f"  per_city (top):    {dict(list(sorted(s['per_city'].items(), key=lambda x: -x[1]))[:12])}")
    print(f"  ev_histogram:      {s['histogram']}")


def main() -> int:
    print_summary(hours=24.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
