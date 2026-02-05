import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify
from datetime import datetime

# ================== APP ==================
app = Flask(__name__)

TOKEN = os.environ.get("TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

ACCOUNT_SIZE = 150000
RISK_PERCENT = 2

# ================== WATCHLIST ==================
WATCHLIST = {
    "ASELS.IS": {"lower": 290.0, "upper": 310.0, "alerted": None},
    "TUPRS.IS": {"lower": 140.0, "upper": 170.0, "alerted": None},
    "FROTO.IS": {"lower": 850.0, "upper": 900.0, "alerted": None},
}

TICKERS = {s: yf.Ticker(s) for s in WATCHLIST}

# ================== HELPERS ==================
def parse_price(value: str) -> float:
    """
    294,25 -> 294.25
    294.25 -> 294.25
    """
    if not value:
        raise ValueError("Boş değer")
    return float(value.strip().replace(",", "."))


def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18


def send_telegram(msg: str):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=5,
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
        return "VERİ YOK"
    if price <= lower:
        return "AL"
    elif price >= upper:
        return "SAT"
    return "BEKLE"


# ================== BACKGROUND MONITOR ==================
def price_monitor():
    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            prices = get_prices()

            for s, data in WATCHLIST.items():
                price = prices.get(s)
                if price is None:
                    continue

                if price <= data["lower"] and data["alerted"] != "lower":
                    send_telegram(f"🟢 AL SİNYALİ\n{s}\nFiyat: {price}")
                    data["alerted"] = "lower"

                elif price >= data["upper"] and data["alerted"] != "upper":
                    send_telegram(f"🔴 SAT SİNYALİ\n{s}\nFiyat: {price}")
                    data["alerted"] = "upper"

                elif data["lower"] < price < data["upper"]:
                    data["alerted"] = None

            time.sleep(30)

        except Exception:
            time.sleep(10)


# ================== API ==================
@app.route("/api/data")
def api_data():
    prices = get_prices()
    signals = {
        s: generate_signal(prices[s], WATCHLIST[s]["lower"], WATCHLIST[s]["upper"])
        for s in WATCHLIST
    }
    return jsonify({"prices": prices, "watchlist": WATCHLIST, "signals": signals})


# ================== WEB PANEL ==================
@app.route("/", methods=["GET", "POST"])
def home():
    error = None

    if request.method == "POST":
        try:
            symbol = request.form.get("symbol")
            lower = parse_price(request.form.get("lower"))
            upper = parse_price(request.form.get("upper"))

            if symbol not in WATCHLIST:
                raise ValueError("Geçersiz hisse")

            WATCHLIST[symbol]["lower"] = lower
            WATCHLIST[symbol]["upper"] = upper
            WATCHLIST[symbol]["alerted"] = None

        except Exception as e:
            error = str(e)

    html = """
    <html>
    <head>
        <title>BIST Professional Panel</title>
        <style>
            body { background:#0e0e0e; color:white; font-family:Arial; padding:40px; }
            table { width:100%; border-collapse:collapse; margin-bottom:30px; }
            th, td { padding:12px; border-bottom:1px solid #333; text-align:center; }
            th { background:#1e1e1e; }
            .badge { padding:6px 12px; border-radius:12px; font-weight:bold; }
            .buy { background:#0f5132; color:#9cffd0; }
            .sell { background:#842029; color:#ffb3b3; }
            .wait { background:#41464b; color:#e2e3e5; }
            input, select { padding:8px; margin:5px; }
            button { padding:10px 20px; background:#0a84ff; color:white; border:none; cursor:pointer; }
            .error { color:#ff6b6b; margin-bottom:15px; }
        </style>
    </head>
    <body>

    <h2>📊 BIST Manuel Alarm Paneli</h2>

    {% if error %}
        <div class="error">Hata: {{error}}</div>
    {% endif %}

    <table>
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

    <h3>Limit Güncelle</h3>
    <form method="post">
        <select name="symbol">
            {% for s in watchlist %}
            <option value="{{s}}">{{s}}</option>
            {% endfor %}
        </select>
        <input name="lower" placeholder="Alt (örn 294,25)" inputmode="decimal">
        <input name="upper" placeholder="Üst (örn 310,00)" inputmode="decimal">
        <button type="submit">Güncelle</button>
    </form>

    <script>
    async function refresh() {
        const r = await fetch("/api/data");
        const d = await r.json();

        for (const s in d.prices) {
            document.getElementById("price-"+s).innerText =
                d.prices[s] ?? "Veri Yok";

            const cell = document.getElementById("signal-"+s);
            cell.innerHTML = "";

            const badge = document.createElement("span");
            badge.classList.add("badge");

            if (d.signals[s] === "AL") {
                badge.classList.add("buy"); badge.innerText="AL";
            } else if (d.signals[s] === "SAT") {
                badge.classList.add("sell"); badge.innerText="SAT";
            } else {
                badge.classList.add("wait"); badge.innerText="BEKLE";
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

    return render_template_string(html, watchlist=WATCHLIST, error=error)


# ================== START ==================
threading.Thread(target=price_monitor, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
