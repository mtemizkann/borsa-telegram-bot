# app.py
# -*- coding: utf-8 -*-
"""
Smart Alert Engine (BIST) - Flask + TwelveData + Telegram

Özellikler:
- Trend filtresi (Close>EMA50 ve EMA50>EMA200)
- RSI(14) filtresi
- Destek bölgesi kontrolü (son 20 gün min low, fiyata %3 yakınlık)
- Risk/Reward (min 1:2), stop = support*0.98, hedef = price + 2*risk
- 2 kademeli limit önerisi (piyasanın altında)
- Web panel (/)
- Telegram uyarısı (sinyal AL olunca ve değişince)

Kurulum:
pip install flask requests pandas

Çalıştır:
export TWELVEDATA_API_KEY="..."
export TELEGRAM_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
export CHECK_INTERVAL_SEC="180"   # 3 dk
export WEBHOOK_SECRET="..."       # opsiyonel
python app.py
"""

import os
import time
import threading
from math import floor
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

import requests
import pandas as pd
from flask import Flask, jsonify, request, render_template_string

# -------------------------
# Ayarlar (ENV)
# -------------------------
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
CHECK_INTERVAL_SEC = int(os.environ.get("CHECK_INTERVAL_SEC", "180"))  # default 3 dk
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()

# İzlenecek hisseler (TwelveData sembol formatı değişebilir)
# TwelveData için BIST sembolleri bazen farklı çalışır.
# Eğer sembol bulunamazsa, SYMBOL_MAP ile kendi hesabına göre düzeltirsin.
WATCHLIST = ["FROTO", "TUPRS", "ASELS", "MGROS"]

# Eğer TwelveData sembol isimleri farklıysa burada eşle
# Örn: {"ASELS": "ASELS.IS"} gibi (TwelveData'da ne geçerliyse)
SYMBOL_MAP: Dict[str, str] = {
    "FROTO": "FROTO",
    "TUPRS": "TUPRS",
    "ASELS": "ASELS",
    "MGROS": "MGROS",
}

# Bütçe/lot hesaplamak istersen (opsiyonel)
BUDGETS_TRY: Dict[str, int] = {
    "FROTO": 50000,
    "TUPRS": 50000,
    "ASELS": 50000,
    "MGROS": 25000,
}

# Eski "alt/üst" mantığını da korumak istersen:
# (Boş bırakırsan sadece Smart Engine çalışır)
USER_LEVELS: Dict[str, Dict[str, Optional[float]]] = {
    # "FROTO": {"low": 114.00, "high": 118.50},
    # "TUPRS": {"low": 226.50, "high": 235.00},
    # "ASELS": {"low": 292.00, "high": 305.00},
    # "MGROS": {"low": 640.00, "high": 680.00},
}

# -------------------------
# Teknik indikatörler
# -------------------------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss.replace(0, pd.NA))
    out = 100 - (100 / (1 + rs))
    return out

# -------------------------
# TwelveData - OHLCV çek
# -------------------------
def fetch_ohlc_twelvedata(symbol: str, interval: str = "1day", outputsize: int = 260) -> pd.DataFrame:
    """
    TwelveData Time Series endpoint.
    Returns DataFrame with columns: open, high, low, close, volume
    """
    if not TWELVEDATA_API_KEY:
        raise RuntimeError("TWELVEDATA_API_KEY boş. ENV'e ekleyin.")

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": str(outputsize),
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    if "status" in data and data["status"] == "error":
        raise RuntimeError(f"TwelveData error for {symbol}: {data.get('message')}")

    values = data.get("values") or []
    if not values:
        raise RuntimeError(f"TwelveData empty values for {symbol}. Symbol map'i kontrol et.")

    # values genelde en yeni -> en eski gelir; ters çevir
    values = list(reversed(values))
    df = pd.DataFrame(values)

    # numeric parse
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # datetime index
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime")

    df = df.dropna(subset=["close", "low"])
    return df

# -------------------------
# Telegram
# -------------------------
def send_telegram(text: str) -> None:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    rr = requests.post(url, json=payload, timeout=15)
    rr.raise_for_status()

