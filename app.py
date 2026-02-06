import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

# ================== AYARLAR ==================
TOKEN = os.environ.get("TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

ACCOUNT_SIZE = 150000
RISK_PERCENT = 2

TR_TZ = timezone(timedelta(hours=3))

WATCHLIST = {
    "ASELS.IS": {"lower": 284, "upper": 286, "alerted": None},
    "TUPRS.IS": {"lower": 226, "upper": 229, "alerted": None},
    "FROTO.IS": {"lower": 114, "upper": 116, "alerted": None},
}

TICKERS = {s: yf.Ticker(s) for s in WATCHLIST}

LATEST = {
    "prices": {},
    "ts": None
}

# ================== MARKET ==================
def market_open():
    now = datetime.now(TR_TZ)
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18

# ================== TELEGRAM ==================
def send(msg):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=5
        )
    except Exception:
        pass

# ================== FİYAT ÇEKME (ARKA PLAN) ==================
def fetch_loop():
    while True:
        prices = {}
        for symbol in WATCHLIST:
            try:
                df = yf.download(
                    symbol,
                    period="1d",
                    interval="1m",
                    progress=False,
                    threads=False,
                )
                if df is not None and not df.empty:
                    prices[symbol] = round(float(df["Close"].iloc[-1]), 2)
                else:
                    prices[symbol] = None
            except Exception:
                prices[symbol] = None

        LATEST["prices"] = prices
        LATEST["ts"] = datetime.now(TR_TZ).strftime("%H:%M:%S")
        time.sleep(7)

threading.Thread(target=fetch_loop, daemon=True).start()

# ================== SİNYAL ==================
def signal(price, low, high):
    if price is None:
        return "VERİ YOK"
    if price <= low:
        return "AL"
    if price >= high:
        return "SAT"
    return "BEKLE"

# ================== TELEGRAM MONITOR ==================
def monitor_loop():
    while True:
        if not market_open():
            time.sleep(60)
            continue

        for s, data in WATCHLIST.items():
            price = LATEST["prices"].get(s)
            if price is None:
                continue

            sig = signal(price, data["lower"], data["upper"])

            if sig == "AL" and data["alerted"] != "AL":
                send(f"🟢 AL SİNYALİ\n{s}\nFiyat: {price}")
                data["alerted"] = "AL"

            elif sig == "SAT" and data["alerted"] != "SAT":
                send(f"🔴 SAT SİNYALİ\n{s}\nFiyat: {price}")
                data["alerted"] = "SAT"

            elif sig == "BEKLE":
                data["alerted"] = None

        time.sleep(10)

threading.Thread(target=monitor_loop, daemon=True).start()

# ================== API ==================
@app.route("/api/data")
def api_data():
    prices = LATEST["prices"]
    rows = []

    for s, cfg in WATCHLIST.items():
        p = prices.get(s)
        sig = signal(p, cfg["lower"], cfg["upper"])

        lot = 0
        risk = 0
        if sig in ("AL", "SAT") and p:
            risk = ACCOUNT_SIZE * (RISK_PERCENT / 100)
            lot = int(risk / abs(cfg["upper"] - cfg["lower"]))

        rows.append({
            "symbol": s,
            "price": p,
            "lower": cfg["lower"],
            "upper": cfg["upper"],
            "signal": sig,
            "lot": lot,
            "risk": round(risk, 2),
        })

    return jsonify({
        "rows": rows,
        "ts": LATEST["ts"]
    })

# ================== PANEL ==================
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        s = request.form["symbol"]
        WATCHLIST[s]["lower"] = float(request.form["lower"].replace(",", "."))
        WATCHLIST[s]["upper"] = float(request.form["upper"].replace(",", "."))
        WATCHLIST[s]["alerted"] = None

    html = """
    <html>
    <head>
    <title>BIST Profesyonel Trading Sistemi</title>
    <style>
    body { background:#0b0b0b; color:white; font-family:Arial; padding:30px; }
    table { width:100%; border-collapse:collapse; }
    th, td { padding:10px; border-bottom:1px solid #333; text-align:center; }
    th { background:#1c1c1c; }
    tr.al { background:#123b25; }
    tr.sat { background:#4b1616; }
    .btn { padding:8px 16px; background:#0a84ff; border:none; color:white; }
    input, select { padding:6px; }
    </style>
    </head>
    <body>

    <h2>📊 BIST Profesyonel Trading Sistemi</h2>
    <div>Son güncelleme: <span id="ts">-</span></div>

    <table>
    <thead>
    <tr>
        <th>Hisse</th><th>Fiyat</th><th>Alt</th><th>Üst</th>
        <th>Sinyal</th><th>Lot</th><th>Risk</th>
    </tr>
    </thead>
    <tbody id="body"></tbody>
    </table>

    <h3>Limit Güncelle</h3>
    <form method="post">
        <select name="symbol">
        {% for s in watchlist %}
            <option value="{{s}}">{{s}}</option>
        {% endfor %}
        </select>
        <input name="lower" placeholder="Alt">
        <input name="upper" placeholder="Üst">
        <button class="btn">Güncelle</button>
    </form>

    <script>
    async function refresh(){
        const r = await fetch("/api/data");
        const d = await r.json();
        document.getElementById("ts").innerText = d.ts;
        let html = "";
        d.rows.forEach(x=>{
            let cls = x.signal === "AL" ? "al" : x.signal === "SAT" ? "sat" : "";
            html += `<tr class="${cls}">
                <td>${x.symbol}</td>
                <td>${x.price ?? "-"}</td>
                <td>${x.lower}</td>
                <td>${x.upper}</td>
                <td>${x.signal}</td>
                <td>${x.lot}</td>
                <td>${x.risk}</td>
            </tr>`;
        });
        document.getElementById("body").innerHTML = html;
    }
    setInterval(refresh, 5000);
    refresh();
    </script>

    </body>
    </html>
    """
    return render_template_string(html, watchlist=WATCHLIST)

# ================== START ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
