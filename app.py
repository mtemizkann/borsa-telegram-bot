import os
import json
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional, Tuple

import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

# ================= ENV =================
TOKEN = os.environ.get("TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
RUN_MONITOR_IN_WEB = os.environ.get("RUN_MONITOR_IN_WEB", "false").strip().lower() == "true"

ACCOUNT_SIZE = float(os.environ.get("ACCOUNT_SIZE", "150000").replace(",", "."))
RISK_PERCENT = float(os.environ.get("RISK_PERCENT", "2").replace(",", "."))
BAND_SIZE_TL = float(os.environ.get("BAND_SIZE_TL", "1").replace(",", "."))
MIN_STOP_DISTANCE_TL = float(os.environ.get("MIN_STOP_DISTANCE_TL", "0.5").replace(",", "."))
MAX_STOP_DISTANCE_TL = float(os.environ.get("MAX_STOP_DISTANCE_TL", "20").replace(",", "."))
ALERT_COOLDOWN_SEC = int(float(os.environ.get("ALERT_COOLDOWN_SEC", "180").replace(",", ".")))
BUY_SCORE_THRESHOLD = int(float(os.environ.get("BUY_SCORE_THRESHOLD", "70").replace(",", ".")))
BUY_SETUP_COOLDOWN_SEC = int(float(os.environ.get("BUY_SETUP_COOLDOWN_SEC", "3600").replace(",", ".")))
ANALYSIS_REFRESH_SEC = int(float(os.environ.get("ANALYSIS_REFRESH_SEC", "300").replace(",", ".")))

# ================= STATE =================
WATCHLIST: Dict[str, Dict[str, Any]] = {
    "ASELS.IS": {
        "lower": 290.0,
        "upper": 310.0,
        "alerted": None,
        "initialized": False,
        "last_alert_at": 0.0,
        "last_setup_at": 0.0,
        "last_analysis_at": 0.0,
        "buy_setup": None,
    },
    "TUPRS.IS": {
        "lower": 140.0,
        "upper": 170.0,
        "alerted": None,
        "initialized": False,
        "last_alert_at": 0.0,
        "last_setup_at": 0.0,
        "last_analysis_at": 0.0,
        "buy_setup": None,
    },
    "FROTO.IS": {
        "lower": 850.0,
        "upper": 900.0,
        "alerted": None,
        "initialized": False,
        "last_alert_at": 0.0,
        "last_setup_at": 0.0,
        "last_analysis_at": 0.0,
        "buy_setup": None,
    },
}

_TICKERS: Dict[str, yf.Ticker] = {}
_state_lock = threading.Lock()
_monitor_started = False
_monitor_lock = threading.Lock()

# ================= HELPERS =================
def tr_float(x: Any, default: Optional[float] = None) -> Optional[float]:
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
    now = datetime.now(ZoneInfo("Europe/Istanbul"))
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 18

def get_ticker(symbol: str) -> yf.Ticker:
    if symbol not in _TICKERS:
        _TICKERS[symbol] = yf.Ticker(symbol)
    return _TICKERS[symbol]

def fetch_last_price(symbol: str) -> Optional[float]:
    try:
        t = get_ticker(symbol)
        hist = t.history(period="1d", interval="1m", actions=False, timeout=5)
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None

def fetch_daily_history(symbol: str):
    try:
        t = get_ticker(symbol)
        hist = t.history(period="2y", interval="1d", actions=False, timeout=8)
        if hist is None or hist.empty:
            return None
        return hist.dropna(subset=["Close"])
    except Exception:
        return None

def calculate_rsi(close_series, period: int = 14) -> Optional[float]:
    if close_series is None or len(close_series) < period + 2:
        return None
    delta = close_series.diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)
    avg_gain = up.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = down.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    last_gain = avg_gain.iloc[-1]
    last_loss = avg_loss.iloc[-1]
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return float(100 - (100 / (1 + rs)))

