import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify
from datetime import datetime

app = Flask(__name__)

TOKEN = os.environ.get("TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

ACCOUNT_SIZE = 150000
RISK_PERCENT = 2

WATCHLIST = {
    "ASELS.IS": {"lower": 284, "upper": 286, "alerted": None},
    "TUPRS.IS": {"lower": 226, "upper": 229, "alerted": None},
    "FROTO.IS": {"lower": 114, "upper": 116, "alerted": None},
}

# -------- MARKET --------
def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18

# -------- TELEGRAM --------
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

# -------- FRESH PRICE (CACHE YOK) --------
def get_price(symbol):
    try:
        t = yf.Ticker(symbol)
        price = t.fast_info["last_price"]
        return round(float(price), 2) if price else None
    except:
        return None

# -------- LOT / RISK --------
def calc_lot(entry, stop):
    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100)
    diff = abs(entry - stop)
    if diff == 0:
        return 0, 0
    lot = int(risk_amount / diff)
    return lot, round(lot * diff, 2)

# -------- MONITOR (TELEGRAM KİLİTLİ) --------
def monitor():
    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            for s, d in WATCHLIST.items():
                price = get_price(s)
                if price is None:
                    continue

                if price <= d["lower"] and d["alerted"] != "BUY":
                    lot, risk = calc_lot(price, d["upper"])
                    send(f"🟢 AL SİNYALİ\n{s}\nFiyat: {price}\nLot: {lot}\nRisk: {risk} TL")
                    d["alerted"] = "BUY"

                elif price >= d["upper"] and d["alerted"] != "SELL":
                    lot, risk = calc_lot(price, d["lower"])
                    send(f"🔴 SAT SİNYALİ\n{s}\nFiyat: {price}\nLot: {lot}\nRisk: {risk} TL")
                    d["alerted"] = "SELL"

                elif d["lower"] < price < d["upper"]:
                    d["alerted"] = None

            time.sleep(15)

        except:
            time.sleep(10)

# -------- API --------
@app.route("/api/data")
def api():
    data = {}
    for s, d in WATCHLIST.items():
        price = get_price(s)
        signal = "BEKLE"
        if price is not None:
            if price <= d["lower"]:
                signal = "AL"
            elif price >= d["upper"]:
                signal = "SAT"

        lot, risk = (0, 0)
        if signal != "BEKLE" and price:
            lot, risk = calc_lot(price, d["upper"] if signal == "AL" else d["lower"])

        data[s] = {
            "price": price,
            "lower": d["lower"],
            "upper": d["upper"],
            "signal": signal,
            "lot": lot,
            "risk": risk
        }
    return jsonify(data)

# -------- PANEL --------
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        s = request.form["symbol"]
        WATCHLIST[s]["lower"] = float(request.form["lower"].replace(",", "."))
        WATCHLIST[s]["upper"] = float(request.form["upper"].replace(",", "."))
        WATCHLIST[s]["alerted"] = None

    html = """
    <html>
    <head>
        <title>BIST Profesyonel Trading Sistemi</title>
        <style>
            body { background:#0b0b0b; color:white; font-family:Arial; padding:30px }
            table { width:100%; border-collapse:collapse }
            th,td { padding:12px; border-bottom:1px solid #333; text-align:center }
            th { background:#1e1e1e }
            .AL { background:#0f5132 }
            .SAT { background:#842029 }
            .BEKLE { background:#333 }
            input,select,button { padding:8px }
            button { background:#0a84ff; color:white; border:none }
        </style>
    </head>
    <body>
        <h2>📊 BIST Profesyonel Trading Sistemi</h2>
        <table>
            <tr><th>Hisse</th><th>Fiyat</th><th>Alt</th><th>Üst</th><th>Sinyal</th><th>Lot</th><th>Risk</th></tr>
            {% for s,d in data.items() %}
            <tr class="{{d.signal}}">
                <td>{{s}}</td><td>{{d.price}}</td><td>{{d.lower}}</td><td>{{d.upper}}</td>
                <td>{{d.signal}}</td><td>{{d.lot}}</td><td>{{d.risk}}</td>
            </tr>
            {% endfor %}
        </table>

        <h3>Limit Güncelle</h3>
        <form method="post">
            <select name="symbol">
                {% for s in data.keys() %}
                <option>{{s}}</option>
                {% endfor %}
            </select>
            <input name="lower" placeholder="Alt">
            <input name="upper" placeholder="Üst">
            <button>Güncelle</button>
        </form>
    </body>
    </html>
    """
    return render_template_string(html, data={k: {
        **v,
        "price": get_price(k),
        "lot": calc_lot(get_price(k), v["upper"] if get_price(k) else v["lower"])[0],
        "risk": calc_lot(get_price(k), v["upper"] if get_price(k) else v["lower"])[1],
        "signal": "AL" if get_price(k) and get_price(k) <= v["lower"] else "SAT" if get_price(k) and get_price(k) >= v["upper"] else "BEKLE"
    } for k,v in WATCHLIST.items()})

# -------- START --------
threading.Thread(target=monitor, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
