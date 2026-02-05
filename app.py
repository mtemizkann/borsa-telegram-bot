import os
import time
import threading
import requests
import yfinance as yf
import numpy as np
from flask import Flask, request, render_template_string, jsonify
from datetime import datetime

app = Flask(__name__)

TOKEN = os.environ.get("TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

ACCOUNT_SIZE = float(os.environ.get("ACCOUNT_SIZE", "150000"))   # TL
RISK_PERCENT = float(os.environ.get("RISK_PERCENT", "2"))        # %

# İzleme listesi: lower/upper senin manuel sınırların
WATCHLIST = {
    "ASELS.IS": {"lower": 290, "upper": 310, "alerted": None},
    "TUPRS.IS": {"lower": 140, "upper": 170, "alerted": None},
    "FROTO.IS": {"lower": 850, "upper": 900, "alerted": None},
}

TICKERS = {s: yf.Ticker(s) for s in WATCHLIST}


# ---------------- TIME / MARKET ----------------
def market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    # BIST kabaca 09-18; istersen burada daha detaylı seans ekleriz
    return 9 <= now.hour < 18


# ---------------- TELEGRAM ----------------
def send(message: str):
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


# ---------------- DATA ----------------
def get_hist(symbol: str, period="5d", interval="5m"):
    try:
        return TICKERS[symbol].history(period=period, interval=interval, actions=False)
    except Exception:
        return None


def get_current_prices():
    prices = {}
    for symbol in WATCHLIST:
        try:
            hist = TICKERS[symbol].history(period="1d", interval="1m", actions=False)
            if hist is not None and not hist.empty:
                prices[symbol] = round(float(hist["Close"].iloc[-1]), 2)
            else:
                prices[symbol] = None
        except Exception:
            prices[symbol] = None
    return prices


# ---------------- POSITION / RISK ----------------
def calculate_position(entry: float, stop: float):
    risk_amount = ACCOUNT_SIZE * (RISK_PERCENT / 100.0)
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0:
        return 0, 0.0
    lot = int(risk_amount / per_share_risk)
    total_risk = lot * per_share_risk
    return lot, float(total_risk)


# ---------------- TREND / RANGE DETECTION ----------------
def classify_market(symbol: str):
    """
    Basit ama iş görür sınıflandırma:
    - Son ~2-5 gün 5dk veriden:
      * slope (linear regression) + R^2
      * ATR benzeri (ortalama true range yaklaşımı)
    - slope/ATR ve R^2 yüksekse TREND, değilse RANGE
    Ayrıca trend yönü için hızlı/ yavaş MA kıyası.
    """
    hist = get_hist(symbol, period="5d", interval="5m")
    if hist is None or hist.empty or len(hist) < 60:
        return {"type": "UNKNOWN", "dir": "FLAT", "strength": 0.0}

    close = hist["Close"].astype(float).values
    high = hist["High"].astype(float).values if "High" in hist else close
    low = hist["Low"].astype(float).values if "Low" in hist else close

    n = min(len(close), 200)
    close = close[-n:]
    high = high[-n:]
    low = low[-n:]

    x = np.arange(n, dtype=float)
    y = close

    # Linear regression
    slope, intercept = np.polyfit(x, y, 1)
    y_hat = slope * x + intercept
    ss_res = np.sum((y - y_hat) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2) + 1e-9
    r2 = 1.0 - (ss_res / ss_tot)

    # ATR yaklaşımı
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(y, 1)), np.abs(low - np.roll(y, 1))))
    tr[0] = high[0] - low[0]
    atr = float(np.mean(tr[-50:])) if len(tr) >= 50 else float(np.mean(tr))

    # Normalize slope: "bar başına" hareket / atr
    slope_norm = abs(float(slope)) / (atr + 1e-9)

    # Trend yönü: EMA(20) vs EMA(50)
    def ema(arr, span):
        alpha = 2 / (span + 1)
        e = arr[0]
        for v in arr[1:]:
            e = alpha * v + (1 - alpha) * e
        return float(e)

    ema20 = ema(y[-60:], 20) if len(y) >= 60 else ema(y, 20)
    ema50 = ema(y[-120:], 50) if len(y) >= 120 else ema(y, 50)

    direction = "UP" if ema20 > ema50 else ("DOWN" if ema20 < ema50 else "FLAT")

    # Karar
    # (eşikler pratik; istersen birlikte kalibre ederiz)
    if r2 > 0.35 and slope_norm > 0.06:
        mtype = "TREND"
        strength = min(1.0, (r2 - 0.35) / 0.65 + (slope_norm - 0.06) / 0.20)
    else:
        mtype = "RANGE"
        strength = min(1.0, (0.35 - r2) / 0.35 + (0.06 - slope_norm) / 0.06)

    return {"type": mtype, "dir": direction, "strength": float(max(0.0, min(1.0, strength)))}