def evaluate_buy_setup(symbol: str, current_price: float) -> Optional[Dict[str, Any]]:
    hist = fetch_daily_history(symbol)
    if hist is None or len(hist) < 205:
        return None

    close = hist["Close"]
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
    rsi14 = calculate_rsi(close, 14)
    atr20 = (hist["High"] - hist["Low"]).rolling(20).mean().iloc[-1]
    breakout_level = hist["High"].tail(21).head(20).max()

    score = 0
    reasons = []

    if ema20 > ema50 > ema200:
        score += 30
        reasons.append("Trend yukari (EMA20>EMA50>EMA200)")

    pullback_distance = abs(current_price - float(ema20)) / current_price
    if pullback_distance <= 0.015:
        score += 20
        reasons.append("Fiyat EMA20'ye yakin")

    if current_price > float(breakout_level):
        score += 20
        reasons.append("20 gunluk direnc ustu")

    if rsi14 is not None and 45 <= rsi14 <= 65:
        score += 15
        reasons.append("RSI dengeli")
    elif rsi14 is not None and 40 <= rsi14 <= 70:
        score += 8
        reasons.append("RSI kabul edilebilir")

    if current_price > float(ema50):
        score += 10
        reasons.append("EMA50 uzeri")

    atr = float(atr20) if atr20 is not None else 0.0
    fallback_stop = current_price - max(BAND_SIZE_TL, 0.5)
    stop = min(float(ema20), current_price - (1.2 * atr)) if atr > 0 else fallback_stop
    stop = min(stop, current_price - 0.01)

    if not stop_distance_allowed(current_price, stop):
        return {
            "symbol": symbol,
            "score": score,
            "eligible": False,
            "reason": "Stop mesafesi filtre disi",
        }

    target1 = current_price + (2.0 * (current_price - stop))
    target2 = current_price + (3.0 * (current_price - stop))

    return {
        "symbol": symbol,
        "score": int(score),
        "eligible": score >= BUY_SCORE_THRESHOLD,
        "price": safe_round(current_price),
        "entry_low": safe_round(current_price * 0.997),
        "entry_high": safe_round(current_price * 1.003),
        "stop": safe_round(stop),
        "target1": safe_round(target1),
        "target2": safe_round(target2),
        "rr": safe_round((target1 - current_price) / max(current_price - stop, 0.01), 2),
        "ema20": safe_round(ema20),
        "ema50": safe_round(ema50),
        "ema200": safe_round(ema200),
        "rsi14": safe_round(rsi14),
        "reasons": reasons[:4],
    }

def generate_signal(price: Optional[float], lower: float, upper: float) -> str:
    if price is None:
        return "VERİ YOK"
    if price <= lower:
        return "AL"
    if price >= upper:
        return "SAT"
    return "BEKLE"

def calculate_position(entry: float, stop: float) -> Tuple[int, float]:
    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100.0)
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0:
        return 0, 0.0
    lot = int(risk_amount / per_share_risk)
    total_risk = lot * per_share_risk
    return lot, total_risk

def recenter_band(st: Dict[str, Any], center_price: float) -> Tuple[float, float]:
    half_band = max(BAND_SIZE_TL, 0.01)
    lower = round(center_price - half_band, 2)
    upper = round(center_price + half_band, 2)
    st["lower"] = lower
    st["upper"] = upper
    return lower, upper

def stop_distance_allowed(entry: float, stop: float) -> bool:
    distance = abs(entry - stop)
    return MIN_STOP_DISTANCE_TL <= distance <= MAX_STOP_DISTANCE_TL

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

