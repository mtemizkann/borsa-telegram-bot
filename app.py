import os
import time
import threading
from datetime import datetime

import requests
import yfinance as yf
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

# ================= CONFIG =================
TOKEN = os.environ.get("TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
REFRESH_SECONDS = 10
ACCOUNT_RISK_TL = 3000

WATCHLIST = {
    "ASELS.IS": {"lower": 284, "upper": 286, "signal": "BEKLE", "last_sent": None},
    "TUPRS.IS": {"lower": 226, "upper": 229, "signal": "BEKLE", "last_sent": None},
    "FROTO.IS": {"lower": 114, "upper": 116, "signal": "BEKLE", "last_sent": None},
}

prices = {}
last_update = None
tickers = {s: yf.Ticker(s) for s in WATCHLIST}


# ================= TELEGRAM =================
def send_telegram(msg):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=5
        )
    except:
        pass


# ================= SIGNAL ENGINE =================
def evaluate_signals():
    for s, cfg in WATCHLIST.items():
        price = prices.get(s)
        if price is None:
            continue

        lower, upper = cfg["lower"], cfg["upper"]
        new_signal = "BEKLE"

        if price <= lower:
            new_signal = "AL"
        elif price >= upper:
            new_signal = "SAT"

        # 🔴 KRİTİK KONTROL: sadece değişince gönder
        if new_signal != cfg["last_sent"]:
            if new_signal == "AL":
                send_telegram(f"🟢 AL SİNYALİ\n{s}\nFiyat: {price}")
            elif new_signal == "SAT":
                send_telegram(f"🔴 SAT SİNYALİ\n{s}\nFiyat: {price}")

            cfg["last_sent"] = new_signal

        cfg["signal"] = new_signal


# ================= PRICE LOOP =================
def price_loop():
    global last_update
    while True:
        for symbol, ticker in tickers.items():
            try:
                hist = ticker.history(period="1d", interval="1m")
                if not hist.empty:
                    prices[symbol] = round(float(hist["Close"].iloc[-1]), 2)
            except:
                pass

        last_update = datetime.now().strftime("%H:%M:%S")
        evaluate_signals()
        time.sleep(REFRESH_SECONDS)


# ================= API =================
@app.route("/api/data", methods=["GET", "POST"])
def api_data():
    rows = []
    for s, cfg in WATCHLIST.items():
        price = prices.get(s)
        lot = int(ACCOUNT_RISK_TL / max(abs(cfg["upper"] - cfg["lower"]), 0.01)) if cfg["signal"] != "BEKLE" else 0

        rows.append({
            "symbol": s,
            "price": price,
            "lower": cfg["lower"],
            "upper": cfg["upper"],
            "signal": cfg["signal"],
            "lot": lot,
            "risk": ACCOUNT_RISK_TL if lot > 0 else 0
        })

    return jsonify({
        "last_update": last_update,
        "rows": rows
    })


# ================= UI =================
@app.route("/", methods=["GET"])
def home():
    html = """
    <h2>BIST Profesyonel Trading Sistemi</h2>
    <p>Son güncelleme: <b id="time">-</b></p>

    <table border="1" cellpadding="6">
      <tr>
        <th>Hisse</th><th>Fiyat</th><th>Alt</th><th>Üst</th>
        <th>Sinyal</th><th>Lot</th><th>Risk</th>
      </tr>
      <tbody id="body"></tbody>
    </table>

    <script>
    async function refresh(){
      const r = await fetch("/api/data");
      const d = await r.json();
      document.getElementById("time").innerText = d.last_update;

      const body = document.getElementById("body");
      body.innerHTML = "";
      d.rows.forEach(x => {
        body.innerHTML += `
          <tr>
            <td>${x.symbol}</td>
            <td>${x.price ?? "-"}</td>
            <td>${x.lower}</td>
            <td>${x.upper}</td>
            <td>${x.signal}</td>
            <td>${x.lot}</td>
            <td>${x.risk}</td>
          </tr>`;
      });
    }
    setInterval(refresh, 5000);
    refresh();
    </script>
    """
    return render_template_string(html)


# ================= START =================
threading.Thread(target=price_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
