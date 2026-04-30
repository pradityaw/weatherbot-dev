#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Precipitation market scanner — paper signal engine (Phase 1).

Discovers rain/snow/precip Polymarket markets, scores YES vs NO using Open-Meteo
precipitation sums and normal tail probabilities, prices via CLOB bid/ask.

Does not import bot_v2 (avoid circular imports). Loads the same config.json keys.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from clob_market import (
    best_bid_ask_from_orderbook,
    get_clob_market_snapshot,
    get_clob_orderbook,
    resolve_market_token_id,
)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
PRECIP_MARKETS_DIR = DATA_DIR / "precip_markets"
PRECIP_LOG = DATA_DIR / "precip_log.jsonl"
FORECASTS_PRECIP_DIR = DATA_DIR / "forecasts_precip"

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
GAMMA_MARKET_URL = "https://gamma-api.polymarket.com/markets/{market_id}"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
VISUAL_CROSSING_TIMELINE = (
    "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
)

VC_KEY = os.getenv("WEATHERBOT_VC_KEY", "")
PLACEHOLDER_VC_KEYS = frozenset({"", "YOUR_KEY_HERE", "YOUR_VISUAL_CROSSING_KEY"})
PRECIP_SECONDARY_REQUIRED = os.getenv(
    "WEATHERBOT_PRECIP_SECONDARY_REQUIRED", "true"
).lower() in {"1", "true", "yes", "on"}
PRECIP_SECONDARY_MAX_DIFF_IN = float(
    os.getenv("WEATHERBOT_PRECIP_SECONDARY_MAX_DIFF_IN", "0.10")
)

_MONTHS = (
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
)

# Prefer explicit city mappings over free-form geocoding for markets we trade.
# Source is logged so we can later replace proxies with exact resolution stations.
PRECIP_LOCATIONS = {
    "nyc": (40.7128, -74.0060, "New York City", "open_meteo_city_proxy"),
    "new york city": (40.7128, -74.0060, "New York City", "open_meteo_city_proxy"),
    "new york": (40.7128, -74.0060, "New York City", "open_meteo_city_proxy"),
    "seattle": (47.6062, -122.3321, "Seattle", "open_meteo_city_proxy"),
    "london": (51.5072, -0.1276, "London", "open_meteo_city_proxy"),
    "seoul": (37.5665, 126.9780, "Seoul", "open_meteo_city_proxy"),
    "hong kong": (22.3193, 114.1694, "Hong Kong", "open_meteo_city_proxy"),
}

# Word boundaries: avoid substring hits like "Uk**rain**e"
_PRECIP_RE = re.compile(
    r"\b(?:rainfall|precipitation|snowfall|\brain\b|\bsnow\b|inches?\s+of\s+rain|mm\s+of\s+rain)\b",
    re.IGNORECASE,
)


def vc_key_configured() -> bool:
    k = str(VC_KEY).strip()
    return bool(k and k not in PLACEHOLDER_VC_KEYS)


def _load_cfg() -> dict[str, Any]:
    p = ROOT / "config.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


_cfg = _load_cfg()
if not VC_KEY:
    VC_KEY = str(_cfg.get("vc_key", ""))
