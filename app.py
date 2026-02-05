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


# ---------------- MONITOR ----------------
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
                    send(f"🔻 {symbol} alt limit kırıldı\nFiyat: {price}")
                    data["alerted"] = "lower"

                elif price >= upper and data["alerted"] != "upper":
                    send(f"🔺 {symbol} üst limit kırıldı\nFiyat: {price}")
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
    return jsonify({"prices": prices, "watchlist": WATCHLIST})


# ---------------- WEB PANEL ----------------
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        symbol = request.form["symbol"]

        # TR formatı destekle: 294,25 -> 294.25
        lower_str = request.form["lower"].strip().replace(",", ".")
        upper_str = request.form["upper"].strip().replace(",", ".")

        lower = float(lower_str)
        upper = float(upper_str)

        WATCHLIST[symbol]["lower"] = lower
        WATCHLIST[symbol]["upper"] = upper
        WATCHLIST[symbol]["alerted"] = None

    html = """
    <html>
    <head>
        <title>BIST Professional Panel</title>
        <style>
            body { background:#111; color:white; font-family:Arial; padding:40px; }
            table { width:100%; border-collapse:collapse; margin-bottom:40px; }
            th, td { padding:12px; border-bottom:1px solid #333; text-align:center; }
            th { background:#222; }
            tr.lower { background:#400; }
            tr.upper { background:#043; }
            input, select { padding:8px; margin:5px; }
            button { padding:10px 20px; background:#0a84ff; color:white; border:none; cursor:pointer; }
            .hint { color:#aaa; font-size: 13px; margin-top: 8px; }
        </style>
    </head>
    <body>

        <h2>📊 BIST Professional Manuel Alarm Paneli</h2>

        <table id="priceTable">
            <thead>
                <tr>
                    <th>Hisse</th>
                    <th>Anlık Fiyat</th>
                    <th>Alt Limit</th>
                    <th>Üst Limit</th>
                    <th>Durum</th>
                </tr>
            </thead>
            <tbody>
            {% for s in watchlist %}
                <tr id="row-{{s}}">
                    <td>{{s}}</td>
                    <td id="price-{{s}}">-</td>
                    <td id="lower-{{s}}">{{watchlist[s]["lower"]}}</td>
                    <td id="upper-{{s}}">{{watchlist[s]["upper"]}}</td>
                    <td id="status-{{s}}">Normal</td>
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
            <input name="lower" placeholder="Alt Limit (örn: 294,25)">
            <input name="upper" placeholder="Üst Limit (örn: 310)">
            <button type="submit">Güncelle</button>
            <div class="hint">Not: Virgüllü değer girersen (294,25) otomatik olarak 294.25'e çevrilir.</div>
        </form>

        <script>
            async function refreshData() {
                const response = await fetch('/api/data');
                const data = await response.json();

                for (const symbol in data.prices) {
                    const price = data.prices[symbol];
                    const row = document.getElementById("row-" + symbol);
                    const priceCell = document.getElementById("price-" + symbol);
                    const statusCell = document.getElementById("status-" + symbol);
                    const lowerCell = document.getElementById("lower-" + symbol);
                    const upperCell = document.getElementById("upper-" + symbol);

                    const lower = data.watchlist[symbol].lower;
                    const upper = data.watchlist[symbol].upper;

                    if (lowerCell) lowerCell.innerText = lower;
                    if (upperCell) upperCell.innerText = upper;

                    priceCell.innerText = (price === null || price === undefined) ? "Veri Yok" : price;

                    row.classList.remove("lower", "upper");

                    if (price !== null && price !== undefined) {
                        if (price <= lower) {
                            row.classList.add("lower");
                            statusCell.innerText = "Alt Alarm";
                        } else if (price >= upper) {
                            row.classList.add("upper");
                            statusCell.innerText = "Üst Alarm";
                        } else {
                            statusCell.innerText = "Normal";
                        }
                    } else {
                        statusCell.innerText = "Veri Yok";
                    }
                }
            }

            setInterval(refreshData, 15000);
            refreshData();
        </script>

    </body>
    </html>
    """
    return render_template_string(html, watchlist=WATCHLIST)


# Start monitor thread (gunicorn altında da çalışsın diye burada başlatıyoruz)
monitor_thread = threading.Thread(target=price_monitor, daemon=True)
monitor_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
