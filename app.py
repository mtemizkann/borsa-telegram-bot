import os
import json
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional

import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

# ---------------- CONFIG ----------------
TOKEN = os.environ.get("TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()

# Railway: kalıcı disk kullanmıyorsan bile, en azından deploy içinde kaybolmasın diye dosyayı root'ta tutuyoruz.
STATE_FILE = os.environ.get("STATE_FILE", "watchlist.json")

REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "15"))   # web panel refresh
MONITOR_SECONDS = int(os.environ.get("MONITOR_SECONDS", "30"))   # telegram monitor loop

# BIST saatleri (yaklaşık): 09:00-18:00
MARKET_OPEN_HOUR = int(os.environ.get("MARKET_OPEN_HOUR", "9"))
MARKET_CLOSE_HOUR = int(os.environ.get("MARKET_CLOSE_HOUR", "18"))

# Başlangıç watchlist (STATE_FILE yoksa bununla başlar)
DEFAULT_WATCHLIST = {
    "ASELS.IS": {"lower": 290.0, "upper": 310.0, "alerted": None},
    "TUPRS.IS": {"lower": 140.0, "upper": 170.0, "alerted": None},
    "FROTO.IS": {"lower": 850.0, "upper": 900.0, "alerted": None},
}

# Runtime state
WATCHLIST: Dict[str, Dict[str, Any]] = {}
TICKERS: Dict[str, yf.Ticker] = {}
_state_lock = threading.Lock()


# ---------------- UTILS ----------------
def parse_float_tr(value: str) -> float:
    """
    '294,25' gibi TR formatlarını '294.25' yapar.
    Boş/None gelirse ValueError verir.
    """
    if value is None:
        raise ValueError("empty")
    v = value.strip().replace(" ", "")
    # binlik ayırıcı gibi '.' kullanılmışsa kaldırıp ',' -> '.' çeviriyoruz
    # Örn: "1.234,56" -> "1234.56"
    if "," in v and "." in v:
        v = v.replace(".", "").replace(",", ".")
    else:
        v = v.replace(",", ".")
    return float(v)


def safe_round(x: Optional[float], nd: int = 2) -> Optional[float]:
    if x is None:
        return None
    try:
        return round(float(x), nd)
    except Exception:
        return None


def market_open_now() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:  # Cumartesi/Pazar
        return False
    return MARKET_OPEN_HOUR <= now.hour < MARKET_CLOSE_HOUR


def load_state() -> None:
    global WATCHLIST, TICKERS
    with _state_lock:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    WATCHLIST = json.load(f)
            except Exception:
                WATCHLIST = DEFAULT_WATCHLIST.copy()
        else:
            WATCHLIST = DEFAULT_WATCHLIST.copy()

        # normalize types
        for s, d in WATCHLIST.items():
            d["lower"] = float(d.get("lower", 0))
            d["upper"] = float(d.get("upper", 0))
            if d.get("alerted") not in ("lower", "upper", None):
                d["alerted"] = None

        # ticker cache
        TICKERS = {s: yf.Ticker(s) for s in WATCHLIST.keys()}


def save_state() -> None:
    with _state_lock:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(WATCHLIST, f, ensure_ascii=False, indent=2)
        except Exception:
            # dosya yazılamazsa (readonly vs.) uygulama yine de çalışsın
            pass


def telegram_send(message: str) -> None:
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message},
            timeout=7,
        )
    except Exception:
        pass


def fetch_last_price(symbol: str) -> Optional[float]:
    """
    yfinance bazen boş döner. En son kapanışı almaya çalışıyoruz.
    """
    try:
        hist = TICKERS[symbol].history(period="1d", interval="1m")
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


