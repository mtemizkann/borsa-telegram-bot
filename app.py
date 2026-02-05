import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify
from datetime import datetime

app = Flask(__name__)

# ================== CONFIG ==================
TOKEN = os.environ.get("TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

ACCOUNT_SIZE = 150000
RISK_PERCENT = 2
REFRESH_SECONDS = 10

WATCHLIST = {
    "ASELS.IS": {"lower": 290, "upper": 310, "alerted": None},
    "TUPRS.IS": {"lower": 140, "upper": 170, "alerted": None},
    "FROTO.IS": {"lower": 850, "upper": 900, "alerted": None},
}

TICKERS = {s: yf.Ticker(s) for s in WATCHLIST}


# ================== HELPERS ==================
def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18


def send_telegram(msg):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=5
        )
    except Exception:
        pass


def get_prices():
    prices = {}
    for s in WATCHLIST:
        try:
            hist = TICKERS[s].history(period="1d", interval="1m")
            prices[s] = round(float(hist["Close"].iloc[-1]), 2) if not hist.empty else None
        except Exception:
            prices[s] = None
    return prices


def generate_signal(price, lower, upper):
    if price is None:
        return "VERİ YOK", 0

    distance = abs(upper - lower)
    if distance == 0:
        return "BEKLE", 0

    if price <= lower:
        confidence = min(90, int((lower - price) / distance * 100) + 50)
        return "AL", confidence

    if price >= upper:
        confidence = min(90, int((price - upper) / distance * 100) + 50)
        return "SAT", confidence

    return "BEKLE", 50


def calculate_lot(price, stop_distance):
    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100)
    if stop_distance <= 0:
        return 0, 0
    lot = int(risk_amount / stop_distance)
    return lot, round(lot * stop_distance, 2)


# ================== API ==================
@app.route("/api/data")
def api_data():
    prices = get_prices()
    rows = []

    for s, cfg in WATCHLIST.items():
        price = prices[s]
        signal, conf = generate_signal(price, cfg["lower"], cfg["upper"])

        lot, risk = (0, 0)
        if signal in ["AL", "SAT"] and price:
            stop_dist = abs(cfg["upper"] - cfg["lower"]) * 0.3
            lot, risk = calculate_lot(price, stop_dist)

        rows.append({
            "symbol": s,
            "price": price,
            "lower": cfg["lower"],
            "upper": cfg["upper"],
            "signal": signal,
            "confidence": conf,
            "lot": lot,
            "risk": risk
        })

    return jsonify(rows)


# ================== UI ==================
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        symbol = request.form["symbol"]
        WATCHLIST[symbol]["lower"] = float(request.form["lower"].replace(",", "."))
        WATCHLIST[symbol]["upper"] = float(request.form["upper"].replace(",", "."))
        WATCHLIST[symbol]["alerted"] = None

    html = """
<!DOCTYPE html>
<html>
<head>
<title>BIST Profesyonel Trading Sistemi</title>
<style>
body {
    background:#0b0b0b;
    color:#eaeaea;
    font-family: Inter, Arial;
    padding:40px;
}
h1 {
    font-weight:600;
    margin-bottom:20px;
}
.card {
    background:#121212;
    border-radius:14px;
    padding:24px;
    box-shadow:0 0 0 1px rgba(255,255,255,0.05);
}
table {
    width:100%;
    border-collapse:collapse;
}
th, td {
    padding:14px;
    border-bottom:1px solid #1f1f1f;
    text-align:center;
}
th {
    color:#aaa;
    font-weight:500;
}
tr.buy { background:#0f2a1f; }
tr.sell { background:#2a1212; }
.badge {
    padding:6px 14px;
    border-radius:999px;
    font-weight:600;
    font-size:13px;
}
.al { background:#1f7a4f; }
.sat { background:#7a1f1f; }
.bekle { background:#444; }
form {
    margin-top:30px;
    display:flex;
    gap:10px;
}
input, select {
    padding:10px;
    border-radius:8px;
    border:1px solid #333;
    background:#0f0f0f;
    color:white;
}
button {
    padding:10px 18px;
    border-radius:8px;
    background:#2563eb;
    color:white;
    border:none;
    cursor:pointer;
}
.footer {
    margin-top:16px;
    color:#777;
    font-size:13px;
}
</style>
</head>
<body>

<h1>📊 BIST Profesyonel Trading Sistemi</h1>

<div class="card">
<table>
<thead>
<tr>
<th>Hisse</th>
<th>Fiyat</th>
<th>Alt</th>
<th>Üst</th>
<th>Sinyal</th>
<th>Confidence</th>
<th>Lot</th>
<th>Risk (TL)</th>
</tr>
</thead>
<tbody id="rows"></tbody>
</table>

<div class="footer">
Otomatik yenileme: {{refresh}} sn • Manuel limit sistemi
</div>
</div>

<div class="card" style="margin-top:30px;">
<h3>Limit Güncelle</h3>
<form method="post">
<select name="symbol">
{% for s in watchlist %}
<option value="{{s}}">{{s}}</option>
{% endfor %}
</select>
<input name="lower" placeholder="Alt limit">
<input name="upper" placeholder="Üst limit">
<button>Güncelle</button>
</form>
</div>

<script>
async function refresh(){
    const r = await fetch("/api/data");
    const data = await r.json();
    const tbody = document.getElementById("rows");
    tbody.innerHTML = "";

    data.forEach(row => {
        let cls = row.signal === "AL" ? "buy" : row.signal === "SAT" ? "sell" : "";
        let badgeClass = row.signal === "AL" ? "al" : row.signal === "SAT" ? "sat" : "bekle";

        tbody.innerHTML += `
        <tr class="${cls}">
            <td>${row.symbol}</td>
            <td>${row.price ?? "-"}</td>
            <td>${row.lower}</td>
            <td>${row.upper}</td>
            <td><span class="badge ${badgeClass}">${row.signal}</span></td>
            <td>%${row.confidence}</td>
            <td>${row.lot}</td>
            <td>${row.risk}</td>
        </tr>`;
    });
}
refresh();
setInterval(refresh, {{refresh}} * 1000);
</script>

</body>
</html>
"""
    return render_template_string(html, watchlist=WATCHLIST, refresh=REFRESH_SECONDS)


# ================== START ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