# -------------------------
# Smart Engine
# -------------------------
@dataclass
class SignalResult:
    symbol: str
    mapped_symbol: str
    price: float
    ema50: float
    ema200: float
    rsi14: float
    support: float
    stop: float
    target: float
    rr: float
    trend_ok: bool
    rsi_ok: bool
    support_ok: bool
    rr_ok: bool
    signal: str  # "AL" / "BEKLE"
    k1: float
    k2: float
    note: str

def analyze_symbol(df: pd.DataFrame, symbol: str, mapped_symbol: str) -> SignalResult:
    close = df["close"]
    low = df["low"]

    # yeterli data kontrol
    if len(df) < 220:
        raise RuntimeError(f"{symbol}: Yetersiz veri ({len(df)}). outputsize artır veya interval değiştir.")

    price = float(close.iloc[-1])

    ema50_v = float(ema(close, 50).iloc[-1])
    ema200_v = float(ema(close, 200).iloc[-1])
    rsi14_v = float(rsi(close, 14).iloc[-1])

    # destek = son 20 gün min low
    support_v = float(low.tail(20).min())
    dist_to_support = (price - support_v) / price if price > 0 else 999

    # filtreler
    trend_ok = (price > ema50_v) and (ema50_v > ema200_v)

    # RSI filtresi:
    # - normal alım: 40-55
    # - agresif: <30
    rsi_aggr = (rsi14_v < 30)
    rsi_norm = (40 <= rsi14_v <= 55)
    rsi_ok = bool(rsi_aggr or rsi_norm)

    # destek yakınlığı: %3 içinde
    support_ok = bool(dist_to_support <= 0.03)

    # risk/ödül
    stop_v = float(support_v * 0.98)
    risk = price - stop_v
    # hedef: 1:2
    target_v = float(price + 2 * risk) if risk > 0 else float(price)
    rr_v = float((target_v - price) / risk) if risk > 0 else 0.0
    rr_ok = bool(rr_v >= 2.0)

    signal = "AL" if (trend_ok and rsi_ok and support_ok and rr_ok) else "BEKLE"

    # Kademeli limit önerisi (piyasanın altında)
    # trend güçlü değilse kademeleri biraz daha aşağı iteriz
    if trend_ok:
        k1 = round(price * 0.995, 2)  # -0.5%
        k2 = round(price * 0.988, 2)  # -1.2%
    else:
        k1 = round(price * 0.990, 2)  # -1.0%
        k2 = round(price * 0.975, 2)  # -2.5%

    note_parts = []
    if rsi_aggr:
        note_parts.append("RSI<30 (agresif)")
    if not trend_ok:
        note_parts.append("trend zayıf")
    if not support_ok:
        note_parts.append("destekten uzak")
    if not rr_ok:
        note_parts.append("RR<2")

    note = ", ".join(note_parts) if note_parts else "OK"

    return SignalResult(
        symbol=symbol,
        mapped_symbol=mapped_symbol,
        price=price,
        ema50=ema50_v,
        ema200=ema200_v,
        rsi14=rsi14_v,
        support=support_v,
        stop=stop_v,
        target=target_v,
        rr=rr_v,
        trend_ok=trend_ok,
        rsi_ok=rsi_ok,
        support_ok=support_ok,
        rr_ok=rr_ok,
        signal=signal,
        k1=k1,
        k2=k2,
        note=note,
    )

# -------------------------
# Global State
# -------------------------
STATE_LOCK = threading.Lock()
LATEST: Dict[str, SignalResult] = {}
LAST_SENT_SIGNAL: Dict[str, str] = {}  # symbol -> "AL" / "BEKLE"
LAST_SENT_TS: Dict[str, float] = {}    # symbol -> epoch

