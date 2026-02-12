# app.py
# -*- coding: utf-8 -*-

"""
Smart Alert Engine (BIST) - Flask + TwelveData + Telegram
Production-safe version
"""

import os
import time
import threading
from math import floor
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple

import requests
import pandas as pd
from flask import Flask, jsonify, request, render_template_string

# -------------------------------------------------
# ENV VARIABLES (ASLA BURAYA KEY YAZMA)
# -------------------------------------------------
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "180"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
WATCHLIST = ["FROTO", "TUPRS", "ASELS", "MGROS"]

SYMBOL_MAP: Dict[str, str] = {
    "FROTO": "FROTO",
    "TUPRS": "TUPRS",
    "ASELS": "ASELS",
    "MGROS": "MGROS",
}

BUDGETS_TRY: Dict[str, int] = {
    "FROTO": 50000,
    "TUPRS": 50000,
    "ASELS": 50000,
    "MGROS": 25000,
}

# -------------------------------------------------
# INDICATORS
# -------------------------------------------------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss.replace(0, pd.NA))
    return 100 - (100 / (1 + rs))

# -------------------------------------------------
# DATA FETCH
# -------------------------------------------------
def fetch_ohlc(symbol: str) -> pd.DataFrame:
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
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime")
    df = df.dropna(subset=["close", "low"])
    return df

# -------------------------------------------------
# TELEGRAM
# -------------------------------------------------
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text
        }, timeout=10)
    except Exception as e:
        print("Telegram error:", e)

# -------------------------------------------------
# ENGINE
# -------------------------------------------------
@dataclass
class SignalResult:
    symbol: str
    price: float
    signal: str
    rsi: float
    support: float
    stop: float
    target: float
    rr: float
    k1: float
    k2: float
    note: str

def analyze(df: pd.DataFrame, symbol: str) -> SignalResult:

    close = df["close"]
    low = df["low"]

    price = float(close.iloc[-1])
    ema50 = float(ema(close, 50).iloc[-1])
    ema200 = float(ema(close, 200).iloc[-1])
    rsi_v = float(rsi(close).iloc[-1])

    support = float(low.tail(20).min())
    stop = support * 0.98
    risk = price - stop
    target = price + (2 * risk) if risk > 0 else price
    rr = (target - price) / risk if risk > 0 else 0

    trend_ok = price > ema50 and ema50 > ema200
    rsi_ok = rsi_v < 30 or 40 <= rsi_v <= 55
    support_ok = (price - support) / price <= 0.03
    rr_ok = rr >= 2

    signal = "AL" if trend_ok and rsi_ok and support_ok and rr_ok else "BEKLE"

    if trend_ok:
        k1 = round(price * 0.995, 2)
        k2 = round(price * 0.988, 2)
    else:
        k1 = round(price * 0.990, 2)
        k2 = round(price * 0.975, 2)

    note = "OK" if signal == "AL" else "Filtrelerden biri sağlanmadı"

    return SignalResult(
        symbol=symbol,
        price=price,
        signal=signal,
        rsi=rsi_v,
        support=support,
        stop=stop,
        target=target,
        rr=rr,
        k1=k1,
        k2=k2,
        note=note
    )

# -------------------------------------------------
# STATE
# -------------------------------------------------
STATE_LOCK = threading.Lock()
LATEST: Dict[str, SignalResult] = {}

def refresh():
    for sym in WATCHLIST:
        mapped = SYMBOL_MAP.get(sym, sym)
        try:
            df = fetch_ohlc(mapped)
            res = analyze(df, sym)
            with STATE_LOCK:
                LATEST[sym] = res

            if res.signal == "AL":
                send_telegram(
                    f"📌 {sym} AL Sinyali\n"
                    f"Fiyat: {res.price}\n"
                    f"Stop: {res.stop}\n"
                    f"Hedef: {res.target}\n"
                    f"RR: {res.rr:.2f}"
                )

        except Exception as e:
            print(sym, "error:", e)

def loop():
    while True:
        refresh()
        time.sleep(CHECK_INTERVAL_SEC)

# -------------------------------------------------
# FLASK
# -------------------------------------------------
app = Flask(__name__)

@app.route("/api/state")
def api_state():
    with STATE_LOCK:
        return jsonify({k: asdict(v) for k, v in LATEST.items()})

@app.route("/api/refresh")
def api_refresh():
    if WEBHOOK_SECRET and request.args.get("key") != WEBHOOK_SECRET:
        return jsonify({"error": "unauthorized"}), 401
    refresh()
    return jsonify({"ok": True})

# -------------------------------------------------
# MAIN
# -------------------------------------------------
if __name__ == "__main__":
    print("Starting Smart Engine...")
    refresh()
    threading.Thread(target=loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
