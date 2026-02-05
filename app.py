import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask

app = Flask(__name__)

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# İzlenecek hisseler
WATCHLIST = {
    "ASELS.IS": {"lower": 290, "upper": 310, "alerted": None},
    "TUPRS.IS": {"lower": 140, "upper": 170, "alerted": None},
    "EREGL.IS": {"lower": 40, "upper": 50, "alerted": None},
}

# Ticker objelerini bir kere oluştur
TICKERS = {s: yf.Ticker(s) for s in WATCHLIST.keys()}


def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": msg
            },
            timeout=5
        )
    except Exception as e:
        print("Telegram gönderim hatası:", e)


def price_monitor():
    print("Fiyat takibi başladı...")
    while True:
        try:
            for symbol, data in WATCHLIST.items():

                hist = TICKERS[symbol].history(period="1d", interval="1m", actions=False)

                if hist.empty:
                    continue

                price = float(hist["Close"].iloc[-1])

                # ALT kırılım
                if price <= data["lower"] and data["alerted"] != "lower":
                    send(f"🔻 {symbol}\nAlt seviye kırıldı!\nFiyat: {price}")
                    data["alerted"] = "lower"

                # ÜST kırılım
                elif price >= data["upper"] and data["alerted"] != "upper":
                    send(f"🔺 {symbol}\nÜst seviye kırıldı!\nFiyat: {price}")
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
if __name__ == "__main__":
    threading.Thread(target=price_monitor, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)