# ---------------- MONITOR (TELEGRAM ALERTS) ----------------
def price_monitor_loop() -> None:
    while True:
        try:
            if not market_open_now():
                time.sleep(60)
                continue

            with _state_lock:
                symbols = list(WATCHLIST.keys())

            for symbol in symbols:
                price = fetch_last_price(symbol)
                if price is None:
                    continue

                with _state_lock:
                    data = WATCHLIST.get(symbol)
                    if not data:
                        continue
                    lower = float(data["lower"])
                    upper = float(data["upper"])
                    alerted = data.get("alerted")

                # alert logic
                if price <= lower and alerted != "lower":
                    telegram_send(f"🟢 AL SİNYALİ\n{symbol}\nFiyat: {safe_round(price)}\nAlt Limit: {lower}")
                    with _state_lock:
                        WATCHLIST[symbol]["alerted"] = "lower"
                    save_state()

                elif price >= upper and alerted != "upper":
                    telegram_send(f"🔴 SAT SİNYALİ\n{symbol}\nFiyat: {safe_round(price)}\nÜst Limit: {upper}")
                    with _state_lock:
                        WATCHLIST[symbol]["alerted"] = "upper"
                    save_state()

                elif lower < price < upper:
                    # reset when inside band
                    if alerted is not None:
                        with _state_lock:
                            WATCHLIST[symbol]["alerted"] = None
                        save_state()

            time.sleep(MONITOR_SECONDS)
        except Exception:
            time.sleep(10)


# ---------------- API ----------------
@app.route("/api/data", methods=["GET"])
def api_data():
    with _state_lock:
        snapshot = json.loads(json.dumps(WATCHLIST))  # deep-ish copy

    prices = {}
    signals = {}

    for s, d in snapshot.items():
        p = fetch_last_price(s)
        p2 = safe_round(p, 2)
        prices[s] = p2
        signals[s] = generate_signal(p, float(d["lower"]), float(d["upper"]))

    return jsonify({"prices": prices, "watchlist": snapshot, "signals": signals})


