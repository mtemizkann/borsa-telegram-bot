import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify
from datetime import datetime

app = Flask(__name__)

# ================== CONFIG ==================
TOKEN = os.environ.get("TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

ACCOUNT_SIZE = 150000
RISK_PERCENT = 2

WATCHLIST = {
    "ASELS.IS": {"lower": 290.0, "upper": 310.0, "alerted": None},
    "TUPRS.IS": {"lower": 140.0, "upper": 170.0, "alerted": None},
    "FROTO.IS": {"lower": 850.0, "upper": 900.0, "alerted": None},
}

TICKERS = {s: yf.Ticker(s) for s in WATCHLIST}

# ================== UTILS ==================
def parse_price(v):
    return float(v.replace(",", ".").strip())


def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18


def send_telegram(msg):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=5,
        )
    except Exception:
        pass


# ================== PRICE & MOMENTUM ==================
def get_price_and_momentum(symbol):
    """
    Son 6 mum alınır:
    - Son fiyat
    - Son 5 mum momentum (yükseliyor / düşüyor / yatay)
    """
    try:
        hist = TICKERS[symbol].history(period="1d", interval="1m")
        if hist.empty or len(hist) < 6:
            return None, "FLAT"

        closes = hist["Close"].iloc[-6:].tolist()
        price = round(float(closes[-1]), 2)

        ups = sum(1 for i in range(1, 6) if closes[i] > closes[i - 1])
        downs = sum(1 for i in range(1, 6) if closes[i] < closes[i - 1])

        if ups >= 4:
            momentum = "UP"
        elif downs >= 4:
            momentum = "DOWN"
        else:
            momentum = "FLAT"

        return price, momentum

    except Exception:
        return None, "FLAT"


# ================== SMART SIGNAL ENGINE ==================
def generate_signal(price, lower, upper, momentum):
    if price is None:
        return "VERİ YOK"

    # AL: fiyat düşük + momentum yukarı
    if price <= lower * 1.005 and momentum == "UP":
        return "AL"

    # SAT: fiyat yüksek + momentum aşağı
    if price >= upper * 0.995 and momentum == "DOWN":
        return "SAT"

    return "BEKLE"


# ================== BACKGROUND TELEGRAM ==================
def price_monitor():
    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            for s, cfg in WATCHLIST.items():
                price, momentum = get_price_and_momentum(s)
                if price is None:
                    continue

                signal = generate_signal(
                    price, cfg["lower"], cfg["upper"], momentum
                )

                if signal == "AL" and cfg["alerted"] != "AL":
                    send_telegram(
                        f"🟢 AL SİNYALİ\n{s}\nFiyat: {price}\nMomentum: ↑"
                    )
                    cfg["alerted"] = "AL"

                elif signal == "SAT" and cfg["alerted"] != "SAT":
                    send_telegram(
                        f"🔴 SAT SİNYALİ\n{s}\nFiyat: {price}\nMomentum: ↓"
                    )
                    cfg["alerted"] = "SAT"

                elif signal == "BEKLE":
                    cfg["alerted"] = None

            time.sleep(30)

        except Exception:
            time.sleep(10)


# ================== API ==================
@app.route("/api/data")
def api_data():
    data = {}

    for s, cfg in WATCHLIST.items():
        price, momentum = get_price_and_momentum(s)
        signal = generate_signal(
            price, cfg["lower"], cfg["upper"], momentum
        )

        data[s] = {
            "price": price,
            "lower": cfg["lower"],
            "upper": cfg["upper"],
            "signal": signal,
            "momentum": momentum,
        }

    return jsonify(data)


# ================== WEB PANEL ==================
@app.route("/", methods=["GET", "POST"])
def home():
    error = None

    if request.method == "POST":
        try:
            s = request.form["symbol"]
            WATCHLIST[s]["lower"] = parse_price(request.form["lower"])
            WATCHLIST[s]["upper"] = parse_price(request.form["upper"])
            WATCHLIST[s]["alerted"] = None
        except Exception as e:
            error = str(e)

    html = """
    <html>
    <head>
        <title>BIST Akıllı Panel</title>
        <style>
            body { background:#0b0b0b; color:white; font-family:Arial; padding:40px; }
            table { width:100%; border-collapse:collapse; }
            th, td { padding:12px; border-bottom:1px solid #333; text-align:center; }
            th { background:#1e1e1e; }
            .badge { padding:6px 14px; border-radius:14px; font-weight:bold; }
            .AL { background:#0f5132; color:#9cffd0; }
            .SAT { background:#842029; color:#ffb3b3; }
            .BEKLE { background:#41464b; }
            .UP { color:#6cff6c; }
            .DOWN { color:#ff6c6c; }
            .FLAT { color:#ccc; }
            input, select, button { padding:8px; margin:4px; }
            button { background:#0a84ff; color:white; border:none; }
        </style>
    </head>
    <body>

    <h2>📊 BIST Akıllı Manuel Alarm Sistemi</h2>

    {% if error %}<div style="color:red">{{error}}</div>{% endif %}

    <table>
        <tr>
            <th>Hisse</th>
            <th>Fiyat</th>
            <th>Alt</th>
            <th>Üst</th>
            <th>Momentum</th>
            <th>Sinyal</th>
        </tr>
        {% for s in watchlist %}
        <tr>
            <td>{{s}}</td>
            <td id="price-{{s}}">-</td>
            <td>{{watchlist[s]["lower"]}}</td>
            <td>{{watchlist[s]["upper"]}}</td>
            <td id="mom-{{s}}">-</td>
            <td id="sig-{{s}}">-</td>
        </tr>
        {% endfor %}
    </table>

    <h3>Limit Güncelle</h3>
    <form method="post">
        <select name="symbol">
            {% for s in watchlist %}
            <option>{{s}}</option>
            {% endfor %}
        </select>
        <input name="lower" placeholder="Alt (294,25)">
        <input name="upper" placeholder="Üst (310)">
        <button>Güncelle</button>
    </form>

    <script>
    async function refresh() {
        const r = await fetch("/api/data");
        const d = await r.json();

        for (const s in d) {
            document.getElementById("price-"+s).innerText = d[s].price ?? "YOK";
            document.getElementById("mom-"+s).innerHTML =
                "<span class='"+d[s].momentum+"'>"+d[s].momentum+"</span>";

            document.getElementById("sig-"+s).innerHTML =
                "<span class='badge "+d[s].signal+"'>"+d[s].signal+"</span>";
        }
    }

    setInterval(refresh, 15000);
    refresh();
    </script>

    </body>
    </html>
    """

    return render_template_string(html, watchlist=WATCHLIST, error=error)


# ================== START ==================
threading.Thread(target=price_monitor, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
