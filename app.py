import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask

app = Flask(__name__)

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

WATCHLIST = {
    "ASELS.IS": {"lower": 290, "upper": 310, "alerted": None},
    "TUPRS.IS": {"lower": 140, "upper": 170, "alerted": None},
    "EREGL.IS": {"lower": 40, "upper": 50, "alerted": None},
}

TICKERS = {symbol: yf.Ticker(symbol) for symbol in WATCHLIST.keys()}


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


def price_monitor():
    print("Price monitor started")

    while True:
        try:
            for symbol, data in WATCHLIST.items():

                hist = TICKERS[symbol].history(
                    period="1d",
                    interval="1m",
                    actions=False,
                )

                if hist.empty:
                    continue

                price = float(hist["Close"].iloc[-1])

                # ALT
                if price <= data["lower"] and data["alerted"] != "lower":
                    send(f"🔻 {symbol}\nAlt seviye kırıldı\nFiyat: {price}")
                    data["alerted"] = "lower"

                # ÜST
                elif price >= data["upper"] and data["alerted"] != "upper":
                    send(f"🔺 {symbol}\nÜst seviye kırıldı\nFiyat: {price}")
                    data["alerted"] = "upper"

                # Aralığa dönerse reset
                elif data["lower"] < price < data["upper"]:
                    data["alerted"] = None

            time.sleep(60)

        except Exception as e:
            print("Monitor error:", e)
            time.sleep(10)


@app.route("/")
def home():
    return "Bot is running 🚀"


if __name__ == "__main__":
    thread = threading.Thread(target=price_monitor)
    thread.daemon = True
    thread.start()

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
