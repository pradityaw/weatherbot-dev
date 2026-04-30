# Trader edge research — data sources

## Surf CLI (optional)

When `surf` is authenticated, these endpoints mirror Polymarket public data with pagination helpers:

| Command | Purpose |
|---------|---------|
| `surf polymarket-trades --condition-id <hex>` | Trades for one condition; `--address` for wallet; `--limit` up to 500 |
| `surf polymarket-leaderboard` | Global leaderboard (not weather-filtered) |
| `surf polymarket-smart-money` | Whale / directional activity |
| `surf polymarket-markets` | Market metadata |

Run `surf polymarket-trades --help` for flags. Data refresh ~5 minutes per Surf docs.

## Direct HTTP (collector default)

No API key required for read-only public endpoints:

| URL | Use |
|-----|-----|
| `GET https://gamma-api.polymarket.com/events?slug=<event_slug>` | Event + nested `markets[]` with `id`, `conditionId`, `question`, `closed`, `outcomePrices`, `volume`, `endDate` |
| `GET https://gamma-api.polymarket.com/markets/<market_id>` | Single market refresh |
| `GET https://data-api.polymarket.com/trades?market=<conditionId>&limit=500&offset=N` | All trades for a binary market; paginate with `offset` until fewer than `limit` rows |

### Trade row fields (data-api)

Typical fields used in this repo: `proxyWallet`, `side` (`BUY`/`SELL`), `outcome` (`Yes`/`No`), `price`, `size`, `timestamp` (unix seconds), `conditionId`, `transactionHash`, `title`, `slug`, `eventSlug`.

### Weather universe

Event slug pattern (matches `get_polymarket_event` in `bot_v2.py`):

`highest-temperature-in-{city_slug}-on-{month_name}-{day}-{year}`

Example: `highest-temperature-in-nyc-on-april-28-2026`.

Each temperature bucket is its own **binary** market with its own `conditionId`.

## PMXT / tick history

For full LOB replay see `BACKTEST_INTEGRATION_PLAN.md` (PMXT). This research pipeline uses **trade prints + Gamma resolution** only.