MIN_EV = float(_cfg.get("min_ev", 0.10))
MAX_PRICE = float(_cfg.get("max_price", 0.45))
MAX_SLIPPAGE = float(_cfg.get("max_slippage", 0.03))
MIN_VOLUME = float(_cfg.get("min_volume", 500))
MIN_HOURS = float(_cfg.get("min_hours", 2.0))
MAX_HOURS = float(_cfg.get("max_hours", 72.0))
MAX_BET = float(_cfg.get("max_bet", 20.0))
KELLY_FRACTION = float(_cfg.get("kelly_fraction", 0.25))
BALANCE = float(_cfg.get("balance", 10000.0))


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _parse_temp_range(question: str) -> tuple[float, float] | None:
    """Same logic as bot_v2.parse_temp_range — exclude temperature buckets."""
    if not question:
        return None
    num = r"(-?\d+(?:\.\d+)?)"
    if re.search(r"or below", question, re.IGNORECASE):
        m = re.search(num + r"[°]?[FC] or below", question, re.IGNORECASE)
        if m:
            return (-999.0, float(m.group(1)))
    if re.search(r"or higher", question, re.IGNORECASE):
        m = re.search(num + r"[°]?[FC] or higher", question, re.IGNORECASE)
        if m:
            return (float(m.group(1)), 999.0)
    m = re.search(r"between " + num + r"-" + num + r"[°]?[FC]", question, re.IGNORECASE)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.search(r"be " + num + r"[°]?[FC] on", question, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None


def hours_to_resolution(end_date_str: str) -> float:
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 3600)
    except Exception:
        return 999.0


def _mm_to_inches(mm: float) -> float:
    return mm * 0.0393701


def probability_exceed(mean_inches: float, threshold_inches: float, sigma_inches: float) -> float:
    """P(total > threshold) with Gaussian uncertainty on cumulative total."""
    if sigma_inches <= 0:
        sigma_inches = 0.01
    z = (threshold_inches - mean_inches) / sigma_inches
    return max(0.0, min(1.0, 1.0 - _norm_cdf(z)))


def probability_below(mean_inches: float, threshold_inches: float, sigma_inches: float) -> float:
    if sigma_inches <= 0:
        sigma_inches = 0.01
    z = (threshold_inches - mean_inches) / sigma_inches
    return max(0.0, min(1.0, _norm_cdf(z)))


def probability_in_range(
    mean_inches: float, low_inches: float, high_inches: float, sigma_inches: float
) -> float:
    """P(low <= total <= high)."""
    if sigma_inches <= 0:
        sigma_inches = 0.01
    if low_inches > high_inches:
        low_inches, high_inches = high_inches, low_inches
    z_hi = (high_inches - mean_inches) / sigma_inches
    z_lo = (low_inches - mean_inches) / sigma_inches
    p = _norm_cdf(z_hi) - _norm_cdf(z_lo)
    return max(0.0, min(1.0, p))


def calc_ev(p: float, price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)