# ================= MONITOR =================
def price_monitor_loop():
    while True:
        try:
            is_market_open = market_open()

            with _state_lock:
                snapshot = {k: v.copy() for k, v in WATCHLIST.items()}

            for symbol in snapshot.keys():
                price = fetch_last_price(symbol)
                if price is None:
                    continue

                now_ts = time.time()
                should_refresh_analysis = False

                with _state_lock:
                    st = WATCHLIST.get(symbol)
                    if not st:
                        continue

                    st.setdefault("last_alert_at", 0.0)
                    st.setdefault("last_setup_at", 0.0)
                    st.setdefault("last_analysis_at", 0.0)

                    if not st.get("initialized", False):
                        recenter_band(st, price)
                        st["alerted"] = None
                        st["initialized"] = True

                    if now_ts - float(st.get("last_analysis_at", 0.0)) >= ANALYSIS_REFRESH_SEC:
                        should_refresh_analysis = True

                    lower = float(st["lower"])
                    upper = float(st["upper"])

                    alerted = st.get("alerted")

                    if is_market_open and price <= lower and alerted != "lower":
                        now_ts = time.time()
                        stop = upper
                        new_lower, new_upper = recenter_band(st, price)
                        st["alerted"] = "lower"

                        if now_ts - float(st.get("last_alert_at", 0.0)) < ALERT_COOLDOWN_SEC:
                            continue
                        if not stop_distance_allowed(price, stop):
                            continue

                        lot, total_risk = calculate_position(price, stop)
                        send_telegram(
                            f"🟢 AL\n{symbol}\n"
                            f"Fiyat: {safe_round(price)}\n"
                            f"Stop: {safe_round(stop)}\n"
                            f"Lot: {lot}\n"
                            f"Risk: {safe_round(total_risk)}\n"
                            f"Yeni Bant: {safe_round(new_lower)} - {safe_round(new_upper)}"
                        )
                        st["last_alert_at"] = now_ts

                    elif is_market_open and price >= upper and alerted != "upper":
                        now_ts = time.time()
                        stop = lower
                        new_lower, new_upper = recenter_band(st, price)
                        st["alerted"] = "upper"

                        if now_ts - float(st.get("last_alert_at", 0.0)) < ALERT_COOLDOWN_SEC:
                            continue
                        if not stop_distance_allowed(price, stop):
                            continue

                        lot, total_risk = calculate_position(price, stop)
                        send_telegram(
                            f"🔴 SAT\n{symbol}\n"
                            f"Fiyat: {safe_round(price)}\n"
                            f"Stop: {safe_round(stop)}\n"
                            f"Lot: {lot}\n"
                            f"Risk: {safe_round(total_risk)}\n"
                            f"Yeni Bant: {safe_round(new_lower)} - {safe_round(new_upper)}"
                        )
                        st["last_alert_at"] = now_ts

                    elif is_market_open and lower < price < upper:
                        st["alerted"] = None

                if should_refresh_analysis:
                    setup = evaluate_buy_setup(symbol, price)

                    send_setup = False
                    with _state_lock:
                        st = WATCHLIST.get(symbol)
                        if not st:
                            continue

                        st["buy_setup"] = setup
                        st["last_analysis_at"] = now_ts

                        if setup is None:
                            continue

                        if setup.get("eligible") and now_ts - float(st.get("last_setup_at", 0.0)) >= BUY_SETUP_COOLDOWN_SEC:
                            st["last_setup_at"] = now_ts
                            send_setup = True

                    if send_setup:
                        reasons = setup.get("reasons", [])
                        reason_text = "\n".join([f"- {r}" for r in reasons]) if reasons else "- Sinyal kriterleri saglandi"
                        send_telegram(
                            f"📈 ALIM ADAYI\n{symbol}\n"
                            f"Skor: {setup.get('score')}/100\n"
                            f"Giris: {setup.get('entry_low')} - {setup.get('entry_high')}\n"
                            f"Stop: {setup.get('stop')}\n"
                            f"Hedef1: {setup.get('target1')}\n"
                            f"Hedef2: {setup.get('target2')}\n"
                            f"R/R: {setup.get('rr')}\n"
                            f"Nedenler:\n{reason_text}"
                        )

            time.sleep(30 if is_market_open else 60)

        except Exception:
            time.sleep(10)

