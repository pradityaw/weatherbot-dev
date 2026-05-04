# Trader edge research

Offline pipeline to study Polymarket **weather** (`highest-temperature-in-*`) traders and optional **shadow** logging in `bot_v2.py`.

## Prerequisites

- Project venv: `.venv/bin/python`
- Network access to `gamma-api.polymarket.com` and `data-api.polymarket.com`
- Collector imports `LOCATIONS` from `bot_v2` (needs `config.json` present)

## Pipeline

1. **Collect** metadata + trades into `data/trader_research/raw/`:

```bash
.venv/bin/python -m trader_research.collect --start 2026-01-01 --end 2026-04-30 --cities nyc,chicago
```

2. **Rank** wallets by realized PnL on resolved buckets:

```bash
.venv/bin/python -m trader_research.rank_traders --rolling-days 120
```

Output: `data/trader_research/processed/rankings.json`

3. **Patterns** (first BUY YES price, hours before `event_end_date`):

```bash
.venv/bin/python -m trader_research.analyze_patterns --top 25
```

Output: `data/trader_research/processed/pattern_summary.json`

4. **Backtest** simple consensus / veto metrics:

```bash
.venv/bin/python -m trader_research.backtest_edge --top 15
```

Output: `data/trader_research/processed/backtest_summary.json`

## Live bot (shadow only)

After `rankings.json` exists, enable logging of trader-edge features on entry candidates:

```bash
export WEATHERBOT_TRADER_EDGE=1
```

Events are appended to `data/live_events.jsonl` as `trader_edge_snapshot` or `trader_edge_skip`.
By default this is logging-only. To opt into the probability nudge that is now wired in `bot_v2.py`, set a small positive strength:

```bash
export WEATHERBOT_TRADER_EDGE_STRENGTH=0.01
```

The nudge is bounded to `[0.01, 0.99]`, recomputes EV/Kelly/size, and is skipped unless `WEATHERBOT_TRADER_EDGE=1` successfully loads trader features. It can reduce a signal below normal entry thresholds; in that case the bot skips the candidate rather than opening a weakened position.

See [DATA_SOURCES.md](DATA_SOURCES.md) for API notes.