def compute_lot_suggestions(symbol: str, k1: float, k2: float) -> Optional[Tuple[int, int, int]]:
    """
    2 kademede (50/50) kaç lot alınır?
    """
    budget = BUDGETS_TRY.get(symbol)
    if not budget:
        return None
    b1 = budget * 0.5
    b2 = budget * 0.5
    lot1 = floor(b1 / k1) if k1 > 0 else 0
    lot2 = floor(b2 / k2) if k2 > 0 else 0
    used = lot1 * k1 + lot2 * k2
    return lot1, lot2, int(used)

def maybe_notify(res: SignalResult) -> None:
    """
    AL sinyali oluşunca (veya sinyal değişince) Telegram'a gönder.
    Spam engeli: aynı sinyali 30 dk içinde tekrar atmaz.
    """
    now = time.time()
    sym = res.symbol

    prev = LAST_SENT_SIGNAL.get(sym)
    prev_ts = LAST_SENT_TS.get(sym, 0)

    should_send = False
    if prev is None:
        should_send = True
    elif res.signal != prev:
        should_send = True
    else:
        # aynı sinyalse 30 dk sınırı
        if now - prev_ts > 1800:
            should_send = True

    if not should_send:
        return

    # User levels varsa basit alt/üst durumu da göster
    lvl = USER_LEVELS.get(sym)
    lvl_line = ""
    if lvl:
        low = lvl.get("low")
        high = lvl.get("high")
        if low is not None and high is not None:
            lvl_line = f"\nLimit bandın: {low:.2f} - {high:.2f}"

    lots = compute_lot_suggestions(sym, res.k1, res.k2)
    lot_line = ""
    if lots:
        lot1, lot2, used = lots
        lot_line = f"\nÖneri lot (50/50): {res.k1:.2f}→{lot1} lot | {res.k2:.2f}→{lot2} lot (≈{used:,} TL)"

    msg = (
        f"📌 {sym} SİNYAL: {res.signal}\n"
        f"Fiyat: {res.price:.2f}\n"
        f"Trend: {'OK' if res.trend_ok else 'NO'} | RSI14: {res.rsi14:.1f} ({'OK' if res.rsi_ok else 'NO'})\n"
        f"Destek: {res.support:.2f} ({'OK' if res.support_ok else 'NO'})\n"
        f"Stop: {res.stop:.2f} | Hedef: {res.target:.2f} | RR: {res.rr:.2f} ({'OK' if res.rr_ok else 'NO'})\n"
        f"Önerilen Limit Kademeleri: {res.k1:.2f} / {res.k2:.2f}\n"
        f"Not: {res.note}"
        f"{lvl_line}"
        f"{lot_line}"
    )

    send_telegram(msg)
    LAST_SENT_SIGNAL[sym] = res.signal
    LAST_SENT_TS[sym] = now

def refresh_once() -> None:
    """
    WATCHLIST'i dolaş, analizi güncelle.
    """
    for sym in WATCHLIST:
        mapped = SYMBOL_MAP.get(sym, sym)
        try:
            df = fetch_ohlc_twelvedata(mapped, interval="1day", outputsize=260)
            res = analyze_symbol(df, sym, mapped)
            with STATE_LOCK:
                LATEST[sym] = res
            maybe_notify(res)
        except Exception as e:
            # hata durumunu da state'e yaz
            err_res = SignalResult(
                symbol=sym,
                mapped_symbol=mapped,
                price=float("nan"),
                ema50=float("nan"),
                ema200=float("nan"),
                rsi14=float("nan"),
                support=float("nan"),
                stop=float("nan"),
                target=float("nan"),
                rr=float("nan"),
                trend_ok=False,
                rsi_ok=False,
                support_ok=False,
                rr_ok=False,
                signal="HATA",
                k1=float("nan"),
                k2=float("nan"),
                note=str(e),
            )
            with STATE_LOCK:
                LATEST[sym] = err_res

def loop_worker() -> None:
    """
    Periyodik çalıştırma thread'i
    """
    while True:
        refresh_once()
        time.sleep(CHECK_INTERVAL_SEC)

