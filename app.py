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
    "ASELS.IS": {"lower": 290, "upper": 310, "alerted": False},
    "TUPRS.IS": {"lower": 140, "upper": 170, "alerted": False},
    "EREGL.IS": {"lower": 40, "upper": 50, "alerted": False},
}

# ==============================
# TELEGRAM MESAJ
# ==============================
def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": msg
    })

# ==============================
# FİYAT KONTROL LOOP
# ==============================
def price_monitor():
    while True:
        for symbol, data in WATCHLIST.items():
            try:
                ticker = yf.Ticker(symbol)
                price = ticker.history(period="1d")["Close"].iloc[-1]

                # ALT SEVİYE
                if price <= data["lower"] and not data["alerted"]:
                    send(f"🔻 {symbol} ALT SEVİYEYE GELDİ\nFiyat: {price}")
                    data["alerted"] = True

                # ÜST SEVİYE
                elif price >= data["upper"] and not data["alerted"]:
                    send(f"🚀 {symbol} ÜST SEVİYEYE GELDİ\nFiyat: {price}")
                    data["alerted"] = True

                # Tekrar alarm açma reseti
                if data["lower"] < price < data["upper"]:
                    data["alerted"] = False

            except Exception as e:
                print(f"Hata: {s
