import os
import json
import time
import threading
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify, abort

app = Flask(__name__)

# ---------------- ENV / VARS (Railway Variables) ----------------
TOKEN = os.environ.get("TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
SECRET = os.environ.get("SECRET", "").strip()  # webhook auth (opsiyonel)

# Swing/Risk ayarları
ACCOUNT_SIZE = float(os.environ.get("ACCOUNT_SIZE", "150000").replace(",", "."))
RISK_PERCENT = float(os.environ.get("RISK_PERCENT", "2").replace(",", "."))

# İzlenecek hisseler
WATCHLIST: Dict[str, Dict[str, Any]] = {
    "ASELS.IS": {"lower": 290.0, "upper": 310.0, "alerted": None},
    "TUPRS.IS": {"lower": 140.0, "upper": 170.0, "alerted": None},
    "FROTO.IS": {"lower": 850.0, "upper": 900.0, "alerted": None},
}

# Ticker cache
_TICKERS: Dict[str, yf.Ticker] = {}

# Thread safety
_state_lock = threading.Lock()
_monitor_started = False
_monitor_lock = threading.Lock()


# ---------------- HELPERS ----------------
def tr_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    """'294,25' -> 294.25 ; '' -> default ; None -> default"""
    try:
        if x is None:
            return default
        s = str(x).strip()
        if not s:
            return default
        return float(s.replace(",", "."))
    except Exception:
        return default


def safe_round(x: Any, ndigits: int = 2) -> Optional[float]:
    try:
        if x is None:
            return None
        return round(float(x), ndigits)
    except Exception:
        return None


def market_open() -> bool:
    # Basit kontrol: Hafta içi 09:00-18:00
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18


def get_ticker(symbol: str) -> yf.Ticker:
    t = _TICKERS.get(symbol)
    if t is None:
        t = yf.Ticker(symbol)
        _TICKERS[symbol] = t
    return t


def fetch_last_price(symbol: str) -> Optional[float]:
    """1 dakikalık son kapanış. Veri yoksa None."""
    try:
        t = get_ticker(symbol)
        hist = t.history(period="1d", interval="1m", actions=False)
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
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


def calculate_position(entry: float, stop: float) -> Tuple[int, float]:
    """Basit swing: risk = ACCOUNT_SIZE * RISK_PERCENT ; lot = risk / |entry-stop|"""
    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100.0)
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0:
        return 0, 0.0
    lot = int(risk_amount / per_share_risk)
    total_risk = lot * per_share_risk
    return lot, total_risk


def send_telegram(message: str) -> None:
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message},
            timeout=5,
        )
    except Exception:
        pass


# ---------------- SWING MONITOR (BACKGROUND) ----------------
def price_monitor_loop() -> None:
    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            # snapshot al (lock altında)
            with _state_lock:
                snapshot = json.loads(json.dumps(WATCHLIST))

            for symbol, d in snapshot.items():
                lower = float(d["lower"])
                upper = float(d["upper"])

                price = fetch_last_price(symbol)
                if price is None:
                    continue

                # Orijinal state'e geri yazacağımız için lock kullanacağız
                with _state_lock:
                    st = WATCHLIST.get(symbol)
                    if not st:
                        continue

                    alerted = st.get("alerted")

                    # AL kırılım: price <= lower
                    if price <= lower and alerted != "lower":
                        stop = upper  # örnek swing: karşı band stop
                        lot, total_risk = calculate_position(price, stop)
                        send_telegram(
                            f"🟢 AL SİNYALİ\n{symbol}\n"
                            f"Fiyat: {safe_round(price,2)}\n"
                            f"Alt: {safe_round(lower,2)} | Üst: {safe_round(upper,2)}\n"
                            f"Stop: {safe_round(stop,2)}\n"
                            f"Lot: {lot}\nRisk: {safe_round(total_risk,2)}"
                        )
                        st["alerted"] = "lower"

                    # SAT kırılım: price >= upper
                    elif price >= upper and alerted != "upper":
                        stop = lower
                        lot, total_risk = calculate_position(price, stop)
                        send_telegram(
                            f"🔴 SAT SİNYALİ\n{symbol}\n"
                            f"Fiyat: {safe_round(price,2)}\n"
                            f"Alt: {safe_round(lower,2)} | Üst: {safe_round(upper,2)}\n"
                            f"Stop: {safe_round(stop,2)}\n"
                            f"Lot: {lot}\nRisk: {safe_round(total_risk,2)}"
                        )
                        st["alerted"] = "upper"

                    # Band içine döndüyse reset
                    elif lower < price < upper:
                        st["alerted"] = None

            time.sleep(30)

        except Exception:
            time.sleep(10)


def ensure_monitor_started() -> None:
    global _monitor_started
    if _monitor_started:
        return
    with _monitor_lock:
        if _monitor_started:
            return
        threading.Thread(target=price_monitor_loop, daemon=True).start()
        _monitor_started = True


@app.before_request
def _start_bg_once():
    # Gunicorn altında her worker için 1 thread (normal/istenen davranış)
    ensure_monitor_started()


# ---------------- API ----------------
@app.route("/api/data", methods=["GET"])
def api_data():
    with _state_lock:
        snapshot = json.loads(json.dumps(WATCHLIST))  # deep-ish copy

    prices: Dict[str, Optional[float]] = {}
    signals: Dict[str, str] = {}

    for s, d in snapshot.items():
        p = fetch_last_price(s)
        prices[s] = safe_round(p, 2)
        signals[s] = generate_signal(p, float(d["lower"]), float(d["upper"]))

    return jsonify({"prices": prices, "watchlist": snapshot, "signals": signals})


