# Paper Trading Watch

Operational checklist for running **weatherbot** in paper mode while validating signals before shadow or live trading.

## 1. Visual Crossing key (precip cross-check + resolved temps)

1. Copy [`.env.weatherbot.example`](.env.weatherbot.example) to `.env.weatherbot` and set a real `WEATHERBOT_VC_KEY`.
2. Restart the agent:
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.dubski.weatherbot
   ```
3. Verify precip secondary sources:
   ```bash
   ./.venv/bin/python check_precip_secondary.py
   ```
   You should see Open-Meteo vs Visual Crossing totals and `status: agree` or `disagree` (see `WEATHERBOT_PRECIP_SECONDARY_MAX_DIFF_IN` in `.env.weatherbot.example`).

More detail: [VERIFY_CALIBRATION.md](VERIFY_CALIBRATION.md).

## 2. Observe paper (24–72 hours)

- Keep **paper** mode: `WEATHERBOT_LIVE_TRADING=0` or, if testing the gateway, `WEATHERBOT_DRY_RUN_LIVE=1` (see [`.env.weatherbot-live.example`](.env.weatherbot-live.example)).
- Quick sanity:
  ```bash
  ./.venv/bin/python verify_paper_watch.py
  ```
- Let the scheduled **launchd** job run; logs: `logs/weatherbot.out.log`, `logs/weatherbot.err.log`.

## 3. Daily review

Run once per day (or from `cron`):

```bash
./.venv/bin/python paper_watch_review.py
```

This prints `status`, `live-status`, `precip-status`, last-24h `secondary_check` status counts from `data/precip_markets/*.json`, and tails of `data/live_events.jsonl` / `data/live_signals.jsonl` if present.

Also:

```bash
./status.sh
python bot_v2.py report
```

## 4. When to move to **shadow** execution

Treat as a gate, not a calendar date:

- Precip: high-EV lines show `secondary_check.status` = `agree` in `data/precip_markets/*.json` (not `missing` or repeated `disagree`).
- Temperature: resolved PnL and per-city behavior in `python bot_v2.py report` are explainable; no systematic parser or city-proxy mistakes.
- CLOB: spreads and `ask_size` look tradeable for signals you care about.
- Gateway: any `live_events` / `live_signals` entries have clear pass/reject reasons.

Then enable shadow/dry-run live per [SELF_LEARNING.md](SELF_LEARNING.md) and [`.env.weatherbot-live.example`](.env.weatherbot-live.example) — still **no** real capital until a separate tiny-live checklist is satisfied.

## Reference

- Full graduation notes: [SELF_LEARNING.md](SELF_LEARNING.md)
- VC API issues: [VERIFY_CALIBRATION.md](VERIFY_CALIBRATION.md)