def ensure_monitor_started():
    global _monitor_started
    if _monitor_started:
        return
    with _monitor_lock:
        if _monitor_started:
            return
        threading.Thread(target=price_monitor_loop, daemon=True).start()
        _monitor_started = True

@app.before_request
def start_monitor_once():
    if RUN_MONITOR_IN_WEB:
        ensure_monitor_started()

# ================= API =================
@app.route("/api/data", methods=["GET"])
def api_data():
    with _state_lock:
        snapshot = {k: v.copy() for k, v in WATCHLIST.items()}

    prices = {}
    signals = {}
    buy_setups = {}

    for s, d in snapshot.items():
        p = fetch_last_price(s)
        prices[s] = safe_round(p)
        signals[s] = generate_signal(p, float(d["lower"]), float(d["upper"]))
        buy_setups[s] = d.get("buy_setup")

    return jsonify({"prices": prices, "watchlist": snapshot, "signals": signals, "buy_setups": buy_setups})

# ================= PANEL =================
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        symbol = request.form.get("symbol")
        lower = tr_float(request.form.get("lower"))
        upper = tr_float(request.form.get("upper"))

        if symbol in WATCHLIST and lower is not None and upper is not None:
            with _state_lock:
                WATCHLIST[symbol]["lower"] = lower
                WATCHLIST[symbol]["upper"] = upper
                WATCHLIST[symbol]["alerted"] = None
                WATCHLIST[symbol]["initialized"] = True

    with _state_lock:
        snapshot = {k: v.copy() for k, v in WATCHLIST.items()}

    html = """
    <html>
    <head>
    <title>BIST Professional Alarm Panel</title>
    </head>
    <body>
    <h1>BIST Alarm Panel</h1>

    <table border="1" cellpadding="10">
    <tr>
        <th>Hisse</th>
        <th>Fiyat</th>
        <th>Alt</th>
        <th>Üst</th>
        <th>Sinyal</th>
        <th>Alim Skoru</th>
    </tr>
    {% for s in watchlist %}
    <tr>
        <td>{{s}}</td>
        <td id="price-{{s}}">-</td>
        <td id="lower-{{s}}">{{watchlist[s]["lower"]}}</td>
        <td id="upper-{{s}}">{{watchlist[s]["upper"]}}</td>
        <td id="signal-{{s}}">-</td>
        <td id="buy-score-{{s}}">-</td>
    </tr>
    {% endfor %}
    </table>

    <form method="post">
    <select name="symbol">
    {% for s in watchlist %}
        <option value="{{s}}">{{s}}</option>
    {% endfor %}
    </select>
    <input name="lower" placeholder="Alt Limit">
    <input name="upper" placeholder="Üst Limit">
    <button type="submit">Güncelle</button>
    </form>

    <script>
    async function refresh(){
        const r = await fetch("/api/data");
        const d = await r.json();
        for(const s in d.prices){
            document.getElementById("price-"+s).innerText =
                d.prices[s]===null ? "Veri Yok" : d.prices[s];
            document.getElementById("signal-"+s).innerText =
                d.signals[s];

            const setup = d.buy_setups && d.buy_setups[s] ? d.buy_setups[s] : null;
            if (setup && typeof setup.score !== "undefined") {
                const mark = setup.eligible ? "✅" : "•";
                document.getElementById("buy-score-" + s).innerText = mark + " " + setup.score;
            } else {
                document.getElementById("buy-score-" + s).innerText = "-";
            }

            if (d.watchlist && d.watchlist[s]) {
                document.getElementById("lower-" + s).innerText = d.watchlist[s].lower;
                document.getElementById("upper-" + s).innerText = d.watchlist[s].upper;
            }
        }
    }
    setInterval(refresh,15000);
    refresh();
    </script>

    </body>
    </html>
    """

    return render_template_string(html, watchlist=snapshot)

if __name__ == "__main__":
    if RUN_MONITOR_IN_WEB:
        ensure_monitor_started()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
