# Verifying Visual Crossing and calibration

1. Set a real `WEATHERBOT_VC_KEY` in `.env.weatherbot` and restart the agent:
   `launchctl kickstart -k gui/$(id -u)/com.dubski.weatherbot`

2. After the first **Polymarket-resolved** market, the bot fetches the actual max temperature. Check for API issues:
   ```bash
   grep '\[VC\]' logs/weatherbot.out.log
   ```
   - **No output** and a non-empty `actual_temp` in the relevant `data/markets/*.json` file means the key is working.
   - Repeated `[VC] ...` lines mean the key, quota, or request is wrong — fix before trusting `data/calibration.json`.

3. `data/calibration.json` is only created/updated after enough resolved markets with `actual_temp` (see `calibration_min` in `config.json`).

4. With no API key, `get_actual_temp` is never called, so you will see **no** `[VC]` lines — that is expected until you add a key and a market resolves.

## Precipitation secondary source (Open-Meteo vs Visual Crossing)

The precip scanner compares realized month-to-date totals from **Open-Meteo archive** with **Visual Crossing** timeline precipitation before marking `would_pass_gateway=True` (when `WEATHERBOT_PRECIP_SECONDARY_REQUIRED=true`).

1. Set the same `WEATHERBOT_VC_KEY` in `.env.weatherbot` and restart the agent.
2. Run a one-shot comparison (Seoul proxy, current month through yesterday):
   ```bash
   ./.venv/bin/python check_precip_secondary.py
   ```
3. If the key is missing, you will see `vc_key_missing` in `secondary_check` inside `data/precip_markets/*.json` and signals will not pass the gateway-ready flag.

See also [PAPER_WATCH.md](PAPER_WATCH.md).
