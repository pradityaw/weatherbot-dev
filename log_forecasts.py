from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from bot_v1 import ACTIVE_LOCATIONS, get_forecast

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "data" / "forecasts"


def main() -> int:
    captured_at = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = captured_at.strftime("%Y%m%dT%H%M%SZ")
    failures = 0

    for city in ACTIVE_LOCATIONS:
        try:
            daily_max = get_forecast(city)
            if not daily_max:
                print(f"[warn] {city}: empty forecast, skipping write")
                failures += 1
                continue

            city_dir = OUT_DIR / city
            city_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "city": city,
                "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
                "source": "nws",
                "daily_max_f": daily_max,
            }
            tmp = city_dir / f".{stamp}.json.tmp"
            final = city_dir / f"{stamp}.json"
            tmp.write_text(json.dumps(payload, sort_keys=True))
            os.replace(tmp, final)
            print(f"[ok] {city}: {len(daily_max)} days -> {final.relative_to(ROOT)}")
        except Exception as exc:
            print(f"[err] {city}: {exc}")
            failures += 1

    return 1 if failures == len(ACTIVE_LOCATIONS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
