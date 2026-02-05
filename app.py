import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify
from datetime import datetime
import pandas as pd

# ================= CONFIG =================
ACCOUNT_SIZE = 150000
RISK_PERCENT = 2
ATR_PERIOD = 14
STOP_MULT = 1.5
TP_MULT = 2.5
REFRESH_SEC = 10

TOKEN = os.environ.get("TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# ================= APP =================
app = Flask(__name__)

WATCHLIST = {
    "ASELS.IS": {"lower": 290, "upper": 310, "alerted": None},
    "TUPRS.IS": {"lower": 140, "upper": 170, "alerted": None},
    "FROTO.IS": {"lower": 850, "upper": 900, "alerted": None},
}

TICKERS = {s: yf.Ticker(s) for s in WATCHLIST}

# ================= UTILS =================
def market_open():
    now = datetime.now()
    return now.weekday() < 5 and 9 <= now.hour < 18

def send(msg):
    if TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": msg},
                timeout=5,
            )
        except:
            pass

# ================= DATA =================
def get_price_and_atr(symbol):
    try:
        df = TICKERS[symbol].history(period="5d", interval="15m")
        if len(df) < ATR_PERIOD:
            return None, None

        df["H-L"] = df["High"] - df["Low"]
        df["H-C"] = abs(df["High"] - df["Close"].shift())
        df["L-C"] = abs(df["Low"] - df["Close"].shift())
        tr = df[["H-L", "H-C", "L-C"]].max(axis=1)
        atr = tr.rolling(ATR_PERIOD).mean().iloc[-1]

        price = round(df["Close"].iloc[-1], 2)
        return price, round(atr, 2)
    except:
        return None, None

# ================= SIGNAL ENGINE =================
def detect_mode(price, lower, upper, atr):
    if price > upper + atr:
        return "BREAKOUT UP"
    if price < lower - atr:
        return "BREAKOUT DOWN"
    return "RANGE"

def generate_signal(price, lower, upper):
    if price <= lower:
        return "AL"
    if price >= upper:
        return "SAT"
    return "BEKLE"

def confidence(signal, mode):
    base = 60
    if signal != "BEKLE":
        base += 10
    if "BREAKOUT" in mode:
        base += 10
    return min(base, 95)

def calc_risk(price, stop):
    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100)
    per_unit = abs(price - stop)
    if per_unit == 0:
        return 0, 0
    lot = int(risk_amount / per_unit)
    return lot, round(lot * per_unit, 2)

# ================= API =================
@app.route("/api/data")
def api_data():
    rows = {}

    for s, cfg in WATCHLIST.items():
        price, atr = get_price_and_atr(s)
        if price is None:
            continue

        signal = generate_signal(price, cfg["lower"], cfg["upper"])
        mode = detect_mode(price, cfg["lower"], cfg["upper"], atr)
        conf = confidence(signal, mode)

        stop = tp = lot = risk = 0

        if signal == "AL":
            stop = round(price - atr * STOP_MULT, 2)
            tp = round(price + atr * TP_MULT, 2)
            lot, risk = calc_risk(price, stop)

        elif signal == "SAT":
            stop = round(price + atr * STOP_MULT, 2)
            tp = round(price - atr * TP_MULT, 2)
            lot, risk = calc_risk(price, stop)

        rows[s] = {
            "price": price,
            "lower": cfg["lower"],
            "upper": cfg["upper"],
            "signal": signal,
            "confidence": conf,
            "mode": mode,
            "stop": stop,
            "tp": tp,
            "lot": lot,
            "risk": risk,
        }

    return jsonify(rows)

# ================= UI =================
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        s = request.form["symbol"]
        WATCHLIST[s]["lower"] = float(request.form["lower"].replace(",", "."))
        WATCHLIST[s]["upper"] = float(request.form["upper"].replace(",", "."))
        WATCHLIST[s]["alerted"] = None

    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
<title>BIST Profesyonel Trading Sistemi</title>
<style>
body{background:#0b0b0b;color:white;font-family:Arial;padding:30px}
table{width:100%;border-collapse:collapse}
th,td{padding:12px;border-bottom:1px solid #333;text-align:center}
th{background:#1f1f1f}
.green{background:#163b2c}
.red{background:#3b1616}
.badge{padding:6px 12px;border-radius:12px;font-weight:bold}
.buy{background:#0f5132}
.sell{background:#842029}
.wait{background:#444}
small{opacity:.7}
</style>
</head>
<body>

<h2>📊 BIST Profesyonel Trading Sistemi</h2>

<table id="t">
<thead>
<tr>
<th>Hisse</th><th>Fiyat</th><th>Alt</th><th>Üst</th>
<th>Sinyal</th><th>Confidence</th>
<th>Stop</th><th>TP</th>
<th>Lot</th><th>Risk (TL)</th><th>Piyasa</th>
</tr>
</thead>
<tbody></tbody>
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
 let html="";
 for(const s in d){
  const x=d[s];
  const rowClass = x.signal=="AL"?"green":x.signal=="SAT"?"red":"";
  html+=`<tr class="${rowClass}">
  <td>${s}</td><td>${x.price}</td><td>${x.lower}</td><td>${x.upper}</td>
  <td><span class="badge ${x.signal=='AL'?'buy':x.signal=='SAT'?'sell':'wait'}">${x.signal}</span></td>
  <td>%${x.confidence}</td>
  <td>${x.stop}</td><td>${x.tp}</td>
  <td>${x.lot}</td><td>${x.risk}</td>
  <td><small>${x.mode}</small></td>
  </tr>`;
 }
 document.querySelector("#t tbody").innerHTML=html;
}
setInterval(refresh, {{refresh}});
refresh();
</script>

</body>
</html>
""", watchlist=WATCHLIST, refresh=REFRESH_SEC*1000)

# ================= START =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
