import time
import os
import requests
import yfinance as yf

TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "5"))

WATCHLIST = {
    "ASELS.IS": {"below": 654, "above": 700},
    "MGROS.IS": {"below": 480, "above": 520},
    "THYAO.IS": {"below": 240, "above": 270},
    "EREGL.IS": {"below": 45, "above": 55},
    "TUPRS.IS": {"below": 130, "above": 150},
}

state = {s: {"below": False, "above": False} for s in WATCHLIST}

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=15)

def last_price(symbol: str) -> float:
    df = yf.download(symbol, period="1d", interval="1m", progress=False)
    return float(df["Close"].iloc[-1])

def check_once():
    for symbol, levels in WATCHLIST.items():
        try:
            price = last_price(symbol)
            below, above = levels["below"], levels["above"]

            # alt
            if price <= below and not state[symbol]["below"]:
                send_telegram(f"🔻 {symbol} {price:.2f} <= ALT {below} (ALIM alarmı)")
                state[symbol]["below"] = True
                state[symbol]["above"] = False

            # üst
            elif price >= above and not state[symbol]["above"]:
                send_telegram(f"🔺 {symbol} {price:.2f} >= ÜST {above} (SATIM alarmı)")
                state[symbol]["above"] = True
                state[symbol]["below"] = False

            # normal aralık → reset (tekrar tetiklenebilsin)
            elif below < price < above:
                state[symbol]["below"] = False
                state[symbol]["above"] = False

        except Exception as e:
            print(f"[ERR] {symbol}: {e}")

if __name__ == "__main__":
    send_telegram("✅ BIST alarm botu başladı.")
    while True:
        check_once()
        time.sleep(CHECK_INTERVAL)
