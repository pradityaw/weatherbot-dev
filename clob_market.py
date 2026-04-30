from __future__ import annotations

import json
import re
from typing import Any

import requests


GAMMA_MARKET_URL = "https://gamma-api.polymarket.com/markets/{market_id}"
CLOB_BOOK_URL = "https://clob.polymarket.com/book"


def _parse_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _parse_size(value: Any) -> float:
    parsed = _parse_float(value, 0.0)
    return float(parsed or 0.0)


def _extract_token_id(payload: dict[str, Any]) -> str | None:
    tokens = payload.get("tokens")
    if isinstance(tokens, list) and tokens:
        first = tokens[0]
        if isinstance(first, dict):
            for key in ("token_id", "tokenId", "id"):
                val = first.get(key)
                if val:
                    return str(val)
        elif first:
            return str(first)

    for key in ("clobTokenId", "clob_token_id", "token_id"):
        val = payload.get(key)
        if val:
            return str(val)

    ids = payload.get("clobTokenIds")
    if isinstance(ids, str) and ids:
        # Often stored as JSON array in a string.
        try:
            parsed = json.loads(ids)
            if isinstance(parsed, list) and parsed:
                return str(parsed[0])
        except Exception:
            match = re.search(r"\d+", ids)
            if match:
                return match.group(0)
    return None


def extract_yes_no_token_ids(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return YES/NO token IDs when Gamma exposes a binary CLOB token pair."""
    ids = payload.get("clobTokenIds")
    if isinstance(ids, str) and ids:
        try:
            parsed = json.loads(ids)
            if isinstance(parsed, list) and len(parsed) >= 2:
                return str(parsed[0]), str(parsed[1])
        except Exception:
            matches = re.findall(r"\d+", ids)
            if len(matches) >= 2:
                return matches[0], matches[1]

    tokens = payload.get("tokens")
    if isinstance(tokens, list) and len(tokens) >= 2:
        yes_token = None
        no_token = None
        for idx, token in enumerate(tokens[:2]):
            if isinstance(token, dict):
                token_id = token.get("token_id") or token.get("tokenId") or token.get("id")
                outcome = str(token.get("outcome", "")).lower()
            else:
                token_id = token
                outcome = ""
            if not token_id:
                continue
            if outcome == "yes" or idx == 0:
                yes_token = str(token_id)
            elif outcome == "no" or idx == 1:
                no_token = str(token_id)
        return yes_token, no_token

    one = _extract_token_id(payload)
    return one, None


def resolve_market_token_id(market_id: str, timeout: tuple[int, int] = (3, 8)) -> str | None:
    try:
        r = requests.get(GAMMA_MARKET_URL.format(market_id=market_id), timeout=timeout)
        payload = r.json()
    except Exception:
        return None
    return _extract_token_id(payload if isinstance(payload, dict) else {})


def resolve_market_token_ids(market_id: str, timeout: tuple[int, int] = (3, 8)) -> tuple[str | None, str | None]:
    try:
        r = requests.get(GAMMA_MARKET_URL.format(market_id=market_id), timeout=timeout)
        payload = r.json()
    except Exception:
        return None, None
    return extract_yes_no_token_ids(payload if isinstance(payload, dict) else {})


def get_clob_orderbook(token_id: str, timeout: tuple[int, int] = (3, 8)) -> dict[str, Any] | None:
    try:
        r = requests.get(CLOB_BOOK_URL, params={"token_id": token_id}, timeout=timeout)
        if r.status_code >= 400:
            return None
        payload = r.json()
        if isinstance(payload, dict):
            return payload
    except Exception:
        return None
    return None


def best_bid_ask_from_orderbook(orderbook: dict[str, Any]) -> tuple[float | None, float | None]:
    bids = orderbook.get("bids") if isinstance(orderbook, dict) else None
    asks = orderbook.get("asks") if isinstance(orderbook, dict) else None

    best_bid = None
    best_ask = None

    bid_prices = []
    if isinstance(bids, list):
        for level in bids:
            if isinstance(level, dict):
                price = _parse_float(level.get("price"))
            elif isinstance(level, (list, tuple)) and level:
                price = _parse_float(level[0])
            else:
                price = None
            if price is not None:
                bid_prices.append(price)
    if bid_prices:
        best_bid = max(bid_prices)

    ask_prices = []
    if isinstance(asks, list):
        for level in asks:
            if isinstance(level, dict):
                price = _parse_float(level.get("price"))
            elif isinstance(level, (list, tuple)) and level:
                price = _parse_float(level[0])
            else:
                price = None
            if price is not None:
                ask_prices.append(price)
    if ask_prices:
        best_ask = min(ask_prices)

    return best_bid, best_ask


def _top_size(levels: Any, target_price: float | None) -> float:
    if target_price is None or not isinstance(levels, list):
        return 0.0
    total = 0.0
    for level in levels:
        if isinstance(level, dict):
            price = _parse_float(level.get("price"))
            size = _parse_size(level.get("size"))
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            price = _parse_float(level[0])
            size = _parse_size(level[1])
        else:
            continue
        if price == target_price:
            total += size
    return total


def _book_summary(token_id: str | None, timeout: tuple[int, int] = (3, 8)) -> dict[str, Any]:
    if not token_id:
        return {
            "token_id": None,
            "bid": None,
            "ask": None,
            "mid": None,
            "spread": None,
            "bid_size": 0.0,
            "ask_size": 0.0,
        }
    book = get_clob_orderbook(token_id, timeout=timeout) or {}
    bid, ask = best_bid_ask_from_orderbook(book)
    return {
        "token_id": token_id,
        "bid": bid,
        "ask": ask,
        "mid": round((bid + ask) / 2.0, 4) if bid is not None and ask is not None else None,
        "spread": round(ask - bid, 4) if bid is not None and ask is not None else None,
        "bid_size": round(_top_size(book.get("bids"), bid), 4),
        "ask_size": round(_top_size(book.get("asks"), ask), 4),
    }


def get_clob_market_snapshot(market_id: str, timeout: tuple[int, int] = (3, 8)) -> dict[str, Any]:
    """
    Canonical live-tradeable CLOB snapshot for a binary Polymarket market.

    NO may be synthetic when the NO token book is unavailable. Synthetic NO is
    useful for analytics only; do not submit live NO orders without a token_id.
    """
    yes_token, no_token = resolve_market_token_ids(market_id, timeout=timeout)
    yes = _book_summary(yes_token, timeout=timeout)
    no = _book_summary(no_token, timeout=timeout)
    synthetic_no = False

    if no["ask"] is None and yes["bid"] is not None and yes["ask"] is not None:
        synthetic_no = True
        no = {
            "token_id": no_token,
            "bid": round(max(0.01, min(0.99, 1.0 - yes["ask"])), 4),
            "ask": round(max(0.01, min(0.99, 1.0 - yes["bid"])), 4),
            "mid": round(max(0.01, min(0.99, 1.0 - (yes["mid"] or 0.5))), 4),
            "spread": yes["spread"],
            "bid_size": 0.0,
            "ask_size": 0.0,
        }

    return {
        "market_id": str(market_id),
        "yes": yes,
        "no": no,
        "synthetic_no": synthetic_no,
    }
