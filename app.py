import os
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional

import requests
import yfinance as yf
from flask import Flask, request, jsonify, render_template_string, abort

app = Flask(__name__)

# ========= CONFIG =========
TOKEN = os.environ.get("TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

# BIST trading hours (simple): Mon–Fri, 09:00–18:00 (server local time)
MARKET_START_HOUR = int(os.environ.get("MARKET_START_HOUR", "9"))
MARKET_END_HOUR = int(os.environ.get("MARKET_END_HOUR", "18"))

# Monitoring
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "30"))

# Default watchlist (can be updated from UI / API)
WATCHLIST: Dict[str, Dict[str, Any]] = {
    "ASELS.IS": {"lower": 290.0, "upper": 310.0, "alerted": None},
    "TUPRS.IS": {"lower": 140.0, "upper": 170.0, "alerted": None},
    "FROTO.IS": {"lower": 850.0, "upper": 900.0, "alerted": None},
}

# yfinance Ticker cache
TICKERS: Dict[str, yf.Ticker] = {s: yf.Ticker(s) for s in WATCHLIST.keys()}

# Thread guard
_monitor_started = False
_monitor_lock = threading.Lock()


# ========= HELPERS =========
def parse_float(value: str) -> float:
    """
    Accepts inputs like:
      "294.25", "294,25", " 294,25 ", "1.234,56", "1,234.56"
    Tries to normalize to a float.
    """
    if value is None:
        raise ValueError("empty")

    s = str(value).strip().replace(" ", "")

    if not s:
        raise ValueError("empty")

    # If both separators exist, decide which is decimal by last occurrence
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # 1.234,56 -> 1234.56
            s = s.replace(".", "").replace(",", ".")
        else:
            # 1,234.56 -> 1234.56
            s = s.replace(",", "")
    else:
        # Only comma: treat as decimal separator
        if "," in s:
            s = s.replace(".", "")  # handle "1.234" style thousands just in case
            s = s.replace(",", ".")
    return float(s)


def market_open(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return MARKET_START_HOUR <= now.hour < MARKET_END_HOUR


def telegram_send(message: str) -> None:
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message},
            timeout=8,
        )
    except Exception:
        # keep silent; don't crash the worker
        pass


def ensure_ticker(symbol: str) -> yf.Ticker:
    if symbol not in TICKERS:
        TICKERS[symbol] = yf.Ticker(symbol)
    return TICKERS[symbol]


def get_last_price(symbol: str) -> Optional[float]:
    try:
        t = ensure_ticker(symbol)
        hist = t.history(period="1d", interval="1m", actions=False)
        if hist is None or hist.empty:
            return None
        return round(float(hist["Close"].iloc[-1]), 2)
    except Exception:
        return None


def generate_signal(price: Optional[float], lower: float, upper: float) -> str:
    if price is None:
        return "VERİ YOK"
    if price <= lower:
        return "AL"
    if price >= upper:
        return "SAT"
    return "BEKLE"


# ========= MONITOR =========
def price_monitor() -> None:
    app.logger.info("Price monitor started")
    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            for symbol, data in list(WATCHLIST.items()):
                price = get_last_price(symbol)
                if price is None:
                    continue

                lower = float(data["lower"])
                upper = float(data["upper"])

                # AL signal
                if price <= lower and data.get("alerted") != "lower":
                    telegram_send(f"🟢 AL SİNYALİ\n{symbol}\nFiyat: {price}")
                    data["alerted"] = "lower"

                # SAT signal
                elif price >= upper and data.get("alerted") != "upper":
                    telegram_send(f"🔴 SAT SİNYALİ\n{symbol}\nFiyat: {price}")
                    data["alerted"] = "upper"

                # reset when back in range
                elif lower < price < upper:
                    data["alerted"] = None

            time.sleep(POLL_SECONDS)

        except Exception:
            time.sleep(10)


def start_monitor_once() -> None:
    global _monitor_started
    with _monitor_lock:
        if _monitor_started:
            return
        threading.Thread(target=price_monitor, daemon=True).start()
        _monitor_started = True


@app.before_request
def _auto_start_monitor():
    # Starts monitor on first incoming request (safe for Railway/Gunicorn single worker)
    start_monitor_once()


# ========= API =========
@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/data")
def api_data():
    prices = {s: get_last_price(s) for s in WATCHLIST.keys()}
    signals = {
        s: generate_signal(prices[s], float(WATCHLIST[s]["lower"]), float(WATCHLIST[s]["upper"]))
        for s in WATCHLIST.keys()
    }
    return jsonify({"prices": prices, "watchlist": WATCHLIST, "signals": signals})


@app.post("/api/watchlist")
def api_update_watchlist():
    """
    JSON body:
    {
      "symbol": "ASELS.IS",
      "lower": "294,25",
      "upper": "310"
    }
    """
    data = request.get_json(silent=True) or {}
    symbol = str(data.get("symbol", "")).strip()
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    try:
        lower = float(parse_float(str(data.get("lower", ""))))
        upper = float(parse_float(str(data.get("upper", ""))))
    except Exception:
        return jsonify({"error": "lower/upper must be numeric"}), 400

    if lower >= upper:
        return jsonify({"error": "lower must be < upper"}), 400

    if symbol not in WATCHLIST:
        WATCHLIST[symbol] = {"lower": lower, "upper": upper, "alerted": None}
    else:
        WATCHLIST[symbol]["lower"] = lower
        WATCHLIST[symbol]["upper"] = upper
        WATCHLIST[symbol]["alerted"] = None

    ensure_ticker(symbol)
    return jsonify({"ok": True, "watchlist": WATCHLIST})


