# app.py
# -*- coding: utf-8 -*-

import os
import time
import threading
import requests
import pandas as pd
from dataclasses import dataclass, asdict
from typing import Dict
from flask import Flask, jsonify, render_template_string, request

# -------------------------
# ENV
# -------------------------
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "55f5c55b141b4ffc90f614ce796829b8").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "1090532341").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1090532341").strip()
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "180"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "tv_super_secret_2026").strip()

# ========================
# CONFIG
# ========================
WATCHLIST = ["FROTO", "TUPRS", "ASELS", "MGROS"]

SYMBOL_MAP: Dict[str, str] = {
    "FROTO": "FROTO:BIST",
    "TUPRS": "TUPRS:BIST",
    "ASELS": "ASELS:BIST",
    "MGROS": "MGROS:BIST",
}

# ========================
# INDICATORS
# ========================
def ema(series: pd.Series, span: int):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss.replace(0, pd.NA))
    return 100 - (100 / (1 + rs))

# ========================
# DATA
# ========================
def fetch_ohlc(symbol: str):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": "260",
        "apikey": TWELVEDATA_API_KEY,
    }

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    if data.get("status") == "error":
        raise RuntimeError(data.get("message"))

    values = list(reversed(data.get("values", [])))
    if not values:
        raise RuntimeError("No data")

    df = pd.DataFrame(values)
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["datetime"] = pd.to_datetime(df["datetime"])
    df.set_index("datetime", inplace=True)
    return df.dropna()

# ========================
# TELEGRAM
# ========================
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }, timeout=10)

# ========================
# ENGINE
# ========================
@dataclass
class SignalResult:
    symbol: str
    price: float
    signal: str
    note: str

STATE = {}
LOCK = threading.Lock()
WORKER_STARTED = False

def analyze(symbol: str):
    df = fetch_ohlc(SYMBOL_MAP[symbol])
    close = df["close"]
    low = df["low"]

    price = float(close.iloc[-1])
    ema50 = float(ema(close, 50).iloc[-1])
    ema200 = float(ema(close, 200).iloc[-1])
    rsi14 = float(rsi(close, 14).iloc[-1])

    trend_ok = price > ema50 and ema50 > ema200
    rsi_ok = 40 <= rsi14 <= 55
    signal = "AL" if trend_ok and rsi_ok else "BEKLE"

    return SignalResult(symbol, price, signal, f"RSI:{rsi14:.1f}")

def refresh():
    for s in WATCHLIST:
        try:
            res = analyze(s)
            with LOCK:
                STATE[s] = res

            if res.signal == "AL":
                send_telegram(f"📌 {s} AL\nFiyat: {res.price:.2f}")

        except Exception as e:
            with LOCK:
                STATE[s] = SignalResult(s, 0, "HATA", str(e))

def worker():
    while True:
        refresh()
        time.sleep(CHECK_INTERVAL_SEC)

def start_background():
    global WORKER_STARTED
    if WORKER_STARTED:
        return
    WORKER_STARTED = True
    t = threading.Thread(target=worker, daemon=True)
    t.start()

# ========================
# FLASK
# ========================
app = Flask(__name__)

@app.route("/")
def home():
    with LOCK:
        data = {k: asdict(v) for k, v in STATE.items()}

    html = """
    <h2>Smart Signal Panel</h2>
    <table border="1" cellpadding="8">
        <tr><th>Sembol</th><th>Fiyat</th><th>Sinyal</th><th>Not</th></tr>
        {% for s, d in data.items() %}
        <tr>
            <td>{{ s }}</td>
            <td>{{ "%.2f"|format(d.price) if d.price > 0 else "-" }}</td>
            <td>{{ d.signal }}</td>
            <td>{{ d.note }}</td>
        </tr>
        {% endfor %}
    </table>
    """
    return render_template_string(html, data=data)

@app.route("/api/state")
def state():
    with LOCK:
        return jsonify({k: asdict(v) for k, v in STATE.items()})

# ========================
# START (GUNICORN SAFE)
# ========================
start_background()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
