import os
import time
import threading
import requests
import yfinance as yf
import pandas as pd
from flask import Flask, jsonify
from datetime import datetime

app = Flask(__name__)

# ==========================
# ENV
# ==========================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8023661442:AAHVsU9FBN35FMaW787m3EtIOIjpTtnZfhc").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1090532341").strip()
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "900"))  # 15 dk

# ==========================
# WATCHLIST
# ==========================
WATCHLIST = {
    "ASELS.IS": {"last_signal": None},
    "TUPRS.IS": {"last_signal": None},
    "FROTO.IS": {"last_signal": None},
    "MGROS.IS": {"last_signal": None}
}

# ==========================
# TELEGRAM
# ==========================
def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=5
        )
    except:
        pass


# ==========================
# INDICATORS
# ==========================
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


# ==========================
# SWING ENGINE
# ==========================
def analyze_stock(symbol):
    try:
        df = yf.download(symbol, period="6mo", interval="1d", progress=False)
        if df.empty:
            return None

        df["EMA50"] = ema(df["Close"], 50)
        df["RSI"] = rsi(df["Close"])

        current_price = df["Close"].iloc[-1]
        ema50 = df["EMA50"].iloc[-1]
        rsi_value = df["RSI"].iloc[-1]

        support_20 = df["Low"].rolling(20).min().iloc[-1]
        recent_high = df["High"].rolling(60).max().iloc[-1]

        potential = ((recent_high - current_price) / current_price) * 100

        trend_up = current_price > ema50
        dip_zone = rsi_value < 40
        near_support = current_price <= support_20 * 1.03
        good_potential = potential >= 10

        if trend_up and dip_zone and near_support and good_potential:
            return {
                "symbol": symbol,
                "price": round(current_price, 2),
                "support": round(support_20, 2),
                "target": round(recent_high, 2),
                "potential": round(potential, 1),
                "rsi": round(rsi_value, 1)
            }

        return None

    except:
        return None


# ==========================
# MONITOR LOOP
# ==========================
def swing_monitor():
    print("Swing monitor started")

    while True:
        try:
            for symbol, data in WATCHLIST.items():

                result = analyze_stock(symbol)

                if result and data["last_signal"] != "BUY":

                    message = (
                        f"📈 SWING FIRSATI\n\n"
                        f"Hisse: {result['symbol']}\n"
                        f"Fiyat: {result['price']}\n"
                        f"Destek: {result['support']}\n"
                        f"Hedef: {result['target']}\n"
                        f"Potansiyel: %{result['potential']}\n"
                        f"RSI: {result['rsi']}"
                    )

                    send_telegram(message)
                    data["last_signal"] = "BUY"

                elif not result and data["last_signal"] is not None:
                    data["last_signal"] = None

            print(f"Next check in {CHECK_INTERVAL_SEC} seconds")
            time.sleep(CHECK_INTERVAL_SEC)

        except Exception as e:
            print(f"Monitor error: {e}")
            time.sleep(60)


# ==========================
# API STATUS
# ==========================
@app.route("/api/state")
def state():
    return jsonify({
        "status": "running",
        "watchlist": WATCHLIST,
        "check_interval_sec": CHECK_INTERVAL_SEC
    })


# ==========================
# START (Gunicorn uyumlu)
# ==========================
threading.Thread(target=swing_monitor, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
