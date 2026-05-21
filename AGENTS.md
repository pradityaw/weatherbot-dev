# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

WeatherBet is a pure-Python weather-market trading bot for Polymarket. No database, no Docker — all state persisted as JSON in `data/`. See `README.md` for full architecture.

### Running tests

```bash
python3 -m pytest tests/ -v
```

CI uses Python 3.12 with `pytest` and `requests` (see `.github/workflows/test.yml`). Tests are fast (~0.2s) and have no network dependencies.

### Running the bot

The bot requires a `config.json` in the repo root (already committed with placeholder values). Key commands:

```bash
python3 bot_v2.py run          # main loop — scans every hour, monitors every 10 min
python3 bot_v2.py status       # balance and open positions
python3 bot_v2.py report       # full breakdown of resolved markets
python3 bot_v2.py live-status  # execution mode and guardrail config
```

Default mode is **PAPER** (no real money). Environment variables control execution mode — see `execution.py` `ExecutionConfig.from_env()`.

### Caveats

- Use `python3` not `python` — the VM does not alias `python` to `python3`.
- The bot's main loop (`run`) will scan 20 cities and make HTTP calls to Open-Meteo, Polymarket, and Aviation Weather APIs. Some cities (especially Wellington/NZWN) can be slow due to API latency; allow ~3 minutes for a full scan cycle.
- `config.json` has `vc_key: "YOUR_KEY_HERE"` — the bot runs fine without a real Visual Crossing key but cannot self-calibrate or resolve actual temperatures.
- The optional Node.js tool under `tools/cursor-prelive/` requires a `CURSOR_API_KEY` to run; it is not part of the bot's core runtime.
