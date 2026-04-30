from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

# Matches bot_v2.get_polymarket_event slug pattern
MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
DATA_API_TRADES_URL = "https://data-api.polymarket.com/trades"

RESEARCH_DATA_DIR = Path("data") / "trader_research"
RAW_DIR = RESEARCH_DATA_DIR / "raw"


def ensure_dirs() -> None:
    RESEARCH_DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)


def event_slug_for_date(city_slug: str, d: date) -> str:
    month_name = MONTHS[d.month - 1]
    return f"highest-temperature-in-{city_slug}-on-{month_name}-{d.day}-{d.year}"


def fetch_json(url: str, params: dict[str, Any] | None = None, timeout: tuple[int, int] = (10, 25)) -> Any:
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_event_by_slug(slug: str) -> dict[str, Any] | None:
    data = fetch_json(GAMMA_EVENTS_URL, params={"slug": slug})
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        return data[0]
    return None


def yes_won_from_market_payload(market: dict[str, Any]) -> bool | None:
    """Binary YES token resolved ~1 => True, ~0 => False; open => None."""
    if not market.get("closed"):
        return None
    try:
        raw = market.get("outcomePrices", "[0.5,0.5]")
        if isinstance(raw, str):
            prices = json.loads(raw)
        else:
            prices = raw
        yes_price = float(prices[0])
        if yes_price >= 0.95:
            return True
        if yes_price <= 0.05:
            return False
    except Exception:
        return None
    return None


def fetch_all_trades_for_condition(
    condition_id: str,
    *,
    limit: int = 500,
    sleep_s: float = 0.08,
) -> list[dict[str, Any]]:
    """Paginate data-api trades for one condition id (hex string)."""
    all_rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {"market": condition_id, "limit": limit, "offset": offset}
        url = f"{DATA_API_TRADES_URL}?{urlencode(params)}"
        chunk = fetch_json(url)
        if not isinstance(chunk, list) or len(chunk) == 0:
            break
        all_rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
        time.sleep(sleep_s)
    return all_rows


def daterange(start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def parse_iso_date(s: str) -> date:
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()
