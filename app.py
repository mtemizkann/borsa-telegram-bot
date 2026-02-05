import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify
from datetime import datetime

app = Flask(__name__)

# ================== CONFIG ==================
ACCOUNT_SIZE = 150000
RISK_PERCENT = 2

WATCHLIST = {
    "ASELS.IS": {"lower": 290, "upper": 310},
    "TUPRS.IS": {"lower": 140, "upper": 170},
    "FROTO.IS": {"lower": 850, "upper": 900},
}

TICKERS = {s: yf.Ticker(s) for s in WATCHLIST}

# ================== MARKET ==================
def market_open():
    now = datetime.now()
    return now.weekday() < 5 and 9 <= now.hour < 18

# ================== DATA ==================
def get_price_data(symbol):
    try:
        df = TICKERS[symbol].history(period="1d", interval="5m")
        if len(df) < 5:
            return None
        return df
    except:
        return None

# ================== CONFIDENCE ENGINE ==================
def calculate_confidence(price, lower, upper, df):
    score = 0

    # 1️⃣ Seviye yakınlığı (%40)
    range_size = upper - lower
    if price <= lower:
        score += 40
    elif price >= upper:
        score += 40
    else:
        dist = min(abs(price - lower), abs(price - upper))
        score += max(0, 40 - (dist / range_size) * 40)

    # 2️⃣ Momentum (%40)
    closes = df["Close"].iloc[-4:]
    momentum = closes.diff().sum()
    if momentum > 0:
        score += 40
    elif momentum < 0:
        score += 20

    # 3️⃣ Sahte kırılım filtresi (%20)
    if closes.iloc[-1] > closes.mean():
        score += 20

    return round(min(score, 100), 1)

# ================== SIGNAL ==================
def generate_signal(price, lower, upper, confidence):
    if confidence < 40:
        return "BEKLE"
    if price <= lower:
        return "AL"
    if price >= upper:
        return "SAT"
    return "BEKLE"

# ================== LOT & RISK ==================
def calculate_lot(price, stop, confidence):
    if confidence < 50:
        return 0, 0

    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100) * (confidence / 100)
    per_unit_risk = abs(price - stop)
    if per_unit_risk == 0:
        return 0, 0

    lot = int(risk_amount / per_unit_risk)
    return lot, round(lot * per_unit_risk, 2)

# ================== API ==================
@app.route("/api/data")
def api_data():
    result = []

    for s, limits in WATCHLIST.items():
        df = get_price_data(s)
        if df is None:
            continue

        price = round(float(df["Close"].iloc[-1]), 2)
        lower, upper = limits["lower"], limits["upper"]

        confidence = calculate_confidence(price, lower, upper, df)
        signal = generate_signal(price, lower, upper, confidence)

        stop = upper if signal == "AL" else lower
        lot, risk = calculate_lot(price, stop, confidence)

        alarm = "NORMAL"
        if price <= lower:
            alarm = "ALT ALARM"
        elif price >= upper:
            alarm = "ÜST ALARM"

        result.append({
            "symbol": s,
            "price": price,
            "lower": lower,
            "upper": upper,
            "alarm": alarm,
            "signal": signal,
            "confidence": confidence,
            "lot": lot,
            "risk": risk
        })

    return jsonify(result)

# ================== UI ==================
@app.route("/")
def home():
    html = """
    <html>
    <head>
        <title>BIST Profesyonel Trading Sistemi</title>
        <style>
            body { background:#0e0e0e; color:white; font-family:Arial; padding:40px; }
            table { width:100%; border-collapse:collapse; }
            th, td { padding:12px; text-align:center; border-bottom:1px solid #333; }
            th { background:#1e1e1e; }
            .AL { background:#0f5132; }
            .SAT { background:#842029; }
            .BEKLE { background:#41464b; }
        </style>
    </head>
    <body>
        <h2>📊 BIST Profesyonel Trading Sistemi</h2>
        <table>
            <thead>
                <tr>
                    <th>Hisse</th><th>Fiyat</th><th>Alt</th><th>Üst</th>
                    <th>Alarm</th><th>Sinyal</th><th>Confidence %</th>
                    <th>Lot</th><th>Risk (TL)</th>
                </tr>
            </thead>
            <tbody id="body"></tbody>
        </table>

        <script>
        async function load() {
            const r = await fetch("/api/data");
            const d = await r.json();
            const b = document.getElementById("body");
            b.innerHTML = "";
            d.forEach(x => {
                b.innerHTML += `
                <tr class="${x.signal}">
                    <td>${x.symbol}</td>
                    <td>${x.price}</td>
                    <td>${x.lower}</td>
                    <td>${x.upper}</td>
                    <td>${x.alarm}</td>
                    <td>${x.signal}</td>
                    <td>%${x.confidence}</td>
                    <td>${x.lot}</td>
                    <td>${x.risk}</td>
                </tr>`;
            });
        }
        setInterval(load, 15000);
        load();
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

# ================== START ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
