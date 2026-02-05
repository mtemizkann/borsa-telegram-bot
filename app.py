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
    "ASELS.IS": {"lower": 290, "upper": 310, "alerted": None},
    "TUPRS.IS": {"lower": 140, "upper": 170, "alerted": None},
    "FROTO.IS": {"lower": 850, "upper": 900, "alerted": None},
}

TICKERS = {s: yf.Ticker(s) for s in WATCHLIST}


# ---------------- MARKET CONTROL ----------------
def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18


# ---------------- TELEGRAM ----------------
def send(message):
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


# ---------------- PRICE ----------------
def get_current_prices():
    prices = {}
    for symbol in WATCHLIST:
        try:
            hist = TICKERS[symbol].history(period="1d", interval="1m")
            if not hist.empty:
                prices[symbol] = round(float(hist["Close"].iloc[-1]), 2)
            else:
                prices[symbol] = None
        except Exception:
            prices[symbol] = None
    return prices


# ---------------- SIGNAL ENGINE ----------------
def generate_signal(price, lower, upper):
    if price is None:
        return "VERİ YOK"
    if price <= lower:
        return "AL"
    elif price >= upper:
        return "SAT"
    else:
        return "BEKLE"


# ---------------- MONITOR (TELEGRAM) ----------------
def price_monitor():
    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            prices = get_current_prices()

            for symbol, data in WATCHLIST.items():
                price = prices.get(symbol)
                if price is None:
                    continue

                lower = data["lower"]
                upper = data["upper"]

                if price <= lower and data["alerted"] != "lower":
                    send(f"🟢 AL SİNYALİ\n{symbol}\nFiyat: {price}")
                    data["alerted"] = "lower"

                elif price >= upper and data["alerted"] != "upper":
                    send(f"🔴 SAT SİNYALİ\n{symbol}\nFiyat: {price}")
                    data["alerted"] = "upper"

                elif lower < price < upper:
                    data["alerted"] = None

            time.sleep(30)

        except Exception:
            time.sleep(10)


# ---------------- API ----------------
@app.route("/api/data")
def api_data():
    prices = get_current_prices()
    signals = {}

    for s, price in prices.items():
        signals[s] = generate_signal(
            price,
            WATCHLIST[s]["lower"],
            WATCHLIST[s]["upper"],
        )

    return jsonify({
        "prices": prices,
        "watchlist": WATCHLIST,
        "signals": signals
    })


# ---------------- WEB PANEL ----------------
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        symbol = request.form["symbol"]

        lower = float(request.form["lower"].replace(",", "."))
        upper = float(request.form["upper"].replace(",", "."))

        WATCHLIST[symbol]["lower"] = lower
        WATCHLIST[symbol]["upper"] = upper
        WATCHLIST[symbol]["alerted"] = None

    html = """
    <html>
    <head>
        <title>BIST Professional Panel</title>
        <style>
            body { background:#0e0e0e; color:white; font-family:Arial; padding:40px; }
            table { width:100%; border-collapse:collapse; margin-bottom:40px; }
            th, td { padding:12px; border-bottom:1px solid #333; text-align:center; }
            th { background:#1e1e1e; }
            tr.lower { background:#3a0000; }
            tr.upper { background:#003a2b; }
            .badge { padding:6px 12px; border-radius:12px; font-weight:bold; }
            .buy { background:#0f5132; color:#9cffd0; }
            .sell { background:#842029; color:#ffb3b3; }
            .wait { background:#41464b; color:#e2e3e5; }
            input, select { padding:8px; margin:5px; }
            button { padding:10px 20px; background:#0a84ff; color:white; border:none; cursor:pointer; }
        </style>
    </head>
    <body>

    <h2>📊 BIST Professional Manuel Alarm Paneli</h2>

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

    <h3>Limit Güncelle</h3>
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
    async function refresh() {
        const r = await fetch("/api/data");
        const d = await r.json();

        for (const s in d.prices) {
            const price = d.prices[s];
            const signal = d.signals[s];

            document.getElementById("price-" + s).innerText =
                price === null ? "Veri Yok" : price;

            const signalCell = document.getElementById("signal-" + s);
            signalCell.innerHTML = "";

            let badge = document.createElement("span");
            badge.classList.add("badge");

            if (signal === "AL") {
                badge.classList.add("buy");
                badge.innerText = "AL";
            } else if (signal === "SAT") {
                badge.classList.add("sell");
                badge.innerText = "SAT";
            } else {
                badge.classList.add("wait");
                badge.innerText = "BEKLE";
            }

            signalCell.appendChild(badge);
        }
    }

    setInterval(refresh, 15000);
    refresh();
    </script>

    </body>
    </html>
    """

    return render_template_string(html, watchlist=WATCHLIST)


# ---------------- START ----------------
threading.Thread(target=price_monitor, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
