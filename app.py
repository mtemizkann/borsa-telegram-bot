import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, render_template_string, jsonify
from datetime import datetime

# ================== CONFIG ==================
ACCOUNT_SIZE = 150000      # TL
RISK_PERCENT = 2           # %
CHECK_INTERVAL = 15        # saniye

WATCHLIST = {
    "ASELS.IS": {"lower": 290, "upper": 310, "alerted": None},
    "TUPRS.IS": {"lower": 140, "upper": 170, "alerted": None},
    "FROTO.IS": {"lower": 850, "upper": 900, "alerted": None},
}

# ================== APP ==================
app = Flask(__name__)
TICKERS = {s: yf.Ticker(s) for s in WATCHLIST}


# ================== MARKET ==================
def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18


# ================== PRICE ==================
def get_prices():
    prices = {}
    for s in WATCHLIST:
        try:
            h = TICKERS[s].history(period="1d", interval="1m")
            prices[s] = round(float(h["Close"].iloc[-1]), 2) if not h.empty else None
        except:
            prices[s] = None
    return prices


# ================== MOMENTUM ==================
def momentum(symbol):
    try:
        h = TICKERS[symbol].history(period="1d", interval="1m").tail(6)
        closes = h["Close"].tolist()
        up = sum(1 for i in range(1, 6) if closes[i] > closes[i - 1])
        down = sum(1 for i in range(1, 6) if closes[i] < closes[i - 1])
        if up >= 4:
            return "UP"
        if down >= 4:
            return "DOWN"
        return "FLAT"
    except:
        return "FLAT"


# ================== LOT & RISK ==================
def calculate_lot(entry, stop):
    risk_tl = ACCOUNT_SIZE * (RISK_PERCENT / 100)
    per_share_risk = abs(entry - stop)
    if per_share_risk == 0:
        return 0, 0
    lot = int(risk_tl / per_share_risk)
    total_risk = lot * per_share_risk
    return lot, round(total_risk, 2)


# ================== SIGNAL ==================
def generate_signal(symbol, price):
    l = WATCHLIST[symbol]["lower"]
    u = WATCHLIST[symbol]["upper"]
    m = momentum(symbol)

    if price <= l and m == "UP":
        lot, risk = calculate_lot(price, l)
        return "AL", lot, risk

    if price >= u and m == "DOWN":
        lot, risk = calculate_lot(price, u)
        return "SAT", lot, risk

    return "BEKLE", 0, 0


# ================== API ==================
@app.route("/api/data")
def api_data():
    prices = get_prices()
    data = {}

    for s, p in prices.items():
        signal, lot, risk = generate_signal(s, p) if p else ("VERİ YOK", 0, 0)
        data[s] = {
            "price": p,
            "lower": WATCHLIST[s]["lower"],
            "upper": WATCHLIST[s]["upper"],
            "signal": signal,
            "lot": lot,
            "risk": risk,
        }

    return jsonify(data)


# ================== WEB ==================
@app.route("/")
def home():
    html = """
    <html>
    <head>
        <title>BIST Professional Trading Panel</title>
        <style>
            body { background:#0e0e0e; color:white; font-family:Arial; padding:40px; }
            table { width:100%; border-collapse:collapse; }
            th, td { padding:12px; border-bottom:1px solid #333; text-align:center; }
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
                    <th>Hisse</th>
                    <th>Fiyat</th>
                    <th>Alt</th>
                    <th>Üst</th>
                    <th>Sinyal</th>
                    <th>Lot</th>
                    <th>Risk (TL)</th>
                </tr>
            </thead>
            <tbody id="rows"></tbody>
        </table>

        <script>
        async function refresh() {
            const r = await fetch("/api/data");
            const d = await r.json();
            let html = "";
            for (const s in d) {
                const row = d[s];
                html += `
                <tr class="${row.signal}">
                    <td>${s}</td>
                    <td>${row.price ?? "-"}</td>
                    <td>${row.lower}</td>
                    <td>${row.upper}</td>
                    <td>${row.signal}</td>
                    <td>${row.lot}</td>
                    <td>${row.risk}</td>
                </tr>`;
            }
            document.getElementById("rows").innerHTML = html;
        }
        setInterval(refresh, 15000);
        refresh();
        </script>
    </body>
    </html>
    """
    return render_template_string(html)


# ================== START ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
