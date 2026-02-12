# app.py
# -*- coding: utf-8 -*-

import os
import time
import threading
from math import floor
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple

import requests
import pandas as pd
from flask import Flask, jsonify, request, render_template_string

# -------------------------
# ENV
# -------------------------
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "55f5c55b141b4ffc90f614ce796829b8").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "1090532341").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1090532341").strip()
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "180"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "tv_super_secret_2026").strip()

WATCHLIST = ["FROTO", "TUPRS", "ASELS", "MGROS"]

SYMBOL_MAP: Dict[str, str] = {
    "FROTO": "FROTO.IS",
    "TUPRS": "TUPRS.IS",
    "ASELS": "ASELS.IS",
    "MGROS": "MGROS.IS",
}

BUDGETS_TRY = {
    "FROTO": 50000,
    "TUPRS": 50000,
    "ASELS": 50000,
    "MGROS": 25000,
}

# -------------------------
# INDICATORS
# -------------------------
def ema(series: pd.Series, span: int):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss.replace(0, pd.NA))
    return 100 - (100 / (1 + rs))

# -------------------------
# DATA
# -------------------------
def fetch_ohlc(symbol: str):
    if not TWELVEDATA_API_KEY:
        raise RuntimeError("TWELVEDATA_API_KEY missing")

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
        raise RuntimeError("No data returned")
    
    df = pd.DataFrame(values)

    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["datetime"] = pd.to_datetime(df["datetime"])
    df.set_index("datetime", inplace=True)

    return df.dropna(subset=["close", "low"])

# -------------------------
# TELEGRAM
# -------------------------
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram credentials missing")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text
        }, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

# -------------------------
# ENGINE
# -------------------------
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
    df = fetch_ohlc(SYMBOL_MAP.get(symbol, symbol))
    if len(df) < 220:
        raise RuntimeError("Not enough data")

    close = df["close"]
    low = df["low"]

    price = float(close.iloc[-1])
    ema50 = float(ema(close, 50).iloc[-1])
    ema200 = float(ema(close, 200).iloc[-1])
    rsi14 = float(rsi(close, 14).iloc[-1])

    if pd.isna(price) or pd.isna(ema50) or pd.isna(ema200) or pd.isna(rsi14):
        raise RuntimeError("Indicator calculation failed (NaN)")

    support = float(low.tail(20).min())
    stop = support * 0.98
    risk = price - stop
    target = price + 2 * risk if risk > 0 else price

    trend_ok = price > ema50 and ema50 > ema200
    rsi_ok = (rsi14 < 30) or (40 <= rsi14 <= 55)
    support_ok = (price - support) / price <= 0.03 if price > 0 else False
    rr_ok = risk > 0 and (target - price) / risk >= 2

    signal = "AL" if all([trend_ok, rsi_ok, support_ok, rr_ok]) else "BEKLE"
    note = f"RSI:{rsi14:.1f} | Trend:{'✓' if trend_ok else '✗'}"

    return SignalResult(symbol, price, signal, note)

def refresh():
    for s in WATCHLIST:
        try:
            print(f"📊 Fetching {s}...")
            res = analyze(s)
            print(f"✓ {s}: {res.signal} @ {res.price:.2f}")

            with LOCK:
                STATE[s] = res

            if res.signal == "AL":
                send_telegram(f"📌 {s} AL SİNYALİ\nFiyat: {res.price:.2f}\n{res.note}")

        except Exception as e:
            print(f"✗ {s} ERROR: {e}")
            with LOCK:
                STATE[s] = SignalResult(s, 0, "HATA", str(e))

def worker():
    """Background thread - gunicorn ile çalışır"""
    print(f"🔄 Worker started (interval: {CHECK_INTERVAL_SEC}s)")
    while True:
        refresh()
        time.sleep(CHECK_INTERVAL_SEC)

def start_background():
    """Thread'i sadece bir kez başlat"""
    global WORKER_STARTED
    if WORKER_STARTED:
        return
    
    WORKER_STARTED = True
    print("🚀 Starting background worker...")
    
    # İlk veri çekimi
    try:
        refresh()
    except Exception as e:
        print(f"Initial fetch error: {e}")
    
    # Arka plan thread
    t = threading.Thread(target=worker, daemon=True)
    t.start()

# -------------------------
# FLASK
# -------------------------
app = Flask(__name__)

@app.route("/")
def home():
    with LOCK:
        data = {k: asdict(v) for k, v in STATE.items()}
    
    html = """
    <html>
    <head>
        <meta charset="utf-8">
        <title>Signal Panel</title>
        <style>
            body { font-family: Arial; padding: 20px; background: #f5f5f5; }
            h2 { color: #333; }
            table { border-collapse: collapse; width: 100%; background: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
            th { background: #4CAF50; color: white; }
            .AL { background: #e8f5e9; }
            .BEKLE { background: #fff9c4; }
            .HATA { background: #ffebee; }
        </style>
    </head>
    <body>
        <h2>📊 Smart Signal Panel</h2>
        <p>Interval: <b>{{ interval }}s</b> | Watchlist: <b>{{ watchlist }}</b></p>
        <table>
            <tr><th>Sembol</th><th>Fiyat</th><th>Sinyal</th><th>Not</th></tr>
            {% for s, d in data.items() %}
            <tr class="{{ d.signal }}">
                <td><b>{{ s }}</b></td>
                <td>{{ "%.2f"|format(d.price) if d.price > 0 else "-" }}</td>
                <td><b>{{ d.signal }}</b></td>
                <td>{{ d.note }}</td>
            </tr>
            {% endfor %}
        </table>
        <p style="margin-top: 20px;">
            <a href="/api/state">JSON API</a> | 
            <a href="/api/refresh?key={{ secret }}">Manuel Refresh</a>
        </p>
    </body>
    </html>
    """
    return render_template_string(
        html, 
        data=data, 
        interval=CHECK_INTERVAL_SEC,
        watchlist=", ".join(WATCHLIST),
        secret=WEBHOOK_SECRET
    )

@app.route("/api/state")
def state():
    with LOCK:
        return jsonify({k: asdict(v) for k, v in STATE.items()})

@app.route("/api/refresh")
def manual_refresh():
    if WEBHOOK_SECRET:
        if request.args.get("key") != WEBHOOK_SECRET:
            return {"error": "unauthorized"}, 401
    refresh()
    return {"ok": True}

# -------------------------
# GUNICORN UYUMLU BAŞLATMA
# -------------------------
# Gunicorn modülü import ettiğinde otomatik çalışır
start_background()

# Yerel test için
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
