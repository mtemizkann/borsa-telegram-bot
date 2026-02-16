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

Keep `RUN_MONITOR_IN_WEB=false` in web so only the worker sends alerts.

## Render deploy

This repo includes `render.yaml` for Render Blueprint deployment.

1. Push to GitHub.
2. In Render: New + -> Blueprint.
3. Select this repo and deploy.
4. Set `TOKEN` and `CHAT_ID` on both services.

Services:

- `borsa-telegram-web` (`gunicorn app:app --bind 0.0.0.0:$PORT`)
- `borsa-telegram-worker` (`python worker.py`)