# ---------------- TradingView Webhook (Opsiyonel) ----------------
@app.route("/tv", methods=["POST"])
def tv_webhook():
    # SECRET koyduysan zorunlu
    if SECRET:
        incoming = request.args.get("secret") or request.headers.get("X-SECRET")
        if (incoming or "").strip() != SECRET:
            abort(401)

    payload = request.get_json(silent=True) or {}
    # Örnek payload: {"symbol":"ASELS.IS","lower":295.5,"upper":312.0}
    symbol = str(payload.get("symbol", "")).strip()
    lower = tr_float(payload.get("lower"))
    upper = tr_float(payload.get("upper"))

    if not symbol or lower is None or upper is None:
        return jsonify({"ok": False, "error": "symbol/lower/upper required"}), 400

    with _state_lock:
        if symbol not in WATCHLIST:
            WATCHLIST[symbol] = {"lower": float(lower), "upper": float(upper), "alerted": None}
        else:
            WATCHLIST[symbol]["lower"] = float(lower)
            WATCHLIST[symbol]["upper"] = float(upper)
            WATCHLIST[symbol]["alerted"] = None

    return jsonify({"ok": True, "symbol": symbol, "lower": lower, "upper": upper})


# ---------------- WEB PANEL ----------------
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        symbol = str(request.form.get("symbol", "")).strip()
        lower = tr_float(request.form.get("lower"))
        upper = tr_float(request.form.get("upper"))

        if not symbol or lower is None or upper is None:
            # Basit validasyon: 400 dönmek yerine ekranda kal
            pass
        else:
            with _state_lock:
                if symbol not in WATCHLIST:
                    WATCHLIST[symbol] = {"lower": float(lower), "upper": float(upper), "alerted": None}
                else:
                    WATCHLIST[symbol]["lower"] = float(lower)
                    WATCHLIST[symbol]["upper"] = float(upper)
                    WATCHLIST[symbol]["alerted"] = None

    with _state_lock:
        snapshot = json.loads(json.dumps(WATCHLIST))

    html = """
    <html>
    <head>
        <title>BIST Professional Panel</title>
        <style>
            body { background:#0e0e0e; color:white; font-family:Arial; padding:40px; }
            table { width:100%; border-collapse:collapse; margin-bottom:30px; }
            th, td { padding:12px; border-bottom:1px solid #333; text-align:center; }
            th { background:#1e1e1e; }
            .badge { padding:6px 12px; border-radius:12px; font-weight:bold; display:inline-block; min-width:60px; }
            .buy { background:#0f5132; color:#9cffd0; }
            .sell { background:#842029; color:#ffb3b3; }
            .wait { background:#41464b; color:#e2e3e5; }
            .nov { background:#2b2b2b; color:#cfcfcf; }
            input, select { padding:8px; margin:5px; }
            button { padding:10px 20px; background:#0a84ff; color:white; border:none; cursor:pointer; }
            small { color:#aaa; }
        </style>
    </head>
    <body>
        <h2>📊 BIST Panel + Swing Monitor</h2>
        <small>API: /api/data • Webhook (opsiyonel): /tv • Market saatleri: 09:00-18:00 (basit)</small>

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
            {% for s, d in watchlist.items() %}
                <tr>
                    <td>{{s}}</td>
                    <td id="price-{{s}}">-</td>
                    <td>{{d["lower"]}}</td>
                    <td>{{d["upper"]}}</td>
                    <td id="signal-{{s}}">-</td>
                </tr>
            {% endfor %}
            </tbody>
        </table>

        <h3>Limit Güncelle</h3>
        <form method="post">
            <select name="symbol">
                {% for s in watchlist.keys() %}
                    <option value="{{s}}">{{s}}</option>
                {% endfor %}
            </select>
            <input name="lower" placeholder="Alt Limit (örn 294,25 veya 294.25)">
            <input name="upper" placeholder="Üst Limit (örn 310,00 veya 310.00)">
            <button type="submit">Güncelle</button>
        </form>

        <script>
        async function refresh() {
            const r = await fetch("/api/data");
            const d = await r.json();

            for (const s in d.prices) {
                const price = d.prices[s];
                const signal = d.signals[s];

                document.getElementById("price-" + s).innerText =
                    (price === null) ? "Veri Yok" : price;

                const cell = document.getElementById("signal-" + s);
                cell.innerHTML = "";

                const badge = document.createElement("span");
                badge.classList.add("badge");

                if (signal === "AL") { badge.classList.add("buy"); badge.innerText = "AL"; }
                else if (signal === "SAT") { badge.classList.add("sell"); badge.innerText = "SAT"; }
                else if (signal === "VERİ YOK") { badge.classList.add("nov"); badge.innerText = "YOK"; }
                else { badge.classList.add("wait"); badge.innerText = "BEKLE"; }

                cell.appendChild(badge);
            }
        }

        setInterval(refresh, 15000);
        refresh();
        </script>
    </body>
    </html>
    """
    return render_template_string(html, watchlist=snapshot)


# ---------------- LOCAL DEV ----------------
if __name__ == "__main__":
    ensure_monitor_started()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
