import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, request, render_template_string
from datetime import datetime

app = Flask(__name__)

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

ACCOUNT_SIZE = 150000
RISK_PERCENT = 2

WATCHLIST = {
    "ASELS.IS": {"lower": 290, "upper": 310, "alerted": None},
    "TUPRS.IS": {"lower": 140, "upper": 170, "alerted": None},
    "EREGL.IS": {"lower": 40, "upper": 50, "alerted": None},
}

TICKERS = {symbol: yf.Ticker(symbol) for symbol in WATCHLIST.keys()}


def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    if 9 <= now.hour < 18:
        return True
    return False


def send(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": message},
            timeout=5,
        )
    except Exception as e:
        print("Telegram error:", e)


def calculate_position(entry, stop):
    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100)
    per_share_risk = abs(entry - stop)

    if per_share_risk == 0:
        return 0, 0

    lot = int(risk_amount / per_share_risk)
    total_risk = lot * per_share_risk
    return lot, total_risk


def price_monitor():
    print("Price monitor started")

    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            for symbol, data in WATCHLIST.items():

                hist = TICKERS[symbol].history(
                    period="1d",
                    interval="1m",
                    actions=False,
                )

                if hist.empty:
                    continue

                price = float(hist["Close"].iloc[-1])

                if price <= data["lower"] and data["alerted"] != "lower":

                    stop = data["upper"]
                    lot, total_risk = calculate_position(price, stop)

                    message = (
                        f"🔻 {symbol}\n"
                        f"Alt kırılım\n"
                        f"Giriş: {price}\n"
                        f"Stop: {stop}\n"
                        f"Lot: {lot}\n"
                        f"Risk: {total_risk:.2f} TL"
                    )

                    send(message)
                    data["alerted"] = "lower"

                elif price >= data["upper"] and data["alerted"] != "upper":

                    stop = data["lower"]
                    lot, total_risk = calculate_position(price, stop)

                    message = (
                        f"🔺 {symbol}\n"
                        f"Üst kırılım\n"
                        f"Giriş: {price}\n"
                        f"Stop: {stop}\n"
                        f"Lot: {lot}\n"
                        f"Risk: {total_risk:.2f} TL"
                    )

                    send(message)
                    data["alerted"] = "upper"

                elif data["lower"] < price < data["upper"]:
                    data["alerted"] = None

            time.sleep(30)

        except Exception as e:
            print("Monitor error:", e)
            time.sleep(10)


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        symbol = request.form["symbol"]

        lower_raw = request.form["lower"].replace(",", ".")
        upper_raw = request.form["upper"].replace(",", ".")

        lower = float(lower_raw)
        upper = float(upper_raw)

        WATCHLIST[symbol]["lower"] = lower
        WATCHLIST[symbol]["upper"] = upper
        WATCHLIST[symbol]["alerted"] = None


    html = """
    <h2>BIST Alarm Paneli</h2>
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

    return render_template_string(html, watchlist=WATCHLIST.keys())


if __name__ == "__main__":
    monitor_thread = threading.Thread(target=price_monitor)
    monitor_thread.daemon = True
    monitor_thread.start()
    app.run(host="0.0.0.0", port=8080)