def calc_kelly(p: float, price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return round(min(max(0.0, f) * KELLY_FRACTION, 1.0), 4)


def bet_size(kelly: float, balance: float) -> float:
    raw = kelly * balance
    return round(min(raw, MAX_BET), 2)


def _is_precip_candidate(question: str) -> bool:
    if _parse_temp_range(question):
        return False
    return _PRECIP_RE.search(question) is not None


@dataclass
class ParsedPrecip:
    mode: str  # "range" | "above" | "below"
    low_inches: float | None
    high_inches: float | None
    threshold_inches: float | None
    window_label: str
    start_d: date
    end_d: date
    realized_start_d: date | None
    realized_end_d: date | None
    snow: bool
    city_query: str


def _year_from_end_date(end_date_iso: str | None) -> int:
    if not end_date_iso:
        return datetime.now(timezone.utc).year
    try:
        return datetime.fromisoformat(end_date_iso.replace("Z", "+00:00")).year
    except Exception:
        return datetime.now(timezone.utc).year


def _parse_month_in_question(question: str, end_date_iso: str | None) -> tuple[int, int] | None:
    """Returns (year, month) e.g. April from 'in April' / 'April 2026'."""
    q = question.lower()
    for i, name in enumerate(_MONTHS, start=1):
        if re.search(rf"\b{name}\b", q):
            m = re.search(rf"{name}\s+(\d{{4}})", q)
            year = int(m.group(1)) if m else _year_from_end_date(end_date_iso)
            return year, i
    return None


def _is_temperature_only_question(question: str) -> bool:
    """Exclude daily/hi/lo temp buckets; keep precip/rain/snow/inches/mm markets."""
    ql = question.lower()
    if any(
        x in ql
        for x in (
            "precipitation",
            "rain",
            "snow",
            "inches of",
            "inch of",
            "mm of",
            "millimeters",
        )
    ):
        return False
    if _parse_temp_range(question):
        return True
    if "temperature" in ql or "lowest temperature" in ql or "highest temperature" in ql:
        return True
    if "°c" in ql or "°f" in ql or "ºc" in ql or "ºf" in ql:
        return True
    return False


def _extract_city_query(question: str) -> str | None:
    m = re.match(r"Will\s+(.+?)\s+have\b", question, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip("'\"")

    m = re.match(r"Will\s+(.+?)\s+get\b", question, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip("'\"")

    m = re.search(
        r"(?:rainfall|precipitation|snow)\s+in\s+([A-Za-z][A-Za-z\s\-\']+?)(?:\s+in\s+\d{4}|\s+for|\s+on|\?|$)",
        question,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()

    m = re.search(r"in\s+([A-Za-z][A-Za-z\s\-]+?)\s+(?:get|receive|have)", question, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    return None


def parse_precip_market(question: str, end_date_iso: str | None) -> ParsedPrecip | None:
    """
    Parse bucket markets: range (in/mm), above/below single threshold, monthly windows.
    """
    city = _extract_city_query(question)
    if not city:
        return None

    q_lower = question.lower()
    snow = "snow" in q_lower or "snowfall" in q_lower

    mode = "above"
    low_in: float | None = None
    high_in: float | None = None
    threshold: float | None = None

    m_rng_in = re.search(
        r"between\s+(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)\s*(?:inches|inch)\b",
        question,
        re.IGNORECASE,
    )
    m_rng_mm = re.search(
        r"between\s+(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*mm\b",
        question,
        re.IGNORECASE,
    )
    if m_rng_in:
        mode = "range"
        low_in = float(m_rng_in.group(1))
        high_in = float(m_rng_in.group(2))
    elif m_rng_mm:
        mode = "range"
        low_in = _mm_to_inches(float(m_rng_mm.group(1)))
        high_in = _mm_to_inches(float(m_rng_mm.group(2)))
    else:
        inch_one = re.search(
            r"(?:more than|at least|over|above|greater than)\s+(\d+(?:\.\d+)?)\s*(?:inches|inch)\b",
            question,
            re.IGNORECASE,
        )
        inch_under = re.search(
            r"(?:less than|fewer than|under|below)\s+(\d+(?:\.\d+)?)\s*(?:inches|inch)\b",
            question,
            re.IGNORECASE,
        )
        mm_one = re.search(
            r"(?:more than|at least|over|above|or more)\s+(\d+(?:\.\d+)?)\s*mm\b",
            question,
            re.IGNORECASE,
        )
        mm_under = re.search(
            r"(?:less than|fewer than|under|below)\s+(\d+(?:\.\d+)?)\s*mm\b",
            question,
            re.IGNORECASE,
        )
        mm_plain = re.search(r"(\d+(?:\.\d+)?)\s*mm\s+or\s+more\b", question, re.IGNORECASE)
        if inch_one:
            mode = "above"
            threshold = float(inch_one.group(1))
        elif inch_under:
            mode = "below"
            threshold = float(inch_under.group(1))
        elif mm_one:
            mode = "above"
            threshold = _mm_to_inches(float(mm_one.group(1)))
        elif mm_under:
            mode = "below"
            threshold = _mm_to_inches(float(mm_under.group(1)))
        elif mm_plain:
            mode = "above"
            threshold = _mm_to_inches(float(mm_plain.group(1)))
        else:
            inch_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:inches|inch)\b", question, re.IGNORECASE)
            mm_m = re.search(r"(\d+(?:\.\d+)?)\s*mm\b", question, re.IGNORECASE)
            if inch_m:
                threshold = float(inch_m.group(1))
                if re.search(r"(less than|fewer than|under|below|at most)", q_lower):
                    mode = "below"
                elif re.search(r"(more than|at least|over|above|exceed|greater than)", q_lower):
                    mode = "above"
                else:
                    return None
            elif mm_m:
                threshold = _mm_to_inches(float(mm_m.group(1)))
                if re.search(r"(less than|fewer than|under|below|at most)", q_lower):
                    mode = "below"
                elif re.search(r"(more than|at least|over|above|exceed|greater than)", q_lower):
                    mode = "above"
                else:
                    return None
            else:
                return None

    today = datetime.now(timezone.utc).date()
    realized_start_d: date | None = None
    realized_end_d: date | None = None
    my = _parse_month_in_question(question, end_date_iso)
    if my:
        year, month = my
        start_d = date(year, month, 1)
        month_start = start_d
        if month == 12:
            end_d = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_d = date(year, month + 1, 1) - timedelta(days=1)
        realized_start_d = month_start
        realized_end_d = min(today - timedelta(days=1), end_d)
        if realized_end_d < realized_start_d:
            realized_start_d = None
            realized_end_d = None
        window_label = f"{_MONTHS[month - 1]}_{year}_total"
    else:
        if end_date_iso:
            try:
                end_dt = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
                end_d = end_dt.date()
                start_d = end_d
                window_label = "single_day"
            except Exception:
                start_d = today
                end_d = today + timedelta(days=1)
                window_label = "fallback_1d"
        else:
            start_d = today
            end_d = today + timedelta(days=1)
            window_label = "fallback_1d"

    if start_d < today:
        start_d = today

    return ParsedPrecip(
        mode=mode,
        low_inches=low_in,
        high_inches=high_in,
        threshold_inches=threshold,
        window_label=window_label,
        start_d=start_d,
        end_d=end_d,
        realized_start_d=realized_start_d,
        realized_end_d=realized_end_d,
        snow=snow,
        city_query=city,
    )


def geocode_city(name: str) -> tuple[float, float, str] | None:
    try:
        r = requests.get(
            GEOCODE_URL,
            params={"name": name, "count": 1, "language": "en"},
            timeout=(3, 8),
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        hit = results[0]
        lat = float(hit["latitude"])
        lon = float(hit["longitude"])
        label = hit.get("name", name)
        return lat, lon, label
    except Exception:
        return None


def resolve_precip_location(name: str) -> tuple[float, float, str, str] | None:
    key = re.sub(r"\s+", " ", name.strip().lower())
    if key in PRECIP_LOCATIONS:
        return PRECIP_LOCATIONS[key]
    geo = geocode_city(name)
    if not geo:
        return None
    lat, lon, label = geo
    return lat, lon, label, "open_meteo_geocode_proxy"


def fetch_precip_forecast_open_meteo(
    lat: float,
    lon: float,
    start_d: date,
    end_d: date,
    snow: bool,
) -> tuple[float, dict[str, Any]] | None:
    """Returns (total_inches, raw_payload) for daily sum over [start_d, end_d]."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_d.isoformat(),
        "end_date": end_d.isoformat(),
        "daily": "snowfall_sum,precipitation_sum",
        "timezone": "auto",
    }
    try:
        r = requests.get(OPEN_METEO, params=params, timeout=(5, 15))
        if r.status_code >= 400:
            return None
        payload = r.json()
        daily = payload.get("daily") or {}
        if snow:
            series = daily.get("snowfall_sum") or []
            # Open-Meteo snowfall_sum is centimeters
            total_cm = sum(float(x) for x in series if x is not None)
            total_in = total_cm / 2.54
        else:
            series = daily.get("precipitation_sum") or []
            total_mm = sum(float(x) for x in series if x is not None)
            total_in = _mm_to_inches(total_mm)
        if not series:
            return None
        return total_in, payload
    except Exception:
        return None


def fetch_realized_precip_open_meteo(
    lat: float,
    lon: float,
    start_d: date | None,
    end_d: date | None,
    snow: bool,
) -> tuple[float, dict[str, Any] | None]:
    """Month-to-date realized total from Open-Meteo archive, in inches."""
    if start_d is None or end_d is None or end_d < start_d:
        return 0.0, None
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_d.isoformat(),
        "end_date": end_d.isoformat(),
        "daily": "snowfall_sum,precipitation_sum",
        "timezone": "auto",
    }
    try:
        r = requests.get(OPEN_METEO_ARCHIVE, params=params, timeout=(5, 20))
        if r.status_code >= 400:
            return 0.0, None
        payload = r.json()
        daily = payload.get("daily") or {}
        if snow:
            series = daily.get("snowfall_sum") or []
            total_in = sum(float(x) for x in series if x is not None) / 2.54
        else:
            series = daily.get("precipitation_sum") or []
            total_in = _mm_to_inches(sum(float(x) for x in series if x is not None))
        return total_in, payload
    except Exception:
        return 0.0, None


def fetch_realized_precip_visual_crossing(
    lat: float,
    lon: float,
    start_d: date | None,
    end_d: date | None,
    snow: bool,
) -> tuple[float | None, dict[str, Any] | None, str | None]:
    """Secondary realized total from Visual Crossing, in inches.

    Visual Crossing is only used as an independent cross-check for realized
    precipitation. Snow markets stay single-source until units/station behavior
    is verified for the markets we care about.
    """
    if not vc_key_configured():
        return None, None, "vc_key_missing"
    if snow:
        return None, None, "vc_snow_crosscheck_not_enabled"
    if start_d is None or end_d is None or end_d < start_d:
        return None, None, "no_realized_window"

    url = f"{VISUAL_CROSSING_TIMELINE}/{lat},{lon}/{start_d.isoformat()}/{end_d.isoformat()}"
    params = {
        "unitGroup": "metric",
        "key": VC_KEY,
        "include": "days",
        "elements": "datetime,precip",
    }
    try:
        r = requests.get(url, params=params, timeout=(5, 15))
        if r.status_code >= 400:
            return None, None, f"vc_http_{r.status_code}"
        payload = r.json()
        days = payload.get("days") or []
        if not days:
            return None, payload, "vc_no_days"
        total_mm = sum(float(d.get("precip") or 0.0) for d in days if isinstance(d, dict))
        return _mm_to_inches(total_mm), payload, None
    except Exception as e:
        return None, None, f"vc_error:{type(e).__name__}"


def secondary_precip_check(
    open_meteo_inches: float,
    vc_inches: float | None,
    vc_error: str | None,
) -> dict[str, Any]:
    if vc_inches is None:
        return {
            "source": "visual_crossing",
            "required": PRECIP_SECONDARY_REQUIRED,
            "status": "missing",
            "ok": not PRECIP_SECONDARY_REQUIRED,
            "error": vc_error,
            "open_meteo_inches": round(open_meteo_inches, 4),
            "visual_crossing_inches": None,
            "diff_inches": None,
            "max_diff_inches": PRECIP_SECONDARY_MAX_DIFF_IN,
        }

    diff = abs(open_meteo_inches - vc_inches)
    ok = diff <= PRECIP_SECONDARY_MAX_DIFF_IN
    return {
        "source": "visual_crossing",
        "required": PRECIP_SECONDARY_REQUIRED,
        "status": "agree" if ok else "disagree",
        "ok": ok,
        "error": None,
        "open_meteo_inches": round(open_meteo_inches, 4),
        "visual_crossing_inches": round(vc_inches, 4),
        "diff_inches": round(diff, 4),
        "max_diff_inches": PRECIP_SECONDARY_MAX_DIFF_IN,
    }


def sigma_for_window(n_days: int, snow: bool) -> float:
    base = 0.35 if snow else 0.18
    return max(0.08, base + 0.04 * math.sqrt(max(1, n_days)))


def _precip_max_hours() -> float:
    """Monthly precip needs >72h horizon; override via WEATHERBOT_PRECIP_MAX_HOURS."""
    raw = os.getenv("WEATHERBOT_PRECIP_MAX_HOURS")
    if raw not in (None, ""):
        try:
            return float(raw)
        except ValueError:
            pass
    return max(MAX_HOURS, 2160.0)


def discover_precip_markets(max_pages: int | None = None, page_size: int = 100) -> list[dict[str, Any]]:
    """
    Weather-tagged Polymarket events carry NYC/Seattle/London monthly precip buckets.
    Flatten nested markets; exclude pure temperature hi/lo markets.
    """
    if max_pages is None:
        raw = os.getenv("WEATHERBOT_PRECIP_DISCOVERY_PAGES", "").strip()
        if raw:
            try:
                max_pages = int(raw)
            except ValueError:
                max_pages = 8
        else:
            max_pages = 8
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(max_pages):
        try:
            r = requests.get(
                GAMMA_EVENTS,
                params={
                    "tag_slug": "weather",
                    "active": "true",
                    "closed": "false",
                    "limit": page_size,
                    "offset": page * page_size,
                },
                timeout=(8, 25),
            )
            r.raise_for_status()
            events = r.json()
        except Exception:
            break
        if not isinstance(events, list) or not events:
            break
        for ev in events:
            if not isinstance(ev, dict):
                continue
            for m in ev.get("markets") or []:
                if not isinstance(m, dict):
                    continue
                q = str(m.get("question") or "")
                mid = str(m.get("id") or m.get("marketId") or "")
                if not mid or mid in seen:
                    continue
                if _is_temperature_only_question(q):
                    continue
                if not _is_precip_candidate(q):
                    continue
                vol = float(m.get("volume") or 0)
                if vol < MIN_VOLUME:
                    continue
                seen.add(mid)
                out.append(m)
        if len(events) < page_size:
            break
    return out


def _extract_clob_token_ids(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """YES token = index 0, NO token = index 1 when two outcomes."""
    raw = payload.get("clobTokenIds")
    if isinstance(raw, str) and raw.strip().startswith("["):
        try:
            arr = json.loads(raw)
            if isinstance(arr, list) and len(arr) >= 2:
                return str(arr[0]), str(arr[1])
        except Exception:
            pass
    tokens = payload.get("tokens")
    if isinstance(tokens, list) and len(tokens) >= 2:
        y = n = None
        for i, t in enumerate(tokens):
            if not isinstance(t, dict):
                continue
            tid = t.get("token_id") or t.get("tokenId") or t.get("id")
            if i == 0 and tid:
                y = str(tid)
            if i == 1 and tid:
                n = str(tid)
        if y and n:
            return y, n
    # Fallback: single token from helper
    one = resolve_market_token_id(str(payload.get("id", "")))
    return one, None


def clob_bid_ask_mid(market_id: str) -> tuple[float | None, float | None, float | None, str | None]:
    """Fetch YES token book using Gamma + CLOB (same as plan: midpoint from book)."""
    try:
        r = requests.get(GAMMA_MARKET_URL.format(market_id=market_id), timeout=(4, 10))
        payload = r.json() if r.status_code < 400 else {}
        if not isinstance(payload, dict):
            return None, None, None, None
        yes_id, no_id = _extract_clob_token_ids(payload)
        if not yes_id:
            yes_id = resolve_market_token_id(market_id)
        if not yes_id:
            return None, None, None, None
        book = get_clob_orderbook(yes_id) or {}
        bid, ask = best_bid_ask_from_orderbook(book)
        if bid is None or ask is None:
            return bid, ask, None, yes_id
        mid = (bid + ask) / 2.0
        return bid, ask, mid, yes_id
    except Exception:
        return None, None, None, None


def clob_bid_ask_for_token(token_id: str) -> tuple[float | None, float | None]:
    book = get_clob_orderbook(token_id) or {}
    return best_bid_ask_from_orderbook(book)


def score_precip_market(
    market: dict[str, Any],
    balance: float,
) -> dict[str, Any] | None:
    q = str(market.get("question") or "")
    mid = str(market.get("id") or "")
    end_date = str(market.get("endDate") or "")
    hours_left = hours_to_resolution(end_date)
    prec_max_h = _precip_max_hours()
    if hours_left < MIN_HOURS or hours_left > prec_max_h:
        return None

    parsed = parse_precip_market(q, end_date)
    if not parsed:
        return None

    loc = resolve_precip_location(parsed.city_query)
    if not loc:
        return None
    lat, lon, city_label, location_source = loc
    city_slug = re.sub(r"[^\w\-]+", "_", city_label.lower())[:40]

    n_days = max(1, (parsed.end_d - parsed.start_d).days + 1)
    fc = fetch_precip_forecast_open_meteo(lat, lon, parsed.start_d, parsed.end_d, parsed.snow)
    if not fc:
        return None
    remaining_forecast, raw_fc = fc
    realized_mtd, raw_actual = fetch_realized_precip_open_meteo(
        lat,
        lon,
        parsed.realized_start_d,
        parsed.realized_end_d,
        parsed.snow,
    )
    vc_realized, raw_vc_actual, vc_error = fetch_realized_precip_visual_crossing(
        lat,
        lon,
        parsed.realized_start_d,
        parsed.realized_end_d,
        parsed.snow,
    )
    secondary_check = secondary_precip_check(realized_mtd, vc_realized, vc_error)
    forecast_total = realized_mtd + remaining_forecast

    sigma = sigma_for_window(n_days, parsed.snow)
    if parsed.mode == "range":
        if parsed.low_inches is None or parsed.high_inches is None:
            return None
        p_yes = probability_in_range(
            forecast_total, parsed.low_inches, parsed.high_inches, sigma
        )
    elif parsed.mode == "above":
        if parsed.threshold_inches is None:
            return None
        p_yes = probability_exceed(forecast_total, parsed.threshold_inches, sigma)
    else:
        if parsed.threshold_inches is None:
            return None
        p_yes = probability_below(forecast_total, parsed.threshold_inches, sigma)

    clob = get_clob_market_snapshot(mid)
    yes_book = clob.get("yes") or {}
    no_book = clob.get("no") or {}
    bid_yes = yes_book.get("bid")
    ask_yes = yes_book.get("ask")
    if ask_yes is None or bid_yes is None:
        return None
    spread_yes = ask_yes - bid_yes

    bid_no = no_book.get("bid")
    ask_no = no_book.get("ask")
    if bid_no is None or ask_no is None:
        return None

    ev_yes = calc_ev(p_yes, ask_yes)
    ev_no = calc_ev(1.0 - p_yes, ask_no)

    side = "YES"
    p_side = p_yes
    ev = ev_yes
    ask = ask_yes
    bid = bid_yes
    spread = spread_yes
    if ev_no > ev_yes:
        side = "NO"
        p_side = 1.0 - p_yes
        ev = ev_no
        ask = ask_no
        bid = bid_no
        spread = ask_no - bid_no if ask_no and bid_no else spread_yes

    kelly = calc_kelly(p_side, ask)
    size_usdc = bet_size(kelly, balance)

    would_pass = (
        ev >= MIN_EV
        and ask <= MAX_PRICE
        and spread <= MAX_SLIPPAGE
        and float(market.get("volume") or 0) >= MIN_VOLUME
        and not (side == "NO" and bool(clob.get("synthetic_no")))
        and bool(secondary_check["ok"])
    )
    if would_pass:
        reason = "ok"
    elif side == "NO" and bool(clob.get("synthetic_no")):
        reason = "synthetic_no_not_tradeable"
    elif not bool(secondary_check["ok"]):
        reason = f"secondary_precip_{secondary_check['status']}"
    else:
        reason = "filtered_weak_or_price"

    # Optional forecast artifact
    try:
        ddir = FORECASTS_PRECIP_DIR / city_slug
        ddir.mkdir(parents=True, exist_ok=True)
        fts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (ddir / f"{fts}_{mid}.json").write_text(
            json.dumps(
                {
                    "forecast": raw_fc,
                    "realized": raw_actual,
                    "realized_secondary": raw_vc_actual,
                    "secondary_check": secondary_check,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    return {
        "market_id": mid,
        "question": q,
        "city": city_slug,
        "city_name": city_label,
        "location_source": location_source,
        "mode": parsed.mode,
        "window": parsed.window_label,
        "threshold_low_inches": round(parsed.low_inches, 4) if parsed.low_inches is not None else None,
        "threshold_high_inches": round(parsed.high_inches, 4) if parsed.high_inches is not None else None,
        "threshold_inches": round(parsed.threshold_inches, 4)
        if parsed.threshold_inches is not None
        else None,
        "forecast_total_inches": round(forecast_total, 4),
        "realized_mtd_inches": round(realized_mtd, 4),
        "secondary_realized_inches": round(vc_realized, 4)
        if vc_realized is not None
        else None,
        "secondary_check": secondary_check,
        "remaining_forecast_inches": round(remaining_forecast, 4),
        "realized_start": parsed.realized_start_d.isoformat()
        if parsed.realized_start_d is not None
        else None,
        "realized_end": parsed.realized_end_d.isoformat()
        if parsed.realized_end_d is not None
        else None,
        "probability": round(p_side, 4),
        "p_yes_model": round(p_yes, 4),
        "side": side,
        "bid": round(bid, 4),
        "ask": round(ask, 4),
        "mid": round((bid + ask) / 2.0, 4) if bid and ask else None,
        "spread": round(spread, 4),
        "ask_size": (yes_book if side == "YES" else no_book).get("ask_size", 0.0),
        "synthetic_no": bool(clob.get("synthetic_no")),
        "ev": ev,
        "kelly": kelly,
        "size_usdc": size_usdc,
        "hours_left": round(hours_left, 2),
        "volume": float(market.get("volume") or 0),
        "would_pass_gateway": would_pass,
        "reason": reason,
    }


def _precip_market_path(market_id: str) -> Path:
    return PRECIP_MARKETS_DIR / f"{market_id}.json"


def scan_precip_and_log(now_iso: str | None = None, balance: float | None = None) -> int:
    """
    Full precip discovery + scoring cycle. Appends one line per scored market to precip_log.jsonl.
    Returns count of log lines written.
    """
    paper_only = os.getenv("WEATHERBOT_PRECIP_PAPER_ONLY", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not paper_only:
        # Phase 2: wire ExecutionGateway + TradeSignal extensions; keep paper behavior until then.
        pass

    if os.getenv("WEATHERBOT_PRECIP_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return 0

    ts = now_iso or datetime.now(timezone.utc).isoformat()
    bal = balance if balance is not None else BALANCE

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PRECIP_MARKETS_DIR.mkdir(parents=True, exist_ok=True)

    markets = discover_precip_markets()
    logged = 0
    for m in markets:
        time.sleep(0.05)
        try:
            scored = score_precip_market(m, bal)
            if not scored:
                continue
            scored["ts"] = ts
            scored["paper_only"] = True

            # Persist latest snapshot per market id
            mp = _precip_market_path(scored["market_id"])
            mp.write_text(json.dumps(scored, indent=2, ensure_ascii=False), encoding="utf-8")

            if scored["ev"] < MIN_EV:
                continue

            line = json.dumps(scored, ensure_ascii=True)
            with PRECIP_LOG.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            logged += 1
        except Exception:
            continue

    return logged
