# borsa-telegram-bot

## Run (web + worker separate)

Web process:

```bash
python app.py
```

Worker process:

```bash
python worker.py
```

Required environment variables:

- `TOKEN`
- `CHAT_ID`
- `CHECK_INTERVAL` (optional, default: `5`)
- `BAND_SIZE_TL` (optional, default: `1`)
- `MIN_STOP_DISTANCE_TL` (optional, default: `0.5`)
- `MAX_STOP_DISTANCE_TL` (optional, default: `20`)
- `ALERT_COOLDOWN_SEC` (optional, default: `180`)
- `ANALYSIS_REFRESH_SEC` (optional, default: `300`)
- `STRATEGY_PRESET` (optional, `AGRESIF` / `DENGELI` / `KORUMACI`, default: `DENGELI`)
- `DECISION_ALERT_COOLDOWN_SEC` (optional, default: `3600`)
- `NEWS_LOOKBACK_HOURS` (optional, default: `72`)
- `AL_THRESHOLD` (optional, default: `72`)
- `SAT_THRESHOLD` (optional, default: `38`)
- `TECH_WEIGHT` (optional, default: `0.45`)
- `FUND_WEIGHT` (optional, default: `0.25`)
- `NEWS_WEIGHT` (optional, default: `0.20`)
- `REGIME_WEIGHT` (optional, default: `0.10`)
- `BACKTEST_INITIAL_CAPITAL` (optional, default: `100000`)
- `DECISION_LOG_LIMIT` (optional, default: `200`)
- `DAILY_RISK_CAP_PERCENT` (optional, default: `6`)
- `MAX_ACTIVE_POSITIONS` (optional, default: `2`)
- `MAX_POSITIONS_PER_SECTOR` (optional, default: `1`)
- `PARTIAL_TP1_RATIO` (optional, default: `0.5`)
- `TRAILING_STOP_PCT` (optional, default: `1.2`)
- `AUTO_PRESET_BY_REGIME` (optional, default: `true`)
- `DAILY_REPORT_HOUR` (optional, default: `18`)

Keep `RUN_MONITOR_IN_WEB=false` in web so only the worker sends alerts.

If `RUN_MONITOR_IN_WEB=true`, bands are automatically recentered around the latest breakout price using `BAND_SIZE_TL`.
Alerts are rate-limited per symbol with `ALERT_COOLDOWN_SEC`, and alerts are skipped if stop distance is outside `MIN_STOP_DISTANCE_TL` and `MAX_STOP_DISTANCE_TL`.
Decision Engine v3 uses weighted factors (technical + fundamental + news + market regime) and outputs `AL / BEKLE / SAT` with entry, stop, target, risk and confidence score.
Market session checks run with Istanbul time (`Europe/Istanbul`).

Strategy presets:
- `AGRESIF`: lower AL threshold, more signals, faster decision alerts
- `DENGELI`: balanced defaults for normal usage
- `KORUMACI`: higher AL threshold, fewer signals, slower decision alerts
- Auto mode (`AUTO_PRESET_BY_REGIME=true`) dynamically switches preset by market regime score.

If you set custom `AL_THRESHOLD`, `SAT_THRESHOLD` or weight envs, they override preset defaults.

Risk Engine v2:
- New positions are blocked when daily risk budget is exceeded
- New positions are blocked when max concurrent positions is reached
- New positions are blocked when sector concentration limit is reached

Exit Management v2:
- At TP1, position closes partially with `PARTIAL_TP1_RATIO`
- Remaining lot moves to break-even then trailing stop updates by `TRAILING_STOP_PCT`
- Position closes fully at TP2 or trailing stop hit

Limitations (free data):
- No live orderbook/kademe depth (free sources are limited)
- News/KAP effect is keyword-based and should be treated as decision support, not certainty

## New API endpoints

- `GET /api/decision-log?symbol=TUPRS.IS&limit=50`
  - Returns recent decision journal records
- `GET /api/backtest?symbol=TUPRS.IS&days=365&capital=100000`
  - Runs a quick historical simulation and returns metrics + last trades
- `GET /api/risk-state`
  - Returns daily risk usage, open positions and configured limits
- `GET /api/performance`
  - Returns daily realized PnL, expectancy, decision counts and recent events
- `GET /api/calibrate?symbol=TUPRS.IS&days=540&train=180&test=60&capital=100000`
  - Runs walk-forward calibration and returns recommended preset

Backtest note:
- Fundamental/news factors are fixed at neutral score in historical simulation due free-data limitations; result is primarily technical+regime performance estimation.

Daily summary:
- Bot sends one daily performance summary to Telegram at `DAILY_REPORT_HOUR` (Istanbul time).

## Render deploy

This repo includes `render.yaml` for Render Blueprint deployment.

1. Push to GitHub.
2. In Render: New + -> Blueprint.
3. Select this repo and deploy.
4. Set `TOKEN` and `CHAT_ID` on both services.

Services:

- `borsa-telegram-web` (`gunicorn app:app --bind 0.0.0.0:$PORT`)
- `borsa-telegram-worker` (`python worker.py`)