# ---------------- SIGNAL ENGINE ----------------
def generate_signal(price, lower, upper, market_info):
    """
    Önerilen akış:
    - RANGE: mean-reversion => lower: AL, upper: SAT
    - TREND: yönle uyumlu breakout baskın:
        * UP trend: upper üstü => AL (güçlü), lower altı => SAT/kaç (risk)
        * DOWN trend: lower altı => SAT (güçlü), upper üstü => AL/kaç
    """
    if price is None:
        return "VERİ YOK", "VERİ YOK"

    mtype = market_info["type"]
    direction = market_info["dir"]

    # Alarm metni
    alarm = "NORMAL"

    if mtype == "RANGE":
        if price <= lower:
            alarm = "ALT ALARM"
            return "AL", alarm
        elif price >= upper:
            alarm = "ÜST ALARM"
            return "SAT", alarm
        else:
            return "BEKLE", alarm

    if mtype == "TREND":
        # trendde: yönle uyumlu sinyali güçlendir, tersini baskıla
        if direction == "UP":
            if price >= upper:
                alarm = "ÜST ALARM"
                return "AL", alarm
            elif price <= lower:
                alarm = "ALT ALARM"
                return "SAT", alarm  # UP trendde alt kırılım risk -> kaçış
            else:
                return "BEKLE", alarm

        if direction == "DOWN":
            if price <= lower:
                alarm = "ALT ALARM"
                return "SAT", alarm
            elif price >= upper:
                alarm = "ÜST ALARM"
                return "AL", alarm  # DOWN trendde üst kırılım risk -> kaçış/short kapama
            else:
                return "BEKLE", alarm

        # FLAT fallback
        if price <= lower:
            alarm = "ALT ALARM"
            return "AL", alarm
        if price >= upper:
            alarm = "ÜST ALARM"
            return "SAT", alarm
        return "BEKLE", alarm

    # UNKNOWN fallback
    if price <= lower:
        return "AL", "ALT ALARM"
    if price >= upper:
        return "SAT", "ÜST ALARM"
    return "BEKLE", "NORMAL"


def compute_confidence(signal, price, lower, upper, market_info):
    """
    Basit, anlaşılır confidence:
    - Base 50
    - Market uyumu + / -
    - Sınır dışına taşma (delta) bonus
    - Trend strength bonus
    """
    if signal in ("VERİ YOK",):
        return 0

    base = 50.0
    mtype = market_info["type"]
    strength = market_info["strength"]

    # Market uyumu
    if mtype == "TREND" and signal in ("AL", "SAT"):
        base += 10
    if mtype == "RANGE" and signal in ("AL", "SAT"):
        base += 8

    # Güç bonusu
    base += 20.0 * float(max(0.0, min(1.0, strength)))

    # Taşma bonusu
    if price is not None:
        span = max(0.01, abs(upper - lower))
        if signal == "AL":
            delta = max(0.0, (lower - price) / span)  # lower altına ne kadar indi
            base += min(15.0, 60.0 * delta)
        elif signal == "SAT":
            delta = max(0.0, (price - upper) / span)  # upper üstüne ne kadar çıktı
            base += min(15.0, 60.0 * delta)
        else:
            base -= 15.0

    # Clamp
    return int(max(0, min(100, round(base))))


