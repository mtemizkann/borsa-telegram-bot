import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify
from datetime import datetime

# ---------------- APP ----------------
app = Flask(__name__)

TOKEN = os.environ.get("TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# ---------------- AYARLAR ----------------
ACCOUNT_SIZE = 150000
RISK_PERCENT = 2
REFRESH_SECONDS = 30  # fiyat yenileme

# ---------------- HİSSELER ----------------
WATCHLIST = {
    "ASELS.IS": {"lower": 290, "upper": 310, "alerted": None},
    "TUPRS.IS": {"lower": 140, "upper": 170, "alerted": None},
    "FROTO.IS": {"lower": 850, "upper": 900, "alerted": None},
}

TICKERS = {s: yf.Ticker(s) for s in WATCHLIST}


# ---------------- MARKET SAATİ ----------------
def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18


# ---------------- TELEGRAM ----------------
def send_telegram(message):
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


# ---------------- FİYAT ----------------
def get_prices():
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


# ---------------- SİNYAL ----------------
def generate_signal(price, lower, upper):
    if price is None:
        return "VERİ YOK"
    if price <= lower:
        return "AL"
    if price >= upper:
        return "SAT"
    return "BEKLE"


# ---------------- TELEGRAM MONITOR ----------------
def price_monitor():
    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            prices = get_prices()

            for symbol, cfg in WATCHLIST.items():
                price = prices.get(symbol)
                if price is None:
                    continue

                signal = generate_signal(price, cfg["lower"], cfg["upper"])

                if signal == "AL" and cfg["alerted"] != "AL":
                    send_telegram(f"🟢 AL SİNYALİ\n{symbol}\nFiyat: {price}")
                    cfg["alerted"] = "AL"

                elif signal == "SAT" and cfg["alerted"] != "SAT":
                    send_telegram(f"🔴 SAT SİNYALİ\n{symbol}\nFiyat: {price}")
                    cfg["alerted"] = "SAT"

                elif signal == "BEKLE":
                    cfg["alerted"] = None

            time.sleep(REFRESH_SECONDS)

        except Exception:
            time.sleep(10)


# ---------------- API ----------------
@app.route("/api/data")
def api_data():
    prices = get_prices()
    signals = {
        s: generate_signal(
            prices.get(s),
            WATCHLIST[s]["lower"],
            WATCHLIST[s]["upper"],
        )
        for s in WATCHLIST
    }

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

        # TR format güvenli dönüşüm
        lower = float(request.form["lower"].replace(",", "."))
        upper = float(request.form["upper"].replace(",", "."))

        WATCHLIST[symbol]["lower"] = lower
        WATCHLIST[symbol]["upper"] = upper
        WATCHLIST[symbol]["alerted"] = None

    html = """
    <html>
    <head>
        <title>BIST Alarm Paneli</title>
        <style>
            body { background:#0f1117; color:#e6e6e6; font-family:Arial; padding:40px; }
            table { width:100%; border-collapse:collapse; margin-bottom:30px; }
            th, td { padding:12px; border-bottom:1px solid #2a2d36; text-align:center; }
            th { background:#161a23; }
            .badge { padding:6px 14px; border-radius:12px; font-weight:bold; }
            .buy { background:#103b2f; color:#7dffb3; }
            .sell { background:#3b1010; color:#ff9a9a; }
            .wait { background:#2f2f2f; color:#ddd; }
            input, select, button { padding:8px; margin:5px; }
            button { background:#2563eb; color:white; border:none; cursor:pointer; }
        </style>
    </head>
    <body>

    <h2>📊 Manuel Alt / Üst Alarm Paneli</h2>

    <table>
        <thead>
            <tr>
                <th>Hisse</th>
                <th>Fiyat (gecikmeli)</th>
                <th>Alt</th>
                <th>Üst</th>
                <th>Sinyal</th>
            </tr>
        </thead>
        <tbody>
        {% for s in watchlist %}
            <tr>
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
        <input name="lower" placeholder="Alt (294,25 olabilir)">
        <input name="upper" placeholder="Üst (310)">
        <button type="submit">Güncelle</button>
    </form>

    <script>
    async function refresh() {
        const r = await fetch("/api/data");
        const d = await r.json();

        for (const s in d.prices) {
            document.getElementById("price-" + s).innerText =
                d.prices[s] === null ? "Veri yok" : d.prices[s];

            const cell = document.getElementById("signal-" + s);
            cell.innerHTML = "";

            const badge = document.createElement("span");
            badge.classList.add("badge");

            if (d.signals[s] === "AL") {
                badge.classList.add("buy");
                badge.innerText = "AL";
            } else if (d.signals[s] === "SAT") {
                badge.classList.add("sell");
                badge.innerText = "SAT";
            } else {
                badge.classList.add("wait");
                badge.innerText = "BEKLE";
            }

            cell.appendChild(badge);
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
