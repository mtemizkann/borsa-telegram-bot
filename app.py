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

                if price <= data["lower"] and data["alerted"] != "lower":
                    send(f"🔻 {symbol}\nAlt seviye kırıldı\nFiyat: {price}")
                    data["alerted"] = "lower"

                elif price >= data["upper"] and data["alerted"] != "upper":
                    send(f"🔺 {sym
