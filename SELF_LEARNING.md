# Self-Learning and Operations

This setup runs `bot_v2.py` continuously in paper mode.

## Cadence

- Full market scan: every 60 minutes (`scan_interval=3600`)
- Position monitoring: every 10 minutes
- Process manager: macOS `launchd`

## Data files

- `data/state.json`
  - running paper balance
  - win/loss counts
  - peak balance

- `data/markets/<city>_<date>.json`
  - forecast snapshots from ECMWF, HRRR, and METAR
  - market price snapshots
  - selected bucket and simulated position
  - close reason and realized PnL
  - resolution result once the market is final

- `data/calibration.json`
  - learned `sigma` per `(city, source)`
  - updated after at least `calibration_min` resolved markets are available

## How the learning works

`bot_v2.py` stores forecast snapshots over time for each market. After the market resolves, it tries to fetch the actual maximum temperature. Once enough resolved markets exist, it recalculates forecast error by city and source:

- lower `sigma` means the source has been more accurate there
- lower error increases confidence in the probability estimate
- higher confidence can increase Kelly sizing, subject to the max bet cap

Without a Visual Crossing key, the bot cannot populate `actual_temp`, which means calibration will not advance.

## Environment variables

Create `.env.weatherbot` from `.env.weatherbot.example` if you want local overrides:

```bash
cp .env.weatherbot.example .env.weatherbot
```

Supported values:

- `WEATHERBOT_VC_KEY`
- `WEATHERBOT_SCAN_INTERVAL`
- `WEATHERBOT_MONITOR_INTERVAL`
- `WEATHERBOT_LIVE_TRADING`
- `WEATHERBOT_DRY_RUN_LIVE`
- `WEATHERBOT_MAX_LIVE_USDC`
- `WEATHERBOT_LIVE_TRADE_CAP_USDC`
- `WEATHERBOT_MAX_OPEN_EXPOSURE_USDC`
- `WEATHERBOT_MAX_DAILY_LOSS_USDC`
- `WEATHERBOT_MAX_NEW_TRADES_PER_DAY`
- `WEATHERBOT_LIVE_MAX_PRICE`
- `WEATHERBOT_LIVE_MAX_SLIPPAGE`
- `WEATHERBOT_KILL_SWITCH_FILE`

## launchd

Installed agent label:

- `com.dubski.weatherbot`

Useful commands:

```bash
launchctl print gui/$(id -u)/com.dubski.weatherbot
launchctl kickstart -k gui/$(id -u)/com.dubski.weatherbot
launchctl unload ~/Library/LaunchAgents/com.dubski.weatherbot.plist
launchctl load ~/Library/LaunchAgents/com.dubski.weatherbot.plist
```

Logs:

- `logs/weatherbot.out.log`
- `logs/weatherbot.err.log`

## Graduation to live trading

Before wiring this into real Polymarket execution, collect enough paper samples to answer:

- Is the resolved win rate positive across a meaningful number of markets?
- Does total PnL stay positive after a few dozen resolved positions?
- Does `data/calibration.json` stabilize instead of swinging wildly?
- Which cities and forecast sources are consistently strongest or weakest?

Once those answers look good, the next step is not to trust this script directly with capital, but to port the signal logic into a controlled live execution path.

---

## Graduation checklist (paper to real trading)

Treat this as a gate. Do not skip straight to capital until every item is satisfied.

### Phase 1 — Environment and learning loop

- [ ] `.env.weatherbot` contains a real `WEATHERBOT_VC_KEY` (not a placeholder).
- [ ] Agent restarted after setting the key: `launchctl kickstart -k gui/$(id -u)/com.dubski.weatherbot`.
- [ ] Resolved markets record `actual_temp` in `data/markets/*.json` when Polymarket settles (requires Visual Crossing).
- [ ] No repeated `[VC]` failures in `logs/weatherbot.out.log` once markets resolve (occasional timeouts are OK).

### Phase 2 — Enough sample size

- [ ] At least **30 resolved** markets total (aligned with `calibration_min` in `config.json` so calibration has fired at least once).
- [ ] `data/calibration.json` exists and sigma values stop swinging wildly cycle-over-cycle (e.g. less than ~20% change between updates for stable city/source pairs).

### Phase 3 — profitability and robustness on your machine

- [ ] Resolved **win rate** is acceptable for your risk tolerance (many teams target **≥55%** as a sanity bar for binary-style outcomes — tune to your filters).
- [ ] **Total resolved PnL** on paper balance is positive over the sample (not just one lucky city).
- [ ] Performance is **not concentrated** in one city unless you deliberately restrict the universe — review per-city breakdown in `bot_v2.py report`.

### Phase 4 — Operational readiness before real orders

- [ ] Logs rotate or stay bounded (`launch-weatherbot.sh` rotates at 10 MiB; inspect `logs/` periodically).
- [ ] You run `./daily-check.sh` on a schedule (manual or `cron`) and fix recurring API failures.
- [ ] You understand that **live trading requires a separate implementation**: Polymarket CLOB signing, separate wallet limits, kill switch, and monitoring — not flipping a flag inside this repo.

### Quick commands

```bash
./status.sh           # balance + counts
./daily-check.sh      # status + recent log tail
python paper_watch_review.py   # Paper Trading Watch daily bundle (status + precip + live tails)
python verify_paper_watch.py # env check (paper mode + VC key)
python check_precip_secondary.py   # Open-Meteo vs VC precip sanity check
python bot_v2.py report
python bot_v2.py live-status
python bot_v2.py pause-live
python bot_v2.py resume-live
python check_live_wallet.py
```

Paper Trading Watch checklist: [PAPER_WATCH.md](PAPER_WATCH.md).
