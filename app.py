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

TICKERS = {symbol: yf.Ticker(symbol) for symbol in WATCHLIST.keys()}


def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18


def send(message):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message},
            timeout=5,
        )
    except:
        pass


def calculate_position(entry, stop):
    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100)
    per_share_risk = abs(entry - stop)

    if per_share_risk == 0:
        return 0, 0

    lot = int(risk_amount / per_share_risk)
    total_risk = lot * per_share_risk
    return lot, total_risk


def get_current_prices():
    prices = {}
    for symbol in WATCHLIST:
        try:
            hist = TICKERS[symbol].history(period="1d", interval="1m")
            if not hist.empty:
                prices[symbol] = round(float(hist["Close"].iloc[-1]), 2)
            else:
                prices[symbol] = None
        except:
            prices[symbol] = None
    return prices


def price_monitor():
    print("Manual alarm monitor started")

    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            for symbol, data in WATCHLIST.items():

                hist = TICKERS[symbol].history(period="1d", interval="1m")
                if hist.empty:
                    continue

                price = float(hist["Close"].iloc[-1])
                lower = data["lower"]
                upper = data["upper"]

                # ALT kırılım
                if price <= lower and data["alerted"] != "lower":

                    lot, total_risk = calculate_position(price, upper)

                    message = (
                        f"🔻 {symbol}\n"
                        f"Alt limit kırıldı\n"
                        f"Giriş: {price}\n"
                        f"Stop: {upper}\n"
                        f"Lot: {lot}\n"
                        f"Risk: {total_risk:.2f} TL"
                    )

                    send(message)
                    data["alerted"] = "lower"

                # ÜST kırılım
                elif price >= upper and data["alerted"] != "upper":

                    lot, total_risk = calculate_position(price, lower)

                    message = (
                        f"🔺 {symbol}\n"
                        f"Üst limit kırıldı\n"
                        f"Giriş: {price}\n"
                        f"Stop: {lower}\n"
                        f"Lot: {lot}\n"
                        f"Risk: {total_risk:.2f} TL"
                    )

                    send(message)
                    data["alerted"] = "upper"

                # Bant içi reset
                elif lower < price < upper:
                    data["alerted"] = None

            time.sleep(30)

        except:
            time.sleep(10)


@app.route("/api/prices")
def api_prices():
    return jsonify(get_current_prices())


@app.route("/", methods=["GET", "POST"])
def home():
    error = None

    if request.method == "POST":
        try:
            symbol = request.form["symbol"]
            lower = float(request.form["lower"].replace(",", "."))
            upper = float(request.form["upper"].replace(",", "."))

            WATCHLIST[symbol]["lower"] = lower
            WATCHLIST[symbol]["upper"] = upper
            WATCHLIST[symbol]["alerted"] = None

        except:
            error = "Hatalı sayı formatı"

    prices = get_current_prices()

    html = """
    <h2>📊 BIST Manuel Alarm Paneli</h2>

    <h3>Anlık Fiyatlar</h3>
    <ul>
    {% for s, p in prices.items() %}
        <li><strong>{{s}}</strong> : {{p if p else "Veri yok"}} TL</li>
    {% endfor %}
    </ul>

    <hr>

    {% if error %}
        <p style="color:red;">{{error}}</p>
    {% endif %}

    <h3>Limit Güncelle</h3>
    <form method="post">
        Hisse:
        <select name="symbol">
        {% for s in watchlist %}
            <option value="{{s}}">{{s}}</option>
        {% endfor %}
        </select><br><br>

        Alt Limit: <input name="lower"><br><br>
        Üst Limit: <input name="upper"><br><br>

        <button type="submit">Güncelle</button>
    </form>
    """

    return render_template_string(
        html,
        watchlist=WATCHLIST.keys(),
        prices=prices,
        error=error,
    )


monitor_thread = threading.Thread(target=price_monitor)
monitor_thread.daemon = True
monitor_thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
