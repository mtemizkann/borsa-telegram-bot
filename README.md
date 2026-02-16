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

Keep `RUN_MONITOR_IN_WEB=false` in web so only the worker sends alerts.

If `RUN_MONITOR_IN_WEB=true`, bands are automatically recentered around the latest breakout price using `BAND_SIZE_TL`.
Alerts are rate-limited per symbol with `ALERT_COOLDOWN_SEC`, and alerts are skipped if stop distance is outside `MIN_STOP_DISTANCE_TL` and `MAX_STOP_DISTANCE_TL`.

## Render deploy

This repo includes `render.yaml` for Render Blueprint deployment.

1. Push to GitHub.
2. In Render: New + -> Blueprint.
3. Select this repo and deploy.
4. Set `TOKEN` and `CHAT_ID` on both services.

Services:

- `borsa-telegram-web` (`gunicorn app:app --bind 0.0.0.0:$PORT`)
- `borsa-telegram-worker` (`python worker.py`)