# ---------------- MONITOR (TELEGRAM) ----------------
def price_monitor():
    while True:
        try:
            if not market_open():
                time.sleep(60)
                continue

            prices = get_current_prices()

            for symbol, data in WATCHLIST.items():
                price = prices.get(symbol)
                if price is None:
                    continue

                lower = data["lower"]
                upper = data["upper"]

                market_info = classify_market(symbol)
                signal, alarm = generate_signal(price, lower, upper, market_info)
                conf = compute_confidence(signal, price, lower, upper, market_info)

                # Stop tanımı (basit): karşı sınır
                if signal == "AL":
                    stop = upper if market_info["type"] == "RANGE" else lower
                elif signal == "SAT":
                    stop = lower if market_info["type"] == "RANGE" else upper
                else:
                    stop = None

                if signal in ("AL", "SAT"):
                    lot, risk = calculate_position(price, stop if stop is not None else price)

                    # Alarm spam kontrolü
                    key = "lower" if alarm == "ALT ALARM" else ("upper" if alarm == "ÜST ALARM" else None)
                    if key and data["alerted"] == key:
                        continue

                    msg = (
                        f"📍 {symbol}\n"
                        f"Piyasa: {market_info['type']} ({market_info['dir']})\n"
                        f"Alarm: {alarm}\n"
                        f"Sinyal: {signal} | Confidence: %{conf}\n"
                        f"Fiyat: {price}\n"
                        f"Alt: {lower} | Üst: {upper}\n"
                        f"Lot: {lot} | Risk: {risk:.2f} TL"
                    )
                    send(msg)

                    if key:
                        data["alerted"] = key

                # Reset
                if lower < price < upper:
                    data["alerted"] = None

            time.sleep(30)

        except Exception:
            time.sleep(10)


# ---------------- API ----------------
@app.route("/api/data")
def api_data():
    prices = get_current_prices()

    rows = {}
    for symbol, data in WATCHLIST.items():
        price = prices.get(symbol)
        lower = data["lower"]
        upper = data["upper"]

        market_info = classify_market(symbol)
        signal, alarm = generate_signal(price, lower, upper, market_info)
        conf = compute_confidence(signal, price, lower, upper, market_info)

        # stop ve lot/risk
        lot = 0
        risk = 0.0
        if price is not None and signal in ("AL", "SAT"):
            if signal == "AL":
                stop = upper if market_info["type"] == "RANGE" else lower
            else:
                stop = lower if market_info["type"] == "RANGE" else upper
            lot, risk = calculate_position(price, stop)

        rows[symbol] = {
            "symbol": symbol,
            "price": price,
            "lower": lower,
            "upper": upper,
            "alarm": alarm,
            "signal": signal,
            "confidence": conf,
            "market_type": market_info["type"],
            "market_dir": market_info["dir"],
            "lot": lot,
            "risk": round(risk, 2),
        }

    return jsonify({"rows": rows, "account": ACCOUNT_SIZE, "risk_percent": RISK_PERCENT})


