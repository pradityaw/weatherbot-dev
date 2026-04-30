#!/usr/bin/env python3
"""Smoke-test Open-Meteo vs Visual Crossing realized precip (precip_engine secondary gate).

Loads `.env.weatherbot` before importing `precip_engine` so `WEATHERBOT_VC_KEY` is picked up.
Run from repo root:

  ./.venv/bin/python check_precip_secondary.py
  # or: source .env.weatherbot && ./.venv/bin/python check_precip_secondary.py

Exit codes: 0 = VC configured and check ran; 1 = missing key or fatal error.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
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

    # Import after env is populated
    from precip_engine import (  # noqa: E402
        PRECIP_LOCATIONS,
        fetch_realized_precip_open_meteo,
        fetch_realized_precip_visual_crossing,
        secondary_precip_check,
        vc_key_configured,
    )

    if not vc_key_configured():
        print(
            "FAIL: No valid WEATHERBOT_VC_KEY in .env.weatherbot (or config.json vc_key).\n"
            "Add your Visual Crossing key, then restart the launchd agent:\n"
            "  launchctl kickstart -k gui/$(id -u)/com.dubski.weatherbot"
        )
        return 1

    # Seoul proxy — same lat/lon as PRECIP_LOCATIONS["seoul"]
    lat, lon, label, _src = PRECIP_LOCATIONS["seoul"]
    today = datetime.now(timezone.utc).date()
    # Month-to-date realized window (same idea as monthly markets: start of month .. yesterday)
    start_d = date(today.year, today.month, 1)
    end_d = today - timedelta(days=1)
    if end_d < start_d:
        print("No realized window yet this month.")
        return 0

    om_in, _om_raw = fetch_realized_precip_open_meteo(lat, lon, start_d, end_d, snow=False)
    vc_in, _vc_raw, vc_err = fetch_realized_precip_visual_crossing(lat, lon, start_d, end_d, snow=False)
    check = secondary_precip_check(om_in, vc_in, vc_err)

    print("Paper Trading Watch — precip secondary-source check")
    print(f"  Location: {label} ({lat}, {lon})")
    print(f"  Window:   {start_d} .. {end_d} (realized MTD through yesterday)")
    print(f"  Open-Meteo archive: {check['open_meteo_inches']:.4f} in ({check['open_meteo_inches'] * 25.4:.1f} mm)")
    if vc_in is not None:
        print(f"  Visual Crossing:    {check['visual_crossing_inches']:.4f} in ({check['visual_crossing_inches'] * 25.4:.1f} mm)")
    else:
        print(f"  Visual Crossing:    (unavailable: {vc_err})")
    print(f"  Diff: {check.get('diff_inches')} in (max allowed {check['max_diff_inches']} in)")
    print(f"  Status: {check['status']} | ok for gateway: {check['ok']}")
    print(json.dumps(check, indent=2))

    if not check["ok"]:
        print(
            "\nNOTE: If sources disagree, precip signals will not pass would_pass_gateway "
            "(when WEATHERBOT_PRECIP_SECONDARY_REQUIRED=true)."
        )
        return 0

    print("\nOK: Secondary sources agree within tolerance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
