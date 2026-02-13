import os
import json
import time
import threading
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify, abort

app = Flask(__name__)

# ================= ENV =================
TOKEN = os.environ.get("TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
SECRET = os.environ.get("SECRET", "").strip()

ACCOUNT_SIZE = float(os.environ.get("ACCOUNT_SIZE", "150000").replace(",", "."))
RISK_PERCENT = float(os.environ.get("RISK_PERCENT", "2").replace(",", "."))

# ================= STATE =================
WATCHLIST: Dict[str, Dict[str, Any]] = {
    "ASELS.IS": {"lower": 290.0, "upper": 310.0, "alerted": None},
    "TUPRS.IS": {"lower": 140.0, "upper": 170.0, "alerted": None},
    "FROTO.IS": {"lower": 850.0, "upper": 900.0, "alerted": None},
}

_TICKERS: Dict[str, yf.Ticker] = {}
_state_lock = threading.Lock()
_monitor_started = False
_monitor_lock = threading.Lock()

# ================= HELPERS =================
def tr_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        s = str(x).strip()
        if not s:
            return default
        return float(s.replace(",", "."))
    except Exception:
        return default

def safe_round(x: Any, ndigits: int = 2) -> Optional[float]:
    try:
        if x is None:
            return None
        return round(float(x), ndigits)
    except Exception:
        return None

def market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18

def get_ticker(symbol: str) -> yf.Ticker:
    if symbol not in _TICKERS:
        _TICKERS[symbol] = yf.Ticker(symbol)
    return _TICKERS[symbol]

def fetch_last_price(symbol: str) -> Optional[float]:
    try:
        t = get_ticker(symbol)
        hist = t.history(period="1d", interval="1m", actions=False)
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None

def generate_signal(price: Optional[float], lower: float, upper: float) -> str:
    if price is None:
        return "VERİ YOK"
    if price <= lower:
        return "AL"
    if price >= upper:
        return "SAT"
    return "BEKLE"

def calculate_position(entry: float, stop: float) -> Tuple[int, float]:
    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100.0)
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0:
        return 0, 0.0
    lot = int(risk_amount / per_share_risk)
    total_risk = lot * per_share_risk
    return lot, total_risk

def send_telegram(message: str) -> None:
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message},
            timeout=5,
        )
    except Exception:
        pass

# ================= MONITOR =================
def price_monitor_loop():
    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            with _state_lock:
                snapshot = json.loads(json.dumps(WATCHLIST))

            for symbol, d in snapshot.items():
                price = fetch_last_price(symbol)
                if price is None:
                    continue

                lower = float(d["lower"])
                upper = float(d["upper"])

                with _state_lock:
                    st = WATCHLIST.get(symbol)
                    if not st:
                        continue

                    alerted = st.get("alerted")

                    if price <= lower and alerted != "lower":
                        stop = upper
                        lot, total_risk = calculate_position(price, stop)
                        send_telegram(
                            f"🟢 AL\n{symbol}\n"
                            f"Fiyat: {safe_round(price)}\n"
                            f"Stop: {safe_round(stop)}\n"
                            f"Lot: {lot}\nRisk: {safe_round(total_risk)}"
                        )
                        st["alerted"] = "lower"

                    elif price >= upper and alerted != "upper":
                        stop = lower
                        lot, total_risk = calculate_position(price, stop)
                        send_telegram(
                            f"🔴 SAT\n{symbol}\n"
                            f"Fiyat: {safe_round(price)}\n"
                            f"Stop: {safe_round(stop)}\n"
                            f"Lot: {lot}\nRisk: {safe_round(total_risk)}"
                        )
                        st["alerted"] = "upper"

                    elif lower < price < upper:
                        st["alerted"] = None

            time.sleep(30)
        except Exception:
            time.sleep(10)

def ensure_monitor_started():
    global _monitor_started
    if _monitor_started:
        return
    with _monitor_lock:
        if _monitor_started:
            return
        threading.Thread(target=price_monitor_loop, daemon=True).start()
        _monitor_started = True

@app.before_request
def start_monitor_once():
    ensure_monitor_started()

# ================= API =================
@app.route("/api/data", methods=["GET"])
def api_data():
    with _state_lock:
        snapshot = json.loads(json.dumps(WATCHLIST))

    prices = {}
    signals = {}

    for s, d in snapshot.items():
        p = fetch_last_price(s)
        prices[s] = safe_round(p)
        signals[s] = generate_signal(p, float(d["lower"]), float(d["upper"]))

    return jsonify({"prices": prices, "watchlist": snapshot, "signals": signals})

