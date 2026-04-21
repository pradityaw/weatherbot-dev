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
