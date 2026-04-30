#!/usr/bin/env python3
"""Verify environment for Paper Trading Watch: paper mode, VC key, optional launchd hint.

Exit 0 if paper intent is set; still exit 0 if only warnings (e.g. key missing) — this is a report, not a hard gate.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key:
            os.environ[key] = val


def main() -> int:
    _load_env_file(ROOT / ".env.weatherbot")
    _load_env_file(ROOT / ".env.weatherbot-live")

    print("Paper Trading Watch — environment check\n")

    live = os.getenv("WEATHERBOT_LIVE_TRADING", "0").strip().lower() in {"1", "true", "yes", "on"}
    dry = os.getenv("WEATHERBOT_DRY_RUN_LIVE", "1").strip().lower() in {"1", "true", "yes", "on"}
    if live and not dry:
        print("WARN: WEATHERBOT_LIVE_TRADING is on and WEATHERBOT_DRY_RUN_LIVE is off.")
        print("      Intended paper-watch observation should use live trading OFF or dry-run ON.")
    else:
        print("OK:   Live trading is not in full send mode (paper-friendly).")

    from precip_engine import vc_key_configured  # noqa: E402

    if vc_key_configured():
        print("OK:   WEATHERBOT_VC_KEY is set (Visual Crossing for temp actuals + precip cross-check).")
    else:
        print("FAIL: WEATHERBOT_VC_KEY missing or placeholder — precip secondary gate will block pass signals.")
        print("      Add a real key to .env.weatherbot and restart: launchctl kickstart -k gui/$(id -u)/com.dubski.weatherbot")

    print()
    print("Observation window: run 24–72h in paper mode, then run:")
    print("  ./paper_watch_review.py   (or: python paper_watch_review.py)")
    print()
    print("Optional — launchd job (macOS):")
    try:
        uid = str(os.getuid())
        r = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/com.dubski.weatherbot"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and "state = not running" not in (r.stdout or ""):
            print("launchd: com.dubski.weatherbot is known to launchctl (see `launchctl print` for state).")
        else:
            print("launchd: could not confirm job (not macOS, or agent not loaded).")
    except Exception:
        print("launchd: skip (not available).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
