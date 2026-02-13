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
    <!DOCTYPE html>
    <html>
    <head>
    <title>BIST Enterprise Pro</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
    body{
        background: radial-gradient(circle at top left,#0f172a,#020617);
        color:white;
        font-family:Segoe UI;
        padding:40px;
    }
    h1{margin-bottom:5px}
    .status{
        padding:6px 14px;
        border-radius:20px;
        background:#1e293b;
        display:inline-block;
        margin-bottom:30px;
        font-size:14px;
    }
    table{
        width:100%;
        border-collapse:collapse;
        margin-bottom:40px;
    }
    th,td{
        padding:14px;
        border-bottom:1px solid rgba(255,255,255,0.05);
        text-align:center;
    }
    th{
        color:#94a3b8;
        font-weight:600;
    }
    tr:hover{
        background:rgba(255,255,255,0.03);
    }
    .badge{
        padding:6px 14px;
        border-radius:20px;
        font-weight:600;
        font-size:14px;
    }
    .buy{background:#064e3b;color:#6ee7b7}
    .sell{background:#7f1d1d;color:#fca5a5}
    .wait{background:#1e293b;color:#cbd5e1}
    .nov{background:#334155;color:#cbd5e1}
    .confidence-bar{
        height:6px;
        background:#1e293b;
        border-radius:4px;
        overflow:hidden;
    }
    .confidence-fill{
        height:6px;
        background:#3b82f6;
    }
    form{
        display:flex;
        gap:10px;
        margin-top:20px;
    }
    select,input{
        padding:8px;
        background:#1e293b;
        color:white;
        border:none;
        border-radius:6px;
    }
    button{
        background:#2563eb;
        color:white;
        border:none;
        padding:8px 18px;
        border-radius:6px;
        cursor:pointer;
    }
    </style>
    </head>
    <body>

    <h1>📊 BIST Enterprise Swing Terminal</h1>
    <div class="status">Market: {{market_status}}</div>

    <table>
    <thead>
    <tr>
        <th>Hisse</th>
        <th>Fiyat</th>
        <th>Alt</th>
        <th>Üst</th>
        <th>Band %</th>
        <th>Sinyal</th>
        <th>Confidence</th>
    </tr>
    </thead>
    <tbody>
    {% for s,d in watchlist.items() %}
    <tr>
        <td>{{s}}</td>
        <td id="price-{{s}}">-</td>
        <td>{{d["lower"]}}</td>
        <td>{{d["upper"]}}</td>
        <td id="distance-{{s}}">-</td>
        <td id="signal-{{s}}">-</td>
        <td>
            <div class="confidence-bar">
                <div class="confidence-fill" id="conf-{{s}}" style="width:0%"></div>
            </div>
        </td>
    </tr>
    {% endfor %}
    </tbody>
    </table>

    <h3>Limit Güncelle</h3>
    <form method="post">
        <select name="symbol">
        {% for s in watchlist.keys() %}
            <option value="{{s}}">{{s}}</option>
        {% endfor %}
        </select>
        <input name="lower" placeholder="Alt">
        <input name="upper" placeholder="Üst">
        <button>Güncelle</button>
    </form>

    <script>
    async function refresh(){
        const r=await fetch("/api/data");
        const d=await r.json();

        for(const s in d.prices){
            const price=d.prices[s];
            const lower=d.watchlist[s].lower;
            const upper=d.watchlist[s].upper;

            document.getElementById("price-"+s).innerText=price??"Yok";

            if(price){
                const distance = ((price-lower)/(upper-lower))*100;
                document.getElementById("distance-"+s).innerText =
                    distance.toFixed(1)+"%";
                document.getElementById("conf-"+s).style.width =
                    Math.min(100,Math.max(0,distance))+"%";
            }

            const signal=d.signals[s];
            const cell=document.getElementById("signal-"+s);
            cell.innerHTML="";
            const badge=document.createElement("span");
            badge.classList.add("badge");

            if(signal==="AL"){badge.classList.add("buy");badge.innerText="AL";}
            else if(signal==="SAT"){badge.classList.add("sell");badge.innerText="SAT";}
            else if(signal==="VERİ YOK"){badge.classList.add("nov");badge.innerText="YOK";}
            else{badge.classList.add("wait");badge.innerText="BEKLE";}

            cell.appendChild(badge);
        }
    }

    setInterval(refresh,10000);
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