# ---------------- WEB UI ----------------
HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>BIST Professional Panel</title>
  <style>
    body { background:#0b0f14; color:#e6edf3; font-family: Arial, sans-serif; padding:28px; }
    .wrap { max-width: 1100px; margin: 0 auto; }
    h2 { margin: 0 0 14px 0; }
    .card { background:#111823; border:1px solid #1f2a37; border-radius:16px; padding:18px; box-shadow: 0 10px 30px rgba(0,0,0,.25); }
    table { width:100%; border-collapse: collapse; overflow:hidden; border-radius:14px; }
    th, td { padding:12px; border-bottom: 1px solid #223041; text-align:center; }
    th { background:#0f1722; font-size: 13px; color:#9fb3c8; letter-spacing: .3px; }
    tr:last-child td { border-bottom: none; }
    .muted { color:#9fb3c8; font-size: 12px; }
    .rowAL { background: rgba(16,185,129,.10); }
    .rowSAT { background: rgba(239,68,68,.10); }
    .badge { display:inline-block; padding:6px 12px; border-radius:999px; font-weight:700; font-size: 12px; }
    .buy { background: rgba(16,185,129,.18); color:#34d399; border:1px solid rgba(16,185,129,.35); }
    .sell { background: rgba(239,68,68,.18); color:#f87171; border:1px solid rgba(239,68,68,.35); }
    .wait { background: rgba(148,163,184,.15); color:#cbd5e1; border:1px solid rgba(148,163,184,.25); }
    .err { background: rgba(245,158,11,.15); color:#fbbf24; border:1px solid rgba(245,158,11,.25); }
    .grid { display:grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }
    input, select { padding:10px 12px; border-radius:12px; border:1px solid #223041; background:#0f1722; color:#e6edf3; width: 100%; }
    button { padding:10px 14px; border-radius:12px; border:0; background:#1f6feb; color:white; font-weight:700; cursor:pointer; }
    button:hover { filter: brightness(1.05); }
    .row { display:grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin-top: 10px; }
    .topbar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom: 12px; }
    .pill { padding:6px 10px; border:1px solid #223041; border-radius:999px; font-size:12px; color:#9fb3c8; }
    @media (max-width: 860px) {
      .grid { grid-template-columns: 1fr; }
      .row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div>
      <h2>📊 BIST Professional Alarm Panel</h2>
      <div class="muted">Canlı fiyat, sinyal ve Telegram uyarıları. (TR virgül girişi destekli)</div>
    </div>
    <div class="pill" id="statusPill">Bağlanıyor…</div>
  </div>

  <div class="card">
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
        <tr id="row-{{s}}">
          <td><b>{{s}}</b></td>
          <td id="price-{{s}}">-</td>
          <td id="lower-{{s}}">{{d["lower"]}}</td>
          <td id="upper-{{s}}">{{d["upper"]}}</td>
          <td id="signal-{{s}}">-</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="grid">
    <div class="card">
      <h3 style="margin:0 0 10px 0;">Limit Güncelle</h3>
      <form method="post">
        <div class="row">
          <div>
            <label class="muted">Hisse</label>
            <select name="symbol">
              {% for s in watchlist.keys() %}
                <option value="{{s}}">{{s}}</option>
              {% endfor %}
            </select>
          </div>
          <div>
            <label class="muted">Alt Limit</label>
            <input name="lower" placeholder="Örn: 294,25 veya 294.25" required>
          </div>
          <div>
            <label class="muted">Üst Limit</label>
            <input name="upper" placeholder="Örn: 310" required>
          </div>
        </div>
        <div style="margin-top:12px;">
          <button type="submit">Güncelle</button>
        </div>
      </form>
      {% if error %}
        <div style="margin-top:12px;" class="badge err">{{error}}</div>
      {% endif %}
    </div>

    <div class="card">
      <h3 style="margin:0 0 10px 0;">Sistem</h3>
      <div class="muted">• Web yenileme: {{refresh}} sn</div>
      <div class="muted">• Telegram kontrol: {{monitor}} sn</div>
      <div class="muted">• Market saatleri: {{open_hour}}:00–{{close_hour}}:00 (Hafta içi)</div>
      <div class="muted" style="margin-top:10px;">
        İpucu: Limitleri güncellerken <b>294,25</b> yazabilirsin — otomatik düzeltilir.
      </div>
    </div>
  </div>

</div>

<script>
function badgeFor(signal) {
  const span = document.createElement("span");
  span.classList.add("badge");
  if (signal === "AL") { span.classList.add("buy"); span.innerText = "AL"; }
  else if (signal === "SAT") { span.classList.add("sell"); span.innerText = "SAT"; }
  else if (signal === "VERİ YOK") { span.classList.add("err"); span.innerText = "VERİ YOK"; }
  else { span.classList.add("wait"); span.innerText = "BEKLE"; }
  return span;
}

async function refresh() {
  try {
    const r = await fetch("/api/data", {cache:"no-store"});
    const d = await r.json();

    document.getElementById("statusPill").innerText = "Online";
    document.getElementById("statusPill").style.borderColor = "#223041";

    for (const s in d.watchlist) {
      const price = d.prices[s];
      const sig = d.signals[s];

      const pr = document.getElementById("price-" + s);
      pr.innerText = (price === null) ? "Veri Yok" : price;

      document.getElementById("lower-" + s).innerText = d.watchlist[s].lower;
      document.getElementById("upper-" + s).innerText = d.watchlist[s].upper;

      const row = document.getElementById("row-" + s);
      row.classList.remove("rowAL", "rowSAT");
      if (sig === "AL") row.classList.add("rowAL");
      if (sig === "SAT") row.classList.add("rowSAT");

      const cell = document.getElementById("signal-" + s);
      cell.innerHTML = "";
      cell.appendChild(badgeFor(sig));
    }
  } catch (e) {
    document.getElementById("statusPill").innerText = "Bağlantı Hatası";
    document.getElementById("statusPill").style.borderColor = "rgba(245,158,11,.35)";
  }
}

setInterval(refresh, {{refresh}} * 1000);
refresh();
</script>
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def home():
    error = ""
    if request.method == "POST":
        try:
            symbol = request.form.get("symbol", "").strip()
            lower = parse_float_tr(request.form.get("lower", ""))
            upper = parse_float_tr(request.form.get("upper", ""))

            if not symbol:
                raise ValueError("Hisse seçilmedi.")
            if lower >= upper:
                raise ValueError("Alt limit, üst limitten küçük olmalı.")
            if symbol not in WATCHLIST:
                raise ValueError("Geçersiz hisse.")

            with _state_lock:
                WATCHLIST[symbol]["lower"] = float(lower)
                WATCHLIST[symbol]["upper"] = float(upper)
                WATCHLIST[symbol]["alerted"] = None

            save_state()

        except Exception as e:
            error = f"Hata: {str(e)}"

    with _state_lock:
        snap = json.loads(json.dumps(WATCHLIST))

    return render_template_string(
        HTML,
        watchlist=snap,
        error=error,
        refresh=REFRESH_SECONDS,
        monitor=MONITOR_SECONDS,
        open_hour=MARKET_OPEN_HOUR,
        close_hour=MARKET_CLOSE_HOUR,
    )


# ---------------- BOOT ----------------
load_state()
threading.Thread(target=price_monitor_loop, daemon=True).start()

if __name__ == "__main__":
    # Railway PORT
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