# -------------------------
# Flask UI
# -------------------------
app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Smart Signal Panel</title>
  <style>
    body { font-family: -apple-system, Arial, sans-serif; margin: 24px; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 10px; font-size: 14px; }
    th { background: #f6f6f6; text-align: left; }
    .AL { background: #eaffea; }
    .BEKLE { background: #fffbe6; }
    .HATA { background: #ffecec; }
    code { background:#f2f2f2; padding:2px 4px; border-radius:4px; }
  </style>
</head>
<body>
  <h2>Smart Signal Panel</h2>
  <p>Interval: <b>1D</b> | Check: <b>{{interval}}s</b> | Watchlist: <b>{{watchlist}}</b></p>

  <table>
    <thead>
      <tr>
        <th>Sembol</th>
        <th>Sinyal</th>
        <th>Fiyat</th>
        <th>EMA50 / EMA200</th>
        <th>RSI14</th>
        <th>Destek</th>
        <th>Stop</th>
        <th>Hedef</th>
        <th>RR</th>
        <th>Öneri K1/K2</th>
        <th>Not</th>
      </tr>
    </thead>
    <tbody>
      {% for r in rows %}
      <tr class="{{r.signal}}">
        <td><b>{{r.symbol}}</b><br><small>{{r.mapped_symbol}}</small></td>
        <td><b>{{r.signal}}</b><br>
          <small>
            Trend: {{ "OK" if r.trend_ok else "NO" }} |
            RSI: {{ "OK" if r.rsi_ok else "NO" }} |
            Destek: {{ "OK" if r.support_ok else "NO" }} |
            RR: {{ "OK" if r.rr_ok else "NO" }}
          </small>
        </td>
        <td>{{"%.2f"|format(r.price) if r.price==r.price else "-"}}</td>
        <td>{{"%.2f"|format(r.ema50) if r.ema50==r.ema50 else "-"}} / {{"%.2f"|format(r.ema200) if r.ema200==r.ema200 else "-"}}</td>
        <td>{{"%.1f"|format(r.rsi14) if r.rsi14==r.rsi14 else "-"}}</td>
        <td>{{"%.2f"|format(r.support) if r.support==r.support else "-"}}</td>
        <td>{{"%.2f"|format(r.stop) if r.stop==r.stop else "-"}}</td>
        <td>{{"%.2f"|format(r.target) if r.target==r.target else "-"}}</td>
        <td>{{"%.2f"|format(r.rr) if r.rr==r.rr else "-"}}</td>
        <td>{{"%.2f"|format(r.k1) if r.k1==r.k1 else "-"}} / {{"%.2f"|format(r.k2) if r.k2==r.k2 else "-"}}</td>
        <td><small>{{r.note}}</small></td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <p style="margin-top:16px;">
    JSON endpoint: <code>/api/state</code> |
    Manuel refresh: <code>/api/refresh?key=WEBHOOK_SECRET</code>
  </p>
</body>
</html>
"""

@app.get("/")
def home():
    with STATE_LOCK:
        rows = [asdict(LATEST.get(sym)) if sym in LATEST else None for sym in WATCHLIST]
    rows = [r for r in rows if r is not None]
    # dataclass dict -> object like
    class Obj: pass
    out = []
    for r in rows:
        o = Obj()
        for k,v in r.items():
            setattr(o,k,v)
        out.append(o)

    return render_template_string(
        HTML,
        rows=out,
        interval=CHECK_INTERVAL_SEC,
        watchlist=", ".join(WATCHLIST)
    )

@app.get("/api/state")
def api_state():
    with STATE_LOCK:
        payload = {k: asdict(v) for k, v in LATEST.items()}
    return jsonify(payload)

@app.get("/api/refresh")
def api_refresh():
    # opsiyonel güvenlik
    if WEBHOOK_SECRET:
        if request.args.get("key") != WEBHOOK_SECRET:
            return jsonify({"error": "unauthorized"}), 401
    refresh_once()
    return jsonify({"ok": True})

# -------------------------
# Main
# -------------------------
def start_background():
    t = threading.Thread(target=loop_worker, daemon=True)
    t.start()

if __name__ == "__main__":
    # İlk state oluştur
    try:
        refresh_once()
    except Exception:
        pass

    start_background()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
