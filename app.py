import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify
from datetime import datetime

app = Flask(__name__)

TOKEN = os.environ.get("TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

ACCOUNT_SIZE = 150000
RISK_PERCENT = 2

WATCHLIST = {
    "ASELS.IS": {"lower": 284, "upper": 286, "alerted": None},
    "TUPRS.IS": {"lower": 226, "upper": 229, "alerted": None},
    "FROTO.IS": {"lower": 114, "upper": 116, "alerted": None},
}

TICKERS = {s: yf.Ticker(s) for s in WATCHLIST}

# ---------------- MARKET ----------------
def market_open():
    now = datetime.now()
    return now.weekday() < 5 and 9 <= now.hour < 18

# ---------------- TELEGRAM ----------------
def send(msg):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=5
        )
    except:
        pass

# ---------------- PRICE ----------------
def get_prices():
    prices = {}
    for s in WATCHLIST:
        try:
            h = TICKERS[s].history(period="1d", interval="1m")
            prices[s] = round(float(h["Close"].iloc[-1]), 2) if not h.empty else None
        except:
            prices[s] = None
    return prices

# ---------------- MONITOR ----------------
def monitor():
    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            prices = get_prices()
            for s, d in WATCHLIST.items():
                p = prices.get(s)
                if p is None:
                    continue

                if p <= d["lower"] and d["alerted"] != "lower":
                    send(f"🟢 AL\n{s}\nFiyat: {p}")
                    d["alerted"] = "lower"

                elif p >= d["upper"] and d["alerted"] != "upper":
                    send(f"🔴 SAT\n{s}\nFiyat: {p}")
                    d["alerted"] = "upper"

                elif d["lower"] < p < d["upper"]:
                    d["alerted"] = None

            time.sleep(30)
        except:
            time.sleep(10)

# ---------------- API ----------------
@app.route("/api/data")
def api_data():
    prices = get_prices()
    rows = []

    for s, d in WATCHLIST.items():
        p = prices.get(s)
        signal = "BEKLE"
        lot = risk = 0

        if p is not None:
            if p <= d["lower"]:
                signal = "AL"
            elif p >= d["upper"]:
                signal = "SAT"

            if signal != "BEKLE":
                risk = ACCOUNT_SIZE * RISK_PERCENT / 100
                diff = abs(d["upper"] - d["lower"])
                lot = int(risk / diff) if diff > 0 else 0

        rows.append({
            "symbol": s,
            "price": p,
            "lower": d["lower"],
            "upper": d["upper"],
            "signal": signal,
            "lot": lot,
            "risk": round(risk, 2)
        })

    return jsonify(rows)

# ---------------- WEB ----------------
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
body{background:#0e0e0e;color:#fff;font-family:Arial;padding:30px}
table{width:100%;border-collapse:collapse;margin-bottom:30px}
th,td{padding:12px;border-bottom:1px solid #333;text-align:center}
th{background:#1e1e1e}
.al{background:#123f2b}
.sat{background:#3f1212}
.bekle{background:#222}
input,select,button{padding:8px;margin:5px}
button{background:#0a84ff;color:white;border:none}
</style>
</head>
<body>

<h2>📊 BIST Profesyonel Trading Sistemi</h2>
<p>Son güncelleme: {{time}}</p>

<table id="tbl">
<thead>
<tr>
<th>Hisse</th><th>Fiyat</th><th>Alt</th><th>Üst</th><th>Sinyal</th><th>Lot</th><th>Risk</th>
</tr>
</thead>
<tbody></tbody>
</table>

<h3>Limit Güncelle</h3>
<form method="post">
<select name="symbol">
{% for s in watchlist %}
<option value="{{s}}">{{s}}</option>
{% endfor %}
</select>
<input name="lower" placeholder="Alt">
<input name="upper" placeholder="Üst">
<button>Güncelle</button>
</form>

<script>
async function load(){
 const r = await fetch("/api/data");
 const d = await r.json();
 const b = document.querySelector("tbody");
 b.innerHTML="";
 d.forEach(x=>{
  const tr=document.createElement("tr");
  tr.className=x.signal.toLowerCase();
  tr.innerHTML=`<td>${x.symbol}</td><td>${x.price}</td><td>${x.lower}</td>
                <td>${x.upper}</td><td>${x.signal}</td>
                <td>${x.lot}</td><td>${x.risk}</td>`;
  b.appendChild(tr);
 });
}
setInterval(load,10000); load();
</script>

</body>
</html>
""", watchlist=WATCHLIST.keys(), time=datetime.now().strftime("%H:%M:%S"))

# ---------------- START ----------------
threading.Thread(target=monitor, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
