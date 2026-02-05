import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask

app = Flask(__name__)

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# ==============================
# TAKİP EDİLEN HİSSELER
# ==============================
WATCHLIST = {
    "ASELS.IS": {"lower": 290, "upper": 310, "alerted": None},
    "TUPRS.IS": {"lower": 140, "upper": 170, "alerted": None},
    "EREGL.IS": {"lower": 40, "upper": 50, "alerted": None},
}

# ==============================
# TELEGRAM MESAJ
# ==============================
def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": msg
        })
    except:
        pass

# ==============================
# FİYAT KONTROL
# ==============================
def price_monitor():
    while True:
        try:
            for symbol, data in WATCHLIST.items():
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1d", interval="1m")

                if hist.empty:
                    continue

                price = hist["Close"].iloc[-1]

                # ALT
                if price <= data["lower"] and data["alerted"] != "lower":
                    send(f"🔻 {symbol}\nAlt seviyeye geldi!\nFiyat: {price}")
                    data["alerted"] = "lower"

                # ÜST
                elif price >= data["upper"] and data["alerted"] != "upper":
                    send(f"🚀 {symbol}\nÜst seviyeye geldi!\nFiyat: {price}")
                    data["alerted"] = "upper"

                # Seviye dışına çıkınca reset
                elif data["lower"] < price < data["upper"]:
                    data["alerted"] = None

            time.sleep(30)  # 30 saniyede bir kontrol

        except Exception as e:
            time.sleep(10)

# ==============================
# HEALTH CHECK
# ==============================
@app.route("/")
def home():
    return "Bot is running"

# ==============================
# THREAD BAŞLAT
# ==============================
thread = threading.Thread(target=price_monitor)
thread.daemon = True
thread.start()