# ================= PANEL =================
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        symbol = request.form.get("symbol")
        lower = tr_float(request.form.get("lower"))
        upper = tr_float(request.form.get("upper"))

        if symbol and lower and upper:
            with _state_lock:
                WATCHLIST[symbol]["lower"] = lower
                WATCHLIST[symbol]["upper"] = upper
                WATCHLIST[symbol]["alerted"] = None

    with _state_lock:
        snapshot = json.loads(json.dumps(WATCHLIST))

    market_status = "AÇIK" if market_open() else "KAPALI"

    html = """
<html>
<head>
<title>BIST Professional Alarm Panel</title>

<style>
body{
    margin:0;
    font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto;
    background: radial-gradient(circle at 20% 20%, #0d1b2a, #000814 70%);
    color:white;
    padding:50px;
}

.container{
    max-width:1200px;
    margin:auto;
}

.header{
    display:flex;
    justify-content:space-between;
    align-items:center;
    margin-bottom:30px;
}

.badge-online{
    background:#0f5132;
    padding:6px 14px;
    border-radius:20px;
    font-size:13px;
    opacity:0.9;
}

.card{
    background:rgba(255,255,255,0.05);
    backdrop-filter: blur(18px);
    border-radius:18px;
    padding:30px;
    box-shadow:0 0 40px rgba(0,0,0,0.5);
    margin-bottom:30px;
}

table{
    width:100%;
    border-collapse:collapse;
}

th{
    text-align:center;
    opacity:0.6;
    font-weight:500;
    padding:18px 10px;
}

td{
    text-align:center;
    padding:20px 10px;
    font-size:18px;
}

tr{
    transition:0.25s;
}

tr:hover{
    background:rgba(255,255,255,0.03);
}

tr.sell{
    background:rgba(132,32,41,0.18);
}

tr.buy{
    background:rgba(15,81,50,0.18);
}

.signal{
    padding:8px 18px;
    border-radius:30px;
    font-weight:600;
    font-size:14px;
}

.wait{background:#343a40;}
.sell-badge{background:#842029;}
.buy-badge{background:#0f5132;}

.form-row{
    display:flex;
    gap:15px;
    align-items:center;
}

input,select{
    padding:10px 15px;
    border-radius:10px;
    border:none;
    background:rgba(255,255,255,0.1);
    color:white;
}

button{
    padding:10px 20px;
    border:none;
    border-radius:10px;
    background:#0a84ff;
    color:white;
    font-weight:600;
    cursor:pointer;
    transition:0.2s;
}

button:hover{
    opacity:0.85;
}

.system{
    opacity:0.7;
    font-size:14px;
    line-height:1.6;
}
</style>
</head>

<body>
<div class="container">

<div class="header">
<h1>📊 BIST Professional Alarm Panel</h1>
<div class="badge-online">Online</div>
</div>

<div class="card">
<table>
<thead>
<tr>
<th>Hisse</th>
<th>Anlık Fiyat</th>
<th>Alt Limit</th>
<th>Üst Limit</th>
<th>Sinyal</th>
</tr>
</thead>

<tbody>
{% for s in watchlist %}
<tr id="row-{{s}}">
<td>{{s}}</td>
<td id="price-{{s}}">-</td>
<td>{{watchlist[s]["lower"]}}</td>
<td>{{watchlist[s]["upper"]}}</td>
<td id="signal-{{s}}">-</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>

<div class="card">
<h3>Limit Güncelle</h3>
<form method="post" class="form-row">
<select name="symbol">
{% for s in watchlist %}
<option value="{{s}}">{{s}}</option>
{% endfor %}
</select>
<input name="lower" placeholder="Alt Limit">
<input name="upper" placeholder="Üst Limit">
<button type="submit">Güncelle</button>
</form>
</div>

<div class="card system">
• Web yenileme: 15 sn<br>
• Telegram kontrol: 30 sn<br>
• Market saatleri: 09:00–18:00 (Hafta içi)
</div>

</div>

<script>
async function refresh(){
    const r = await fetch("/api/data");
    const d = await r.json();

    for(const s in d.prices){
        const price = d.prices[s];
        const signal = d.signals[s];

        document.getElementById("price-"+s).innerText =
            price===null ? "Veri Yok" : price;

        const row = document.getElementById("row-"+s);
        row.classList.remove("buy","sell");

        const cell = document.getElementById("signal-"+s);
        cell.innerHTML="";

        let span = document.createElement("span");
        span.classList.add("signal");

        if(signal==="AL"){
            span.classList.add("buy-badge");
            span.innerText="AL";
            row.classList.add("buy");
        }
        else if(signal==="SAT"){
            span.classList.add("sell-badge");
            span.innerText="SAT";
            row.classList.add("sell");
        }
        else{
            span.classList.add("wait");
            span.innerText="BEKLE";
        }

        cell.appendChild(span);
    }
}

setInterval(refresh,15000);
refresh();
</script>

</body>
</html>
"""

    return render_template_string(
        html,
        watchlist=snapshot,
        market_status=market_status
    )

if __name__ == "__main__":
    ensure_monitor_started()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
