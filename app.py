import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, jsonify, render_template_string, request
from datetime import datetime

app = Flask(__name__)

# ================== AYARLAR ==================
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
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18


# ================== PRICE ==================
def get_prices():
    prices = {}
    for s in WATCHLIST:
        try:
            hist = TICKERS[s].history(period="1d", interval="1m")
            prices[s] = round(float(hist["Close"].iloc[-1]), 2) if not hist.empty else None
        except Exception:
            prices[s] = None
    return prices


# ================== ALARM ==================
def alarm_status(price, lower, upper):
    if price is None:
        return "VERİ YOK"
    if price <= lower:
        return "ALT ALARM"
    if price >= upper:
        return "ÜST ALARM"
    return "NORMAL"


# ================== SİNYAL ==================
def generate_signal(price, lower, upper):
    if price is None:
        return "BEKLE"
    if price <= lower:
        return "AL"
    if price >= upper:
        return "SAT"
    return "BEKLE"


# ================== LOT & RISK ==================
def calculate_position(price, lower, upper, signal):
    if signal == "BEKLE" or price is None:
        return 0, 0

    stop = upper if signal == "AL" else lower
    risk_per_share = abs(price - stop)
    if risk_per_share == 0:
        return 0, 0

    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100)
    lot = int(risk_amount / risk_per_share)
    total_risk = round(lot * risk_per_share, 2)

    return lot, total_risk


# ================== API ==================
@app.route("/api/data")
def api_data():
    prices = get_prices()
    rows = []

    for s in WATCHLIST:
        price = prices[s]
        lower = WATCHLIST[s]["lower"]
        upper = WATCHLIST[s]["upper"]

        alarm = alarm_status(price, lower, upper)
        signal = generate_signal(price, lower, upper)
        lot, risk = calculate_position(price, lower, upper, signal)

        rows.append({
            "symbol": s,
            "price": price,
            "lower": lower,
            "upper": upper,
            "alarm": alarm,
            "signal": signal,
            "lot": lot,
            "risk": risk
        })

    return jsonify(rows)


# ================== PANEL ==================
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        s = request.form["symbol"]
        WATCHLIST[s]["lower"] = float(request.form["lower"].replace(",", "."))
        WATCHLIST[s]["upper"] = float(request.form["upper"].replace(",", "."))

    html = """
<!DOCTYPE html>
<html>
<head>
<title>BIST Professional Trading System</title>
<style>
body { background:#0b0b0b; color:white; font-family:Arial; padding:40px; }
table { width:100%; border-collapse:collapse; margin-bottom:30px; }
th,td { padding:12px; border-bottom:1px solid #333; text-align:center; }
th { background:#1e1e1e; }

.alt { background:#4a0000; }
.ust { background:#003a2b; }

.badge { padding:6px 14px; border-radius:14px; font-weight:bold; }
.buy { background:#0f5132; }
.sell { background:#842029; }
.wait { background:#41464b; }

input,select { padding:8px; }
button { padding:10px 20px; background:#0a84ff; border:none; color:white; }
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
<th>Alarm</th>
<th>Sinyal</th>
<th>Lot</th>
<th>Risk (TL)</th>
</tr>
</thead>
<tbody id="table"></tbody>
</table>

<h3>Limit Güncelle</h3>
<form method="post">
<select name="symbol">
{% for s in watchlist %}
<option>{{s}}</option>
{% endfor %}
</select>
<input name="lower" placeholder="Alt">
<input name="upper" placeholder="Üst">
<button>Güncelle</button>
</form>

<script>
async function refresh(){
 const r = await fetch("/api/data");
 const d = await r.json();
 const tb = document.getElementById("table");
 tb.innerHTML = "";

 d.forEach(row=>{
   let tr = document.createElement("tr");
   if(row.alarm==="ALT ALARM") tr.className="alt";
   if(row.alarm==="ÜST ALARM") tr.className="ust";

   tr.innerHTML = `
   <td>${row.symbol}</td>
   <td>${row.price ?? "-"}</td>
   <td>${row.lower}</td>
   <td>${row.upper}</td>
   <td>${row.alarm}</td>
   <td><span class="badge ${row.signal==="AL"?"buy":row.signal==="SAT"?"sell":"wait"}">${row.signal}</span></td>
   <td>${row.lot}</td>
   <td>${row.risk}</td>
   `;
   tb.appendChild(tr);
 });
}
setInterval(refresh,15000);
refresh();
</script>

</body>
</html>
"""
    return render_template_string(html, watchlist=WATCHLIST)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
