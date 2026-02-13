import os
import json
import time
import threading
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import requests
import yfinance as yf
import pandas as pd
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

# ================= ENV =================
TOKEN = os.environ.get("TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

ACCOUNT_SIZE = float(os.environ.get("ACCOUNT_SIZE", "150000").replace(",", "."))
RISK_PERCENT = float(os.environ.get("RISK_PERCENT", "2").replace(",", "."))

# ================= STATE =================
WATCHLIST: Dict[str, Dict[str, Any]] = {
    "ASELS.IS": {},
    "TUPRS.IS": {},
    "FROTO.IS": {},
}

_state_lock = threading.Lock()

# ================= HELPERS =================
def safe_round(x: Any, ndigits: int = 2):
    try:
        return round(float(x), ndigits)
    except:
        return None

def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18

def send_telegram(message: str):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message},
            timeout=5,
        )
    except:
        pass

# ================= SMART ANALYSIS =================
def calculate_indicators(symbol: str):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo", interval="1d")

        if hist is None or hist.empty or len(hist) < 50:
            return None

        hist = hist.dropna()

        # EMA
        hist["EMA20"] = hist["Close"].ewm(span=20).mean()
        hist["EMA50"] = hist["Close"].ewm(span=50).mean()

        # RSI
        delta = hist["Close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()

        if avg_loss.iloc[-1] == 0:
            return None

        rs = avg_gain / avg_loss
        hist["RSI"] = 100 - (100 / (1 + rs))

        latest = hist.iloc[-1]

        high20 = hist["High"].tail(20).max()
        low20 = hist["Low"].tail(20).min()

        if high20 == low20:
            return None

        range_pos = (latest["Close"] - low20) / (high20 - low20)

        return {
            "price": float(latest["Close"]),
            "ema20": float(latest["EMA20"]),
            "ema50": float(latest["EMA50"]),
            "rsi": float(latest["RSI"]),
            "range_pos": float(range_pos),
        }

    except Exception as e:
        print("Indicator error:", e)
        return None


def generate_smart_signal(data):
    if not data:
        return "VERİ YOK", 0

    score = 0

    # Trend
    if data["ema20"] > data["ema50"]:
        score += 30
    else:
        score -= 10

    # RSI dip bölgesi
    if 35 < data["rsi"] < 55:
        score += 30
    elif data["rsi"] > 70:
        score -= 20

    # Range alt bölge
    if data["range_pos"] < 0.4:
        score += 20
    elif data["range_pos"] > 0.8:
        score -= 20

    confidence = max(0, min(score, 100))

    if score >= 60:
        return "AL", confidence
    elif score <= 20:
        return "SAT", confidence
    else:
        return "BEKLE", confidence

# ================= API =================
@app.route("/api/data")
def api_data():

    result = {}

    with _state_lock:
        symbols = list(WATCHLIST.keys())

    for s in symbols:
        data = calculate_indicators(s)
        if not data:
            continue

        signal, confidence = generate_smart_signal(data)

        result[s] = {
            "price": safe_round(data["price"]),
            "signal": signal,
            "confidence": confidence
        }

    return jsonify(result)

# ================= PANEL =================
@app.route("/")
def home():

    html = """
    <html>
    <head>
    <title>BIST Smart AI Panel</title>
    <style>
    body{background:#0d1117;color:white;font-family:Arial;padding:40px}
    table{width:100%;margin-top:30px}
    th,td{text-align:center;padding:15px}
    .buy{background:#0f5132}
    .sell{background:#842029}
    .wait{background:#444}
    </style>
    </head>
    <body>
    <h1>📊 BIST Smart AI Panel</h1>

    <table border="0">
    <thead>
    <tr>
    <th>Hisse</th>
    <th>Fiyat</th>
    <th>Sinyal</th>
    <th>Confidence</th>
    </tr>
    </thead>
    <tbody id="table"></tbody>
    </table>

    <script>
    async function load(){
        const r = await fetch("/api/data");
        const d = await r.json();

        const table = document.getElementById("table");
        table.innerHTML = "";

        for(const s in d){
            const row = document.createElement("tr");

            let cls="wait";
            if(d[s].signal==="AL") cls="buy";
            if(d[s].signal==="SAT") cls="sell";

            row.classList.add(cls);

            row.innerHTML = `
                <td>${s}</td>
                <td>${d[s].price}</td>
                <td>${d[s].signal}</td>
                <td>%${d[s].confidence}</td>
            `;

            table.appendChild(row);
        }
    }

    setInterval(load,15000);
    load();
    </script>

    </body>
    </html>
    """

    return render_template_string(html)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
