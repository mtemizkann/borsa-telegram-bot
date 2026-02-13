import os
import json
import time
import threading
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

# ================= ENV =================
TOKEN = os.environ.get("TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

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
        hist = t.history(period="1d", interval="1m", actions=False, timeout=5)
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
                snapshot = {k: v.copy() for k, v in WATCHLIST.items()}

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
        snapshot = {k: v.copy() for k, v in WATCHLIST.items()}

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

        if symbol in WATCHLIST and lower is not None and upper is not None:
            with _state_lock:
                WATCHLIST[symbol]["lower"] = lower
                WATCHLIST[symbol]["upper"] = upper
                WATCHLIST[symbol]["alerted"] = None

    with _state_lock:
        snapshot = {k: v.copy() for k, v in WATCHLIST.items()}

    html = """
    <html>
    <head>
    <title>BIST Professional Alarm Panel</title>
    </head>
    <body>
    <h1>BIST Alarm Panel</h1>

    <table border="1" cellpadding="10">
    <tr>
        <th>Hisse</th>
        <th>Fiyat</th>
        <th>Alt</th>
        <th>Üst</th>
        <th>Sinyal</th>
    </tr>
    {% for s in watchlist %}
    <tr>
        <td>{{s}}</td>
        <td id="price-{{s}}">-</td>
        <td>{{watchlist[s]["lower"]}}</td>
        <td>{{watchlist[s]["upper"]}}</td>
        <td id="signal-{{s}}">-</td>
    </tr>
    {% endfor %}
    </table>

    <form method="post">
    <select name="symbol">
    {% for s in watchlist %}
        <option value="{{s}}">{{s}}</option>
    {% endfor %}
    </select>
    <input name="lower" placeholder="Alt Limit">
    <input name="upper" placeholder="Üst Limit">
    <button type="submit">Güncelle</button>
    </form>

    <script>
    async function refresh(){
        const r = await fetch("/api/data");
        const d = await r.json();
        for(const s in d.prices){
            document.getElementById("price-"+s).innerText =
                d.prices[s]===null ? "Veri Yok" : d.prices[s];
            document.getElementById("signal-"+s).innerText =
                d.signals[s];
        }
    }
    setInterval(refresh,15000);
    refresh();
    </script>

    </body>
    </html>
    """

    return render_template_string(html, watchlist=snapshot)

if __name__ == "__main__":
    ensure_monitor_started()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
