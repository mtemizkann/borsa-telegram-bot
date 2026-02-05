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

def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": msg
        }, timeout=5)
    except:
        pass

def price_monitor():
    while True:
        try:
            for symbol, data in WATCHLIST.items():
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1d", interval="1m")

                if hist.empty:
                    continue

                price = float(hist["Close"].iloc[-1])

                # ALT seviye
                if price <= data["lower"] and data["alerted"] != "lower":
                    send(f"🔻 {symbol}\nAlt seviye!\nFiyat: {price}")
                    data["alerted"] = "lower"

                # ÜST seviye
                elif price >= data["upper"] and data["alerted"] != "upper":
                    send(f"🔺 {symbol}\nÜst seviye!\nFiyat: {price}")
                    data["alerted"] = "upper"

                # Aralığa geri dönerse reset
                elif data["lower"] < price < data["upper"]:
                    data["alerted"] = None

        except Exception as e:
            print("HATA:", e)

        time.sleep(30)  # 30 saniyede bir kontrol

@app.route("/")
def home():
    return "Bot is running"

# Thread başlat
threading.Thread(target=price_monitor, daemon=True).start()