# ========= WEB PANEL =========
HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>BIST Alarm Paneli</title>
    <style>
      body { background:#0e0e0e; color:#fff; font-family:Arial, sans-serif; padding:32px; }
      h2 { margin-top: 0; }
      table { width:100%; border-collapse:collapse; margin: 18px 0 28px; }
      th, td { padding:12px; border-bottom:1px solid #333; text-align:center; }
      th { background:#1e1e1e; }
      .badge { display:inline-block; padding:6px 12px; border-radius:12px; font-weight:bold; }
      .buy { background:#0f5132; color:#9cffd0; }
      .sell { background:#842029; color:#ffb3b3; }
      .wait { background:#41464b; color:#e2e3e5; }
      .na { background:#2b2b2b; color:#cfcfcf; }
      input, select { padding:10px; margin:6px 6px 6px 0; border-radius:10px; border:1px solid #333; background:#111; color:#fff; }
      button { padding:10px 18px; background:#0a84ff; color:#fff; border:none; border-radius:10px; cursor:pointer; }
      .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
      .card { background:#121212; border:1px solid #222; border-radius:16px; padding:16px; }
      .hint { color:#aaa; font-size: 13px; }
      .err { color:#ff9b9b; margin: 8px 0 0; }
      .ok { color:#9cffd0; margin: 8px 0 0; }
    </style>
  </head>
  <body>
    <h2>📊 BIST Manuel Alarm Paneli</h2>

    <div class="card">
      <div class="row">
        <div class="hint">15 saniyede bir otomatik yenilenir. Virgüllü (294,25) veya noktalı (294.25) giriş kabul eder.</div>
      </div>

      <table>
        <thead>
          <tr>
            <th>Hisse</th>
            <th>Anlık Fiyat</th>
            <th>Alt Limit</th>
            <th>Üst Limit</th>
            <th>Sinyal</th>
          </tr>
        </thead>
        <tbody>
          {% for s in watchlist %}
          <tr>
            <td><b>{{ s }}</b></td>
            <td id="price-{{ s }}">-</td>
            <td id="lower-{{ s }}">{{ watchlist[s]["lower"] }}</td>
            <td id="upper-{{ s }}">{{ watchlist[s]["upper"] }}</td>
            <td id="signal-{{ s }}">-</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>Limit Güncelle</h3>
      <form method="post">
        <div class="row">
          <select name="symbol">
            {% for s in watchlist %}
              <option value="{{ s }}">{{ s }}</option>
            {% endfor %}
          </select>
          <input name="lower" placeholder="Alt Limit (örn: 294,25)" required />
          <input name="upper" placeholder="Üst Limit (örn: 310)" required />
          <button type="submit">Güncelle</button>
        </div>
      </form>

      {% if msg %}
        <div class="{{ 'ok' if ok else 'err' }}">{{ msg }}</div>
      {% endif %}
    </div>

    <script>
      function setBadge(cell, signal) {
        cell.innerHTML = "";
        const b = document.createElement("span");
        b.classList.add("badge");

        if (signal === "AL") { b.classList.add("buy"); b.innerText = "AL"; }
        else if (signal === "SAT") { b.classList.add("sell"); b.innerText = "SAT"; }
        else if (signal === "BEKLE") { b.classList.add("wait"); b.innerText = "BEKLE"; }
        else { b.classList.add("na"); b.innerText = signal || "VERİ YOK"; }

        cell.appendChild(b);
      }

      async function refresh() {
        try {
          const r = await fetch("/api/data", { cache: "no-store" });
          const d = await r.json();

          for (const s in d.watchlist) {
            const price = d.prices[s];
            const sig = d.signals[s];

            const p = document.getElementById("price-" + s);
            if (p) p.innerText = (price === null) ? "Veri Yok" : price;

            const sc = document.getElementById("signal-" + s);
            if (sc) setBadge(sc, sig);
          }
        } catch (e) {
          // ignore UI refresh errors
        }
      }

      setInterval(refresh, 15000);
      refresh();
    </script>
  </body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def home():
    msg = ""
    ok = True

    if request.method == "POST":
        symbol = request.form.get("symbol", "").strip()
        if symbol not in WATCHLIST:
            abort(400)

        try:
            lower = parse_float(request.form.get("lower", ""))
            upper = parse_float(request.form.get("upper", ""))
            if lower >= upper:
                raise ValueError("lower must be < upper")

            WATCHLIST[symbol]["lower"] = float(lower)
            WATCHLIST[symbol]["upper"] = float(upper)
            WATCHLIST[symbol]["alerted"] = None
            msg = f"{symbol} güncellendi: alt={lower} üst={upper}"
            ok = True
        except Exception:
            msg = "Hatalı giriş. Örnek: 294,25 veya 294.25"
            ok = False

    return render_template_string(HTML, watchlist=WATCHLIST, msg=msg, ok=ok)


# ========= ENTRYPOINT =========
if __name__ == "__main__":
    # Local run: python app.py
    start_monitor_once()
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
