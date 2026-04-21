# Weatherbot Safety Scan

Date: 2026-04-22

Upstream:
- Repo: `https://github.com/alteregoeth-ai/weatherbot`
- Pinned HEAD at clone time: `3cabb23e1b74da608f554c427778c8988ad3e591`
- License: MIT

Local runtime:
- Python venv: `.venv`
- Installed dependency: `requests==2.33.1`
- Lock snapshot: `requirements.lock.txt`

## Files reviewed

- `README.md`
- `bot_v1.py`
- `bot_v2.py`
- `config.json`
- `.gitignore`

## Safety findings

No critical red flags found in the upstream code reviewed.

Observed behavior:
- No `subprocess`, `os.system`, `popen`, `eval`, `exec`, dynamic imports, or pickle/marshal deserialization in the Python sources.
- Network access is limited to public data APIs:
  - `https://api.open-meteo.com`
  - `https://aviationweather.gov`
  - `https://gamma-api.polymarket.com`
  - `https://weather.visualcrossing.com`
  - `https://api.weather.gov` in `bot_v1.py`
- The bot is paper-only. It does not sign transactions, use a private key, or submit live Polymarket orders.

Local write surface:
- `data/state.json`
- `data/calibration.json`
- `data/markets/*.json`
- `logs/weatherbot.out.log`
- `logs/weatherbot.err.log`

## Notes

- The README refers to `weatherbet.py`, but the actual full bot file in the repo is `bot_v2.py`.
- Self-learning requires a Visual Crossing API key. Without `WEATHERBOT_VC_KEY` or a real `vc_key` in `config.json`, the bot still scans and paper-trades, but it cannot fetch final observed temperatures for calibration.

## Local hardening added

- `bot_v2.py` now accepts:
  - `WEATHERBOT_VC_KEY`
  - `WEATHERBOT_SCAN_INTERVAL`
  - `WEATHERBOT_MONITOR_INTERVAL`
- Added `launch-weatherbot.sh` for consistent startup from the project root.
- Added `com.dubski.weatherbot.plist` for persistent macOS `launchd` operation.
- Added `.env.weatherbot.example` so secrets can stay out of `config.json`.

## Recommendation

Safe enough to run locally in continuous paper mode for data collection.

Before trusting self-calibration output, add a valid Visual Crossing key so resolved markets can record `actual_temp` and update `data/calibration.json`.