# ---------------- WEB PANEL ----------------
@app.route("/", methods=["GET", "POST"])
def home():
    error = ""
    if request.method == "POST":
        try:
            symbol = request.form["symbol"]
            lower = float(request.form["lower"].replace(",", "."))
            upper = float(request.form["upper"].replace(",", "."))

            WATCHLIST[symbol]["lower"] = lower
            WATCHLIST[symbol]["upper"] = upper
            WATCHLIST[symbol]["alerted"] = None
        except Exception:
            error = "Girdi hatası: Lütfen sayıyı 294.25 veya 294,25 şeklinde gir."

    html = """
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>BIST Profesyonel Trading Sistemi</title>
  <style>
    :root{
      --bg:#0b0b0b; --card:#121212; --muted:#a0a0a0; --line:#242424;
      --green:#0d3b2b; --red:#3b0d12; --blue:#0a84ff;
    }
    body{ margin:0; background:var(--bg); color:#fff; font-family:Arial, Helvetica, sans-serif; }
    .wrap{ max-width:1200px; margin:0 auto; padding:18px; }
    .title{ display:flex; align-items:center; gap:10px; font-size:20px; font-weight:700; margin:10px 0 14px; }
    .card{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; box-shadow:0 8px 30px rgba(0,0,0,.35); }
    .topbar{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:space-between; margin-bottom:12px; }
    .pill{ font-size:12px; padding:6px 10px; border:1px solid var(--line); border-radius:999px; color:var(--muted); }
    .error{ color:#ffb3b3; margin:10px 0 0; font-size:13px; }
    .table-wrap{ overflow-x:auto; border-radius:12px; border:1px solid var(--line); }
    table{ width:100%; min-width:980px; border-collapse:collapse; }
    thead th{ background:#151515; position:sticky; top:0; z-index:1; }
    th,td{ padding:12px 10px; text-align:center; border-bottom:1px solid var(--line); white-space:nowrap; }
    tbody tr{ background:#101010; }
    tbody tr.row-buy{ background:rgba(18, 160, 90, .22); }
    tbody tr.row-sell{ background:rgba(210, 50, 70, .22); }
    .badge{ display:inline-block; padding:6px 10px; border-radius:999px; font-weight:700; font-size:12px; border:1px solid rgba(255,255,255,.12); }
    .b-al{ background:rgba(18,160,90,.25); }
    .b-sat{ background:rgba(210,50,70,.25); }
    .b-bekle{ background:rgba(160,160,160,.18); color:#e7e7e7; }
    .b-unknown{ background:rgba(255,255,255,.08); color:#d8d8d8; }
    .muted{ color:var(--muted); font-size:12px; }
    form{ display:flex; flex-wrap:wrap; gap:10px; margin-top:14px; align-items:center; }
    select,input{ background:#0f0f0f; color:#fff; border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
    input{ width:160px; }
    button{ background:var(--blue); color:white; border:none; border-radius:10px; padding:10px 16px; cursor:pointer; font-weight:700; }
    button:hover{ filter:brightness(1.05); }
    .sub{ margin-top:10px; color:var(--muted); font-size:12px; line-height:1.35; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="title">📊 BIST Profesyonel Trading Sistemi</div>

    <div class="card">
      <div class="topbar">
        <div class="pill">Otomatik yenileme: 10 sn</div>
        <div class="pill">Trend/Range + Confidence aktif</div>
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Hisse</th>
              <th>Fiyat</th>
              <th>Alt</th>
              <th>Üst</th>
              <th>Alarm</th>
              <th>Sinyal</th>
              <th>Confidence %</th>
              <th>Lot</th>
              <th>Risk (TL)</th>
              <th class="muted">Piyasa</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>

      <div class="sub">
        <b>Akış:</b> Trend/Range tespit → sinyal baskılama/güçlendirme → confidence otomatik güncellenir. <br/>
        <span class="muted">Not: Limitleri (Alt/Üst) manuel giriyorsun. İstersen “auto band” (ATR/Bollinger) de ekleriz.</span>
      </div>

      <h3 style="margin:16px 0 8px;">Limit Güncelle</h3>
      <form method="post">
        <select name="symbol">
          {% for s in watchlist %}
            <option value="{{s}}">{{s}}</option>
          {% endfor %}
        </select>
        <input name="lower" placeholder="Alt Limit (örn 294,25)"/>
        <input name="upper" placeholder="Üst Limit (örn 310)"/>
        <button type="submit">Güncelle</button>
      </form>

      {% if error %}
        <div class="error">{{error}}</div>
      {% endif %}
    </div>
  </div>

<script>
  function badge(signal){
    const span = document.createElement("span");
    span.classList.add("badge");
    if(signal === "AL"){ span.classList.add("b-al"); span.innerText="AL"; }
    else if(signal === "SAT"){ span.classList.add("b-sat"); span.innerText="SAT"; }
    else if(signal === "BEKLE"){ span.classList.add("b-bekle"); span.innerText="BEKLE"; }
    else { span.classList.add("b-unknown"); span.innerText=signal; }
    return span;
  }

  function rowClass(signal){
    if(signal === "AL") return "row-buy";
    if(signal === "SAT") return "row-sell";
    return "";
  }

  async function refresh(){
    const r = await fetch("/api/data", {cache:"no-store"});
    const d = await r.json();
    const tbody = document.getElementById("tbody");
    tbody.innerHTML = "";

    const symbols = Object.keys(d.rows);
    for(const s of symbols){
      const row = d.rows[s];
      const tr = document.createElement("tr");
      tr.className = rowClass(row.signal);

      tr.innerHTML = `
        <td><b>${row.symbol}</b></td>
        <td>${row.price === null ? "Veri Yok" : row.price}</td>
        <td>${row.lower}</td>
        <td>${row.upper}</td>
        <td>${row.alarm}</td>
        <td></td>
        <td><b>%${row.confidence}</b></td>
        <td>${row.lot}</td>
        <td>${row.risk}</td>
        <td class="muted">${row.market_type}${row.market_dir ? " ("+row.market_dir+")" : ""}</td>
      `;

      tr.children[5].appendChild(badge(row.signal));
      tbody.appendChild(tr);
    }
  }

  setInterval(refresh, 10000);
  refresh();
</script>

</body>
</html>
    """
    return render_template_string(html, watchlist=WATCHLIST, error=error)


# ---------------- START ----------------
# Gunicorn çok worker açarsa her worker thread başlatır.
# Railway'de WEB_CONCURRENCY=1 set ederek tek worker kullanmanı öneririm.
if os.environ.get("RUN_MONITOR", "1") == "1":
    threading.Thread(target=price_monitor, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
