import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, render_template_string, jsonify, request
from datetime import datetime

app = Flask(__name__)

# ================== CONFIG ==================
WATCHLIST = {
    "ASELS.IS": {"lower": 284, "upper": 286},
    "TUPRS.IS": {"lower": 226, "upper": 229},
    "FROTO.IS": {"lower": 114, "upper": 116},
}

ACCOUNT_RISK_TL = 3000
TOKEN = os.environ.get("TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# ================== STATE ==================
price_cache = {}
last_update = None

# ================== TELEGRAM ==================
def send_telegram(msg):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=5,
        )
    except:
        pass

# ================== PRICE FETCH ==================
def fetch_prices():
    global price_cache, last_update

    while True:
        for symbol in WATCHLIST:
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1d", interval="1m")

                if hist.empty:
                    price_cache[symbol] = None
                else:
                    price_cache[symbol] = round(float(hist["Close"].iloc[-1]), 2)

            except:
                price_cache[symbol] = None

        last_update = datetime.now()
        time.sleep(10)

# ================== SIGNAL ENGINE ==================
def generate_signal(price, lower, upper):
    if price is None:
        return "VERİ YOK", 0, 0, 0

    if price <= lower:
        signal = "AL"
        confidence = 70
    elif price >= upper:
        signal = "SAT"
        confidence = 70
    else:
        return "BEKLE", 50, 0, 0

    risk_per_unit = abs(upper - lower)
    lot = int(ACCOUNT_RISK_TL / risk_per_unit) if risk_per_unit > 0 else 0
    risk = lot * risk_per_unit

    return signal, confidence, lot, round(risk, 2)

# ================== API ==================
@app.route("/api/data")
def api_data():
    rows = []

    for s, limits in WATCHLIST.items():
        price = price_cache.get(s)

        signal, conf, lot, risk = generate_signal(
            price, limits["lower"], limits["upper"]
        )

        rows.append({
            "symbol": s,
            "price": price,
            "lower": limits["lower"],
            "upper": limits["upper"],
            "signal": signal,
            "confidence": conf,
            "lot": lot,
            "risk": risk,
        })

    return jsonify({
        "rows": rows,
        "last_update": last_update.strftime("%H:%M:%S") if last_update else "—",
    })

# ================== WEB ==================
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        s = request.form["symbol"]
        WATCHLIST[s]["lower"] = float(request.form["lower"].replace(",", "."))
        WATCHLIST[s]["upper"] = float(request.form["upper"].replace(",", "."))

    html = """
    <html>
    <head>
        <title>BIST Profesyonel Trading Sistemi</title>
        <style>
            body { background:#0b0b0b; color:white; font-family:Arial; padding:30px; }
            table { width:100%; border-collapse:collapse; margin-bottom:20px; }
            th, td { padding:12px; border-bottom:1px solid #333; text-align:center; }
            th { background:#1b1b1b; }
            .AL { background:#143d2a; }
            .SAT { background:#3d1414; }
            .BEKLE { background:#2a2a2a; }
            .badge { padding:6px 14px; border-radius:14px; font-weight:bold; }
            .green { background:#1f7a4d; }
            .red { background:#8b2e2e; }
            .gray { background:#555; }
            .info { color:#aaa; font-size:13px; margin-bottom:10px; }
        </style>
    </head>
    <body>

    <h2>📊 BIST Profesyonel Trading Sistemi</h2>
    <div class="info">Son güncelleme: <span id="last">—</span></div>

    <table>
        <thead>
            <tr>
                <th>Hisse</th><th>Fiyat</th><th>Alt</th><th>Üst</th>
                <th>Sinyal</th><th>Confidence</th><th>Lot</th><th>Risk (TL)</th>
            </tr>
        </thead>
        <tbody id="rows"></tbody>
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
        <button>Güncelle</button>
    </form>

    <script>
    async function refresh(){
        const r = await fetch("/api/data");
        const d = await r.json();

        document.getElementById("last").innerText = d.last_update;
        const tbody = document.getElementById("rows");
        tbody.innerHTML = "";

        d.rows.forEach(x => {
            const tr = document.createElement("tr");
            tr.className = x.signal;

            let badge = "";
            if (x.signal === "AL") badge = "<span class='badge green'>AL</span>";
            else if (x.signal === "SAT") badge = "<span class='badge red'>SAT</span>";
            else badge = "<span class='badge gray'>BEKLE</span>";

            tr.innerHTML = `
                <td>${x.symbol}</td>
                <td>${x.price ?? "—"}</td>
                <td>${x.lower}</td>
                <td>${x.upper}</td>
                <td>${badge}</td>
                <td>%${x.confidence}</td>
                <td>${x.lot}</td>
                <td>${x.risk}</td>
            `;
            tbody.appendChild(tr);
        });
    }
    setInterval(refresh, 10000);
    refresh();
    </script>

    </body>
    </html>
    """

    return render_template_string(html, watchlist=WATCHLIST)

# ================== START ==================
threading.Thread(target=fetch_prices, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
