#!/usr/bin/env python3
"""Daily review bundle for Paper Trading Watch (status + precip quality + live ledger hints).

Run from repo root:

  ./.venv/bin/python paper_watch_review.py

Optional: `source .env.weatherbot` first so live-status reflects your env.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def _py(args: list[str]) -> str:
    exe = ROOT / ".venv" / "bin" / "python"
    if not exe.is_file():
        exe = Path(sys.executable)
    r = subprocess.run(
        [str(exe), str(ROOT / args[0])] + args[1:],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return (r.stdout or "") + (r.stderr or "")


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        t = s.replace("Z", "+00:00")
        return datetime.fromisoformat(t)
    except Exception:
        return None


def _precip_secondary_stats(hours: float = 24.0) -> dict[str, Any]:
    count: Counter[str] = Counter()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    ddir = DATA / "precip_markets"
    if not ddir.is_dir():
        return {"files": 0, "in_window": 0, "status": dict(count)}

    n_files = 0
    in_window = 0
    for p in ddir.glob("*.json"):
        n_files += 1
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        ts = _parse_ts(data.get("ts"))
        if ts is None or ts < cutoff:
            continue
        in_window += 1
        sc = data.get("secondary_check")
        if not sc:
            count["legacy_no_secondary_field"] += 1
        else:
            st = sc.get("status") or "unknown"
            count[st] += 1

    return {
        "files": n_files,
        "in_window": in_window,
        "status": dict(count),
    }


def _tail_jsonl(path: Path, n: int = 15) -> list[str]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines[-n:] if len(lines) > n else lines


def main() -> int:
    print("=" * 55)
    print("  PAPER TRADING WATCH — daily review")
    print("=" * 55)
    print()

    print("--- bot_v2 status ---")
    print(_py(["bot_v2.py", "status"]).rstrip())
    print()
    print("--- live / gateway guardrails ---")
    print(_py(["bot_v2.py", "live-status"]).rstrip())
    print()
    print("--- precip signals (summary) ---")
    print(_py(["bot_v2.py", "precip-status"]).rstrip())

    stats = _precip_secondary_stats(24.0)
    print()
    print("--- precip secondary_check (last 24h, from data/precip_markets/*.json) ---")
    print(f"  Snapshots in window (with ts): {stats['in_window']}")
    print(f"  By status: {stats['status'] or '(none)'}")
    if stats["status"].get("legacy_no_secondary_field"):
        print(
            "  Note: 'legacy_no_secondary_field' = snapshots saved before secondary_check "
            "was added or before VC key was loaded; new scans will populate agree/disagree/missing."
        )

    le = DATA / "live_events.jsonl"
    ls = DATA / "live_signals.jsonl"
    print()
    print("--- tail live_events.jsonl (last 15 lines) ---")
    for line in _tail_jsonl(le, 15):
        print(line[:500] + ("..." if len(line) > 500 else ""))
    if not le.is_file():
        print("  (file missing — normal if shadow/live logging not used yet)")

    print()
    print("--- tail live_signals.jsonl (last 10 lines) ---")
    for line in _tail_jsonl(ls, 10):
        print(line[:500] + ("..." if len(line) > 500 else ""))
    if not ls.is_file():
        print("  (file missing)")

    print()
    print("--- quick commands ---")
    print("  python bot_v2.py report")
    print("  ./check_precip_secondary.py")
    print("  ./verify_paper_watch.py")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
