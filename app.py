import os
import time
import threading
import requests
import yfinance as yf
from flask import Flask, request, jsonify, render_template_string, abort
from datetime import datetime
from pathlib import Path

app = Flask(__name__)

# ================= CONFIG =================
TOKEN = os.environ.get("TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
PORT = int(os.environ.get("PORT", 8080))

MARKET_START = 9
MARKET_END = 18
POLL_SECONDS = 30

LOCK_FILE = "/tmp/price_monitor.lock"

WATCHLIST = {
    "ASELS.IS": {"lower": 290.0, "upper": 310.0, "alerted": None},
    "TUPRS.IS": {"lower": 140.0, "upper": 170.0, "alerted": None},
    "FROTO.IS": {"lower": 850.0, "upper": 900.0, "alerted": None},
}

TICKERS = {s: yf.Ticker(s) for s in WATCHLIST}


# ================= HELPERS =================
def parse_float(val: str) -> float:
    s = val.strip().replace(" ", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return MARKET_START <= now.hour < MARKET_END


def send_telegram(msg: str):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=8,
        )
    except Exception:
        pass


def get_price(symbol):
    try:
        hist = TICKERS[symbol].history(period="1d", interval="1m")
        if hist.empty:
            return None
        return round(float(hist["Close"].iloc[-1]), 2)
    except Exception:
        return None


def signal_engine(price, lower, upper):
    if price is None:
        return "VERİ YOK"
    if price <= lower:
        return "AL"
    if price >= upper:
        return "SAT"
    return "BEKLE"


# ================= MONITOR =================
def price_monitor():
    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            for s, data in WATCHLIST.items():
                price = get_price(s)
                if price is None:
                    continue

                if price <= data["lower"] and data["alerted"] != "lower":
                    send_telegram(f"🟢 AL SİNYALİ\n{s}\nFiyat: {price}")
                    data["alerted"] = "lower"

                elif price >= data["upper"] and data["alerted"] != "upper":
                    send_telegram(f"🔴 SAT SİNYALİ\n{s}\nFiyat: {price}")
                    data["alerted"] = "upper"

                elif data["lower"] < price < data["upper"]:
                    data["alerted"] = None

            time.sleep(POLL_SECONDS)

        except Exception:
            time.sleep(10)


def start_monitor_once():
    lock = Path(LOCK_FILE)
    if lock.exists():
        return
    lock.touch()
    threading.Thread(target=price_monitor, daemon=True).start()


@app.before_request
def init():
    start_monitor_once()


# ================= API =================
@app.get("/api/data")
def api_data():
    prices = {s: get_price(s) for s in WATCHLIST}
    signals = {
        s: signal_engine(prices[s], WATCHLIST[s]["lower"], WATCHLIST[s]["upper"])
        for s in WATCHLIST
    }
    return jsonify({"prices": prices, "signals": signals, "watchlist": WATCHLIST})


# ================= TRADINGVIEW WEBHOOK =================
@app.post("/webhook/tradingview")
def tradingview_webhook():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON required"}), 400

    symbol = data.get("symbol")
    signal = data.get("signal")
    price = data.get("price")

    if not symbol or not signal:
        return jsonify({"error": "symbol & signal required"}), 400

    msg = f"📡 TradingView Sinyali\n{symbol}\nSinyal: {signal}"
    if price:
        msg += f"\nFiyat: {price}"

    send_telegram(msg)
    return jsonify({"ok": True})


# ================= WEB =================
HTML = """
<!doctype html>
<html>
<head>
<title>BIST Professional Panel</title>
<style>
body{background:#0e0e0e;color:white;font-family:Arial;padding:30px}
table{width:100%;border-collapse:collapse}
th,td{padding:12px;border-bottom:1px solid #333;text-align:center}
th{background:#1e1e1e}
.badge{padding:6px 12px;border-radius:12px;font-weight:bold}
.buy{background:#0f5132;color:#9cffd0}
.sell{background:#842029;color:#ffb3b3}
.wait{background:#41464b;color:#e2e3e5}
input,select,button{padding:10px;border-radius:10px;border:none}
button{background:#0a84ff;color:white}
</style>
</head>
<body>

<h2>📊 BIST Manuel Alarm & Sinyal Paneli</h2>

<table>
<thead>
<tr>
<th>Hisse</th><th>Fiyat</th><th>Alt</th><th>Üst</th><th>Sinyal</th>
</tr>
</thead>
<tbody>
{% for s in watchlist %}
<tr>
<td>{{s}}</td>
<td id="p-{{s}}">-</td>
<td>{{watchlist[s]["lower"]}}</td>
<td>{{watchlist[s]["upper"]}}</td>
<td id="sig-{{s}}">-</td>
</tr>
{% endfor %}
</tbody>
</table>

<h3>Limit Güncelle</h3>
<form method="post">
<select name="symbol">
{% for s in watchlist %}<option>{{s}}</option>{% endfor %}
</select>
<input name="lower" placeholder="Alt">
<input name="upper" placeholder="Üst">
<button>Güncelle</button>
</form>

<script>
async function refresh(){
 const r=await fetch("/api/data");
 const d=await r.json();
 for(const s in d.prices){
  document.getElementById("p-"+s).innerText=d.prices[s]??"YOK";
  const cell=document.getElementById("sig-"+s);
  cell.innerHTML="";
  let b=document.createElement("span");
  b.classList.add("badge");
  if(d.signals[s]=="AL"){b.classList.add("buy");b.innerText="AL";}
  else if(d.signals[s]=="SAT"){b.classList.add("sell");b.innerText="SAT";}
  else{b.classList.add("wait");b.innerText="BEKLE";}
  cell.appendChild(b);
 }
}
setInterval(refresh,15000);refresh();
</script>

</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        s = request.form["symbol"]
        WATCHLIST[s]["lower"] = parse_float(request.form["lower"])
        WATCHLIST[s]["upper"] = parse_float(request.form["upper"])
        WATCHLIST[s]["alerted"] = None
    return render_template_string(HTML, watchlist=WATCHLIST)


# ================= START =================
if __name__ == "__main__":
    start_monitor_once()
    app.run(host="0.0.0.0", port=PORT)
