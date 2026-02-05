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
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": msg
    })

# ==============================
# FİYAT KONTROL
# ==============================
def price_monitor():
    while True:
        for symbol, data in WATCHLIST.items():
            try:
                ticker = yf.Ticker(symbol)
                price = ticker.history(period="1d", interval="1m")["Close"].iloc[-1]

                # ALT
                if price <= data["lower"] and data["alerted"] != "lower":
                    send(f"🔻 {symbol} ALT SEVİYEYE GELDİ\nFiyat: {price}")
                    data["alerted"] = "lower"

                # ÜST
                elif price >= data["upper"] and data["alerted"] != "u
