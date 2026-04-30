# Backtesting Integration Plan

Integrate [evan-kolberg/prediction-market-backtesting](https://github.com/evan-kolberg/prediction-market-backtesting) (NautilusTrader-based) into the weatherbot project to validate and tune the strategy against historical Polymarket data before paying real money for slippage and fees.

## Goal

- Reproduce `bot_v1.py`'s decision logic as a NautilusTrader `Strategy` running inside the backtest framework.
- Replay historical Polymarket weather-market tick data through that strategy.
- Feed NWS forecast snapshots into the strategy at decision time (the non-trivial part — NWS does not expose historical forecasts cleanly; see Phase 2).
- Use Optuna hyperparameter search on `entry_threshold`, `exit_threshold`, `kelly_fraction`, `max_price`, `min_volume`, `min_hours`.
- Output equity curve, Sharpe, drawdown, Brier score so we can compare against the live `simulation.json` numbers.

## Current state (for context)

- `bot_v1.py` — single-file bot, scans Polymarket `highest-temperature-in-{city}-on-{month}-{day}-{year}` markets, fetches NWS forecast/observations, buys the bucket matching the forecast when price < `entry_threshold`, exits when price > `exit_threshold`.
- `config.json` has thresholds and a virtual balance (10000).
- `data/markets/*.json` is the raw market snapshot cache.
- No backtest infra exists.

---

## Phase 0 — Repo setup (30 min)

1. Clone the framework as a sibling directory (keep weatherbot clean):
   ```
   cd ~/
   git clone https://github.com/evan-kolberg/prediction-market-backtesting pmbt
   ```
2. Create a new venv for it — it requires **Python 3.12** and a working Rust toolchain for NautilusTrader. Do **not** reuse weatherbot's Python 3.14 venv.
   ```
   cd pmbt && python3.12 -m venv .venv && source .venv/bin/activate
   pip install -e .
   ```
3. Run their example Polymarket backtest end-to-end to confirm data pipeline and rendering work. If PMXT data download fails, sort that out before writing any code — it's the blocker for everything else.

---

## Phase 1 — Port strategy logic (1–2 hrs)

Create `pmbt/strategies/weather_forecast.py`. Mirror `bot_v1.py`'s decision tree in NautilusTrader's `Strategy` API.

Key translations:

| `bot_v1.py` concept | NautilusTrader equivalent |
|---|---|
| `get_polymarket_event()` scan loop | `on_start()` subscribes to instrument(s) passed in config |
| `parse_temp_range()` + matching bucket | Pre-computed at strategy init from instrument metadata |
| `price < ENTRY_THRESHOLD` | `on_quote_tick()` → submit `MarketOrder(BUY)` |
| `price > EXIT_THRESHOLD` | Same handler → submit `MarketOrder(SELL)` |
| `POSITION_PCT * balance` | `self.portfolio.account(...).balance_free()` * pct |
| `load_sim()` / `save_sim()` | NautilusTrader tracks this — throw away `simulation.json` logic |

**Config class** — mirror `config.json` fields 1:1 as a pydantic/attrs config so Optuna can sweep them later:

```python
class WeatherForecastConfig(StrategyConfig):
    entry_threshold: float = 0.15
    exit_threshold: float = 0.45
    max_price: float = 0.45
    min_volume: float = 500
    min_hours: float = 2.0
    max_hours: float = 72.0
    kelly_fraction: float = 0.25
    position_pct: float = 0.05
    # forecast_provider: injected at runtime, see Phase 2
```

---

## Phase 2 — Forecast data (the hard part, 3–5 hrs)

NWS does not give you forecasts as of a point in time — so for a market resolving on 2026-04-20, you cannot ask today "what did NWS predict on 2026-04-18 at 09:00 UTC?"

Three options, ordered by effort:

### Option A — Start logging forecasts now, backtest later
Run a cron that stores `get_forecast()` output every hour into `data/forecasts/{city}/{timestamp}.json`. After a few weeks you have real historical forecasts. **Fastest to build, slowest to produce useful results.** Recommend doing this in parallel regardless.

### Option B — Use a paid historical-forecast API
Open-Meteo has a free historical forecast archive (`historical-forecast-api.open-meteo.com`) that returns what their model predicted at past timestamps. Swap NWS for Open-Meteo in the backtest only; keep NWS in live. Accept the model-divergence risk (document it).

### Option C — Use actuals as a proxy
Use the realized daily max temperature as the "forecast" (i.e., perfect-foresight backtest). Gives you an **upper bound** on strategy performance, not a realistic estimate. Useful as a sanity check — if the strategy can't make money even with perfect forecasts, the bug isn't data quality.

Build Option C first (cheapest, reveals logic bugs), then Option B for real numbers. Option A is long-term.

Implement as a pluggable `ForecastProvider` interface:
```python
class ForecastProvider(Protocol):
    def forecast_for(self, city: str, target_date: date, as_of: datetime) -> float | None: ...
```

---

## Phase 3 — Historical Polymarket data (1 hr, mostly waiting)

The framework uses PMXT for Polymarket tick data. Weather markets are a niche — check whether they're in the PMXT index before assuming they're available:

```
curl https://r2v2.pmxt.dev/index.json | grep -i weather
```

If weather markets aren't covered, fall back to Polymarket's own `gamma-api` for market metadata + scrape trade history per market from `data-api.polymarket.com/trades?market=<condition_id>`. Slower, rate-limited, but works. Write this as a custom PMXT-compatible parquet exporter so the rest of the framework doesn't care.

---

## Phase 4 — Run the backtest (30 min)

`pmbt/backtests/weather_forecast_backtest.py`:

- Load tick data for ~20 resolved weather markets across your 6 cities over the last 1–3 months.
- Instantiate the strategy with `config.json` defaults.
- Run, produce HTML report.
- Compare equity curve vs. what `simulation.json` shows for the same window.

Sanity gate: if backtested PnL is wildly different from paper PnL over an overlapping window, there's a bug in the port — fix before tuning.

---

## Phase 5 — Optuna sweep (1 hr)

Use the framework's built-in TPE sampler. Objective = Sharpe (or terminal equity if trade count is low). Search space:

```python
{
  "entry_threshold": [0.05, 0.35],
  "exit_threshold":  [0.30, 0.80],
  "max_price":       [0.30, 0.60],
  "kelly_fraction":  [0.05, 0.50],
  "min_hours":       [0.5, 12.0],
}
```

Run ~200 trials. Report best params. Do **not** automatically write them back to `config.json` — eyeball the parameter-importance plot first, reject if the "best" params sit on a sharp spike (overfit).

---

## Phase 6 — Close the loop (optional)

- Nightly cron: re-run the backtest with the latest resolved markets → alert if the live config's expected Sharpe drops below threshold.
- Store winning configs as git-versioned YAML under `configs/` so you can roll back.

---

## File layout after integration

```
~/weatherbot/              # unchanged, live bot
  bot_v1.py
  config.json
  data/forecasts/          # NEW: hourly NWS snapshots (Option A)

~/pmbt/                    # the framework repo
  strategies/
    weather_forecast.py    # ported bot_v1 logic
  providers/
    forecast_nws.py        # live (unused in backtest)
    forecast_openmeteo.py  # Option B
    forecast_actuals.py    # Option C
  backtests/
    weather_forecast_backtest.py
    weather_forecast_optuna.py
  data/polymarket/weather/ # parquet ticks
```

Keep the framework repo separate — don't vendor it into weatherbot. Share a small package (`weatherbot-core`) only if/when logic needs to be shared between live and backtest; until then, duplicate the ~100 lines of decision logic, it's fine.

---

## Open questions to resolve before starting

1. Are resolved Polymarket weather markets actually available in PMXT? (Phase 3, check first)
2. Do you want to tune per-city or global params? Per-city is more accurate but needs 6× the data.
3. How much does forecast-model divergence (NWS vs Open-Meteo) distort backtest results? Run the same backtest with both providers and compare.

## Non-goals

- Rebuilding the bot inside the framework. Keep `bot_v1.py` as the production path.
- Live trading from inside NautilusTrader. Framework is for offline eval only.
- Porting the calibration/self-learning system until Phase 1–5 works end-to-end.
