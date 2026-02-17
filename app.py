import os
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

import requests
import yfinance as yf
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

# ================= ENV =================
TOKEN = os.environ.get("TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "").strip()
RUN_MONITOR_IN_WEB = os.environ.get("RUN_MONITOR_IN_WEB", "false").strip().lower() == "true"
STRATEGY_PRESET = os.environ.get("STRATEGY_PRESET", "DENGELI").strip().upper()

PRESET_CONFIGS: Dict[str, Dict[str, float]] = {
    "AGRESIF": {
        "AL_THRESHOLD": 64,
        "SAT_THRESHOLD": 42,
        "TECH_WEIGHT": 0.55,
        "FUND_WEIGHT": 0.20,
        "NEWS_WEIGHT": 0.15,
        "REGIME_WEIGHT": 0.10,
        "DECISION_ALERT_COOLDOWN_SEC": 1800,
    },
    "DENGELI": {
        "AL_THRESHOLD": 72,
        "SAT_THRESHOLD": 38,
        "TECH_WEIGHT": 0.45,
        "FUND_WEIGHT": 0.25,
        "NEWS_WEIGHT": 0.20,
        "REGIME_WEIGHT": 0.10,
        "DECISION_ALERT_COOLDOWN_SEC": 3600,
    },
    "KORUMACI": {
        "AL_THRESHOLD": 78,
        "SAT_THRESHOLD": 35,
        "TECH_WEIGHT": 0.35,
        "FUND_WEIGHT": 0.35,
        "NEWS_WEIGHT": 0.20,
        "REGIME_WEIGHT": 0.10,
        "DECISION_ALERT_COOLDOWN_SEC": 5400,
    },
}

if STRATEGY_PRESET not in PRESET_CONFIGS:
    STRATEGY_PRESET = "DENGELI"

_preset = PRESET_CONFIGS[STRATEGY_PRESET]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)).replace(",", "."))
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default)).replace(",", ".")))
    except Exception:
        return int(default)

ACCOUNT_SIZE = _env_float("ACCOUNT_SIZE", 150000)
RISK_PERCENT = _env_float("RISK_PERCENT", 2)
BAND_SIZE_TL = _env_float("BAND_SIZE_TL", 1)
MIN_STOP_DISTANCE_TL = _env_float("MIN_STOP_DISTANCE_TL", 0.5)
MAX_STOP_DISTANCE_TL = _env_float("MAX_STOP_DISTANCE_TL", 20)
ALERT_COOLDOWN_SEC = _env_int("ALERT_COOLDOWN_SEC", 180)

ANALYSIS_REFRESH_SEC = _env_int("ANALYSIS_REFRESH_SEC", 300)
DECISION_ALERT_COOLDOWN_SEC = _env_int("DECISION_ALERT_COOLDOWN_SEC", int(_preset["DECISION_ALERT_COOLDOWN_SEC"]))
NEWS_LOOKBACK_HOURS = _env_int("NEWS_LOOKBACK_HOURS", 72)

AL_THRESHOLD = _env_int("AL_THRESHOLD", int(_preset["AL_THRESHOLD"]))
SAT_THRESHOLD = _env_int("SAT_THRESHOLD", int(_preset["SAT_THRESHOLD"]))

TECH_WEIGHT = _env_float("TECH_WEIGHT", _preset["TECH_WEIGHT"])
FUND_WEIGHT = _env_float("FUND_WEIGHT", _preset["FUND_WEIGHT"])
NEWS_WEIGHT = _env_float("NEWS_WEIGHT", _preset["NEWS_WEIGHT"])
REGIME_WEIGHT = _env_float("REGIME_WEIGHT", _preset["REGIME_WEIGHT"])
BACKTEST_INITIAL_CAPITAL = _env_float("BACKTEST_INITIAL_CAPITAL", 100000)
DECISION_LOG_LIMIT = _env_int("DECISION_LOG_LIMIT", 200)
DAILY_RISK_CAP_PERCENT = _env_float("DAILY_RISK_CAP_PERCENT", 6.0)
MAX_ACTIVE_POSITIONS = _env_int("MAX_ACTIVE_POSITIONS", 2)
MAX_POSITIONS_PER_SECTOR = _env_int("MAX_POSITIONS_PER_SECTOR", 1)
PARTIAL_TP1_RATIO = _env_float("PARTIAL_TP1_RATIO", 0.5)
TRAILING_STOP_PCT = _env_float("TRAILING_STOP_PCT", 1.2)
AUTO_PRESET_BY_REGIME = os.environ.get("AUTO_PRESET_BY_REGIME", "true").strip().lower() == "true"
DAILY_REPORT_HOUR = _env_int("DAILY_REPORT_HOUR", 18)
ALLOW_DECISION_ALERTS_OUTSIDE_MARKET = os.environ.get("ALLOW_DECISION_ALERTS_OUTSIDE_MARKET", "false").strip().lower() == "true"
STRICT_MARKET_HOURS = os.environ.get("STRICT_MARKET_HOURS", "true").strip().lower() == "true"

EFFECTIVE_STRATEGY = {
    "preset": STRATEGY_PRESET,
    "al_threshold": AL_THRESHOLD,
    "sat_threshold": SAT_THRESHOLD,
    "weights": {
        "technical": TECH_WEIGHT,
        "fundamental": FUND_WEIGHT,
        "news": NEWS_WEIGHT,
        "regime": REGIME_WEIGHT,
    },
    "decision_alert_cooldown_sec": DECISION_ALERT_COOLDOWN_SEC,
    "risk_controls": {
        "daily_risk_cap_percent": DAILY_RISK_CAP_PERCENT,
        "max_active_positions": MAX_ACTIVE_POSITIONS,
        "max_positions_per_sector": MAX_POSITIONS_PER_SECTOR,
    },
    "exit_management": {
        "partial_tp1_ratio": PARTIAL_TP1_RATIO,
        "trailing_stop_pct": TRAILING_STOP_PCT,
    },
    "auto_preset_by_regime": AUTO_PRESET_BY_REGIME,
    "allow_decision_alerts_outside_market": ALLOW_DECISION_ALERTS_OUTSIDE_MARKET,
    "strict_market_hours": STRICT_MARKET_HOURS,
}

# ================= STATE =================
WATCHLIST: Dict[str, Dict[str, Any]] = {
    "ASELS.IS": {
        "lower": 290.0,
        "upper": 310.0,
        "alerted": None,
        "initialized": False,
        "last_alert_at": 0.0,
        "last_analysis_at": 0.0,
        "last_decision_alert_at": 0.0,
        "decision": None,
        "decision_log": [],
    },
    "TUPRS.IS": {
        "lower": 140.0,
        "upper": 170.0,
        "alerted": None,
        "initialized": False,
        "last_alert_at": 0.0,
        "last_analysis_at": 0.0,
        "last_decision_alert_at": 0.0,
        "decision": None,
        "decision_log": [],
    },
    "FROTO.IS": {
        "lower": 850.0,
        "upper": 900.0,
        "alerted": None,
        "initialized": False,
        "last_alert_at": 0.0,
        "last_analysis_at": 0.0,
        "last_decision_alert_at": 0.0,
        "decision": None,
        "decision_log": [],
    },
}

_TICKERS: Dict[str, yf.Ticker] = {}
_state_lock = threading.Lock()
_monitor_started = False
_monitor_lock = threading.Lock()
_regime_cache: Dict[str, Any] = {"score": 55.0, "reason": "Nötr", "updated_at": 0.0}
_symbol_sector_cache: Dict[str, str] = {}
_risk_state: Dict[str, Any] = {
    "date": "",
    "daily_used_risk": 0.0,
    "open_positions": {},
}
_performance_state: Dict[str, Any] = {
    "date": "",
    "daily_realized_pnl": 0.0,
    "closed_trades": 0,
    "partial_exits": 0,
    "wins": 0,
    "losses": 0,
    "decision_counts": {"AL": 0, "BEKLE": 0, "SAT": 0},
    "reports_sent": False,
    "recent_events": [],
}


# ================= HELPERS =================
def safe_round(x: Any, ndigits: int = 2) -> Optional[float]:
    try:
        if x is None:
            return None
        return round(float(x), ndigits)
    except Exception:
        return None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_weights() -> Tuple[float, float, float, float]:
    weights = [max(0.0, TECH_WEIGHT), max(0.0, FUND_WEIGHT), max(0.0, NEWS_WEIGHT), max(0.0, REGIME_WEIGHT)]
    total = sum(weights)
    if total <= 0:
        return 0.45, 0.25, 0.20, 0.10
    return (
        weights[0] / total,
        weights[1] / total,
        weights[2] / total,
        weights[3] / total,
    )


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
        hist = get_ticker(symbol).history(period="1d", interval="1m", actions=False, timeout=5)
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


def fetch_daily_history(symbol: str):
    try:
        hist = get_ticker(symbol).history(period="2y", interval="1d", actions=False, timeout=8)
        if hist is None or hist.empty:
            return None
        return hist.dropna(subset=["Close", "High", "Low"])
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


def stop_distance_allowed(entry: float, stop: float) -> bool:
    distance = abs(entry - stop)
    return MIN_STOP_DISTANCE_TL <= distance <= MAX_STOP_DISTANCE_TL


def calculate_position(entry: float, stop: float) -> Tuple[int, float]:
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


def recenter_band(st: Dict[str, Any], center_price: float) -> Tuple[float, float]:
    half_band = max(BAND_SIZE_TL, 0.01)
    lower = round(center_price - half_band, 2)
    upper = round(center_price + half_band, 2)
    st["lower"] = lower
    st["upper"] = upper
    return lower, upper


def append_decision_log(st: Dict[str, Any], symbol: str, decision: Dict[str, Any], price: float, ts: float) -> None:
    logs = st.setdefault("decision_log", [])
    logs.append(
        {
            "ts": ts,
            "time": datetime.fromtimestamp(ts, ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "price": safe_round(price),
            "action": decision.get("action"),
            "score": decision.get("score"),
            "entry_low": decision.get("entry_low"),
            "entry_high": decision.get("entry_high"),
            "stop": decision.get("stop"),
            "target1": decision.get("target1"),
            "target2": decision.get("target2"),
            "factors": decision.get("factors"),
            "reasons": decision.get("reasons"),
        }
    )
    if len(logs) > DECISION_LOG_LIMIT:
        del logs[: len(logs) - DECISION_LOG_LIMIT]


def _today_istanbul_date() -> str:
    return datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d")


def _ensure_risk_day_locked() -> None:
    today = _today_istanbul_date()
    if _risk_state.get("date") != today:
        _risk_state["date"] = today
        _risk_state["daily_used_risk"] = 0.0
        _risk_state["open_positions"] = {}

    if _performance_state.get("date") != today:
        _performance_state["date"] = today
        _performance_state["daily_realized_pnl"] = 0.0
        _performance_state["closed_trades"] = 0
        _performance_state["partial_exits"] = 0
        _performance_state["wins"] = 0
        _performance_state["losses"] = 0
        _performance_state["decision_counts"] = {"AL": 0, "BEKLE": 0, "SAT": 0}
        _performance_state["reports_sent"] = False
        _performance_state["recent_events"] = []


def _get_symbol_sector(symbol: str) -> str:
    cached = _symbol_sector_cache.get(symbol)
    if cached:
        return cached

    sector = "UNKNOWN"
    try:
        info = get_ticker(symbol).info or {}
        sector = (info.get("sector") or info.get("industry") or "UNKNOWN").strip().upper()
    except Exception:
        sector = "UNKNOWN"

    if not sector:
        sector = "UNKNOWN"
    _symbol_sector_cache[symbol] = sector
    return sector


def _sector_position_count_locked(sector: str) -> int:
    count = 0
    for pos in _risk_state.get("open_positions", {}).values():
        if pos.get("sector") == sector:
            count += 1
    return count


def apply_risk_controls_locked(symbol: str, decision: Dict[str, Any], now_ts: float) -> Dict[str, Any]:
    _ensure_risk_day_locked()

    risk_budget = ACCOUNT_SIZE * (DAILY_RISK_CAP_PERCENT / 100.0)
    open_positions = _risk_state.get("open_positions", {})
    sector = _get_symbol_sector(symbol)

    risk_meta = {
        "sector": sector,
        "risk_budget": safe_round(risk_budget),
        "daily_used_risk": safe_round(_risk_state.get("daily_used_risk", 0.0)),
        "active_positions": len(open_positions),
        "sector_positions": _sector_position_count_locked(sector),
        "allow_new_position": True,
        "block_reason": None,
    }

    action = decision.get("action")

    if action == "SAT" and symbol in open_positions:
        close_event = close_open_position_locked(
            symbol,
            float(decision.get("entry_low") or decision.get("entry_high") or 0.0),
            "SAT_DECISION",
            now_ts,
        )
        if close_event:
            _register_position_event_locked(close_event)
        risk_meta["active_positions"] = len(open_positions)

    if action == "AL":
        if symbol in open_positions:
            risk_meta["allow_new_position"] = False
            risk_meta["block_reason"] = "Sembolde zaten acik pozisyon var"
        elif len(open_positions) >= MAX_ACTIVE_POSITIONS:
            risk_meta["allow_new_position"] = False
            risk_meta["block_reason"] = "Maksimum acik pozisyon limitine ulasildi"
        elif _sector_position_count_locked(sector) >= MAX_POSITIONS_PER_SECTOR:
            risk_meta["allow_new_position"] = False
            risk_meta["block_reason"] = "Sektor bazli pozisyon limiti asildi"
        else:
            requested_risk = float(decision.get("risk") or 0.0)
            used = float(_risk_state.get("daily_used_risk", 0.0))
            if requested_risk <= 0:
                risk_meta["allow_new_position"] = False
                risk_meta["block_reason"] = "Risk degeri hesaplanamadi"
            elif used + requested_risk > risk_budget:
                risk_meta["allow_new_position"] = False
                risk_meta["block_reason"] = "Gunluk risk limiti asiliyor"

        if risk_meta["allow_new_position"]:
            requested_risk = float(decision.get("risk") or 0.0)
            entry_price = float(decision.get("entry_high") or decision.get("entry_low") or 0.0)
            lot_total = int(decision.get("lot") or 0)
            open_positions[symbol] = {
                "opened_at": now_ts,
                "sector": sector,
                "risk": requested_risk,
                "entry_price": safe_round(entry_price),
                "entry_low": decision.get("entry_low"),
                "entry_high": decision.get("entry_high"),
                "stop": decision.get("stop"),
                "initial_stop": decision.get("stop"),
                "trailing_stop": decision.get("stop"),
                "target1": decision.get("target1"),
                "target2": decision.get("target2"),
                "lot_total": lot_total,
                "lot_open": lot_total,
                "tp1_done": False,
                "realized_pnl": 0.0,
                "last_update": now_ts,
            }
            _risk_state["daily_used_risk"] = float(_risk_state.get("daily_used_risk", 0.0)) + requested_risk
        else:
            decision["action"] = "BEKLE"
            decision["lot"] = None
            decision["risk"] = None
            decision["target1"] = None
            decision["target2"] = None
            decision["rr"] = None
            reasons = list(decision.get("reasons", []))
            reasons.insert(0, f"Risk filtresi: {risk_meta['block_reason']}")
            decision["reasons"] = reasons[:5]

    risk_meta["daily_used_risk"] = safe_round(_risk_state.get("daily_used_risk", 0.0))
    risk_meta["active_positions"] = len(open_positions)
    risk_meta["sector_positions"] = _sector_position_count_locked(sector)
    decision["risk_controls"] = risk_meta
    return decision


def close_open_position_locked(symbol: str, exit_price: float, reason: str, now_ts: float) -> Optional[Dict[str, Any]]:
    open_positions = _risk_state.get("open_positions", {})
    pos = open_positions.get(symbol)
    if not pos:
        return None

    entry_price = float(pos.get("entry_price") or 0.0)
    lot_open = int(pos.get("lot_open") or 0)
    if entry_price <= 0 or lot_open <= 0:
        open_positions.pop(symbol, None)
        return None

    pnl = (exit_price - entry_price) * lot_open
    pos["realized_pnl"] = float(pos.get("realized_pnl", 0.0)) + pnl
    event = {
        "type": "close",
        "symbol": symbol,
        "reason": reason,
        "price": safe_round(exit_price),
        "qty": lot_open,
        "pnl": safe_round(pnl),
        "realized_pnl": safe_round(pos.get("realized_pnl", 0.0)),
        "ts": now_ts,
    }
    open_positions.pop(symbol, None)
    return event


def manage_open_position_locked(symbol: str, price: float, now_ts: float) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    open_positions = _risk_state.get("open_positions", {})
    pos = open_positions.get(symbol)
    if not pos:
        return events

    entry_price = float(pos.get("entry_price") or 0.0)
    lot_open = int(pos.get("lot_open") or 0)
    if entry_price <= 0 or lot_open <= 0:
        open_positions.pop(symbol, None)
        return events

    trailing_stop = float(pos.get("trailing_stop") or pos.get("stop") or 0.0)
    if trailing_stop > 0 and price <= trailing_stop:
        closed = close_open_position_locked(symbol, trailing_stop, "TRAILING_STOP", now_ts)
        if closed:
            events.append(closed)
        return events

    target1 = float(pos.get("target1") or 0.0)
    target2 = float(pos.get("target2") or 0.0)
    tp1_done = bool(pos.get("tp1_done"))

    if (not tp1_done) and target1 > 0 and price >= target1:
        lot_total = int(pos.get("lot_total") or lot_open)
        close_qty = max(1, int(lot_total * clamp(PARTIAL_TP1_RATIO, 0.1, 0.9)))
        close_qty = min(close_qty, lot_open)
        pnl = (price - entry_price) * close_qty
        pos["lot_open"] = lot_open - close_qty
        pos["tp1_done"] = True
        pos["realized_pnl"] = float(pos.get("realized_pnl", 0.0)) + pnl
        break_even = max(entry_price, trailing_stop)
        pos["trailing_stop"] = safe_round(break_even)
        pos["last_update"] = now_ts
        events.append(
            {
                "type": "partial_tp1",
                "symbol": symbol,
                "reason": "TP1",
                "price": safe_round(price),
                "qty": close_qty,
                "remaining": pos["lot_open"],
                "new_trailing_stop": pos.get("trailing_stop"),
                "pnl": safe_round(pnl),
                "realized_pnl": safe_round(pos.get("realized_pnl", 0.0)),
                "ts": now_ts,
            }
        )

    if symbol not in open_positions:
        return events
    pos = open_positions.get(symbol)
    lot_open = int(pos.get("lot_open") or 0)
    if lot_open <= 0:
        open_positions.pop(symbol, None)
        return events

    if bool(pos.get("tp1_done")):
        trailing_candidate = price * (1.0 - (clamp(TRAILING_STOP_PCT, 0.2, 10.0) / 100.0))
        current_trailing = float(pos.get("trailing_stop") or 0.0)
        if trailing_candidate > current_trailing:
            pos["trailing_stop"] = safe_round(trailing_candidate)

    if target2 > 0 and price >= target2:
        closed = close_open_position_locked(symbol, target2, "TP2", now_ts)
        if closed:
            events.append(closed)
        return events

    pos["last_update"] = now_ts
    return events


def format_position_event_message(event: Dict[str, Any]) -> str:
    if event.get("type") == "partial_tp1":
        return (
            f"🟡 TP1 PARCALI CIKIS\n{event.get('symbol')}\n"
            f"Fiyat: {event.get('price')}\n"
            f"Kapanan Lot: {event.get('qty')}\n"
            f"Kalan Lot: {event.get('remaining')}\n"
            f"Yeni Trailing Stop: {event.get('new_trailing_stop')}\n"
            f"Gerceklesen PnL: {event.get('pnl')}"
        )

    return (
        f"⚪ POZISYON KAPANDI\n{event.get('symbol')}\n"
        f"Neden: {event.get('reason')}\n"
        f"Fiyat: {event.get('price')}\n"
        f"Kapanan Lot: {event.get('qty')}\n"
        f"PnL: {event.get('pnl')}\n"
        f"Toplam Gerceklesen: {event.get('realized_pnl')}"
    )


def evaluate_technical(symbol: str, current_price: float) -> Optional[Dict[str, Any]]:
    hist = fetch_daily_history(symbol)
    if hist is None or len(hist) < 205:
        return None

    close = hist["Close"]
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
    rsi14 = calculate_rsi(close, 14)
    atr20 = float((hist["High"] - hist["Low"]).rolling(20).mean().iloc[-1])
    breakout_level = float(hist["High"].tail(21).head(20).max())

    score = 0
    reasons: List[str] = []

    if ema20 > ema50 > ema200:
        score += 35
        reasons.append("Trend yukari")
    elif ema20 < ema50 < ema200:
        score += 5
        reasons.append("Trend zayif/asagi")
    else:
        score += 18
        reasons.append("Trend karisik")

    if current_price > ema50:
        score += 10
        reasons.append("EMA50 uzeri")

    pullback_distance = abs(current_price - ema20) / max(current_price, 1e-6)
    if pullback_distance <= 0.015:
        score += 15
        reasons.append("EMA20'ye yakin")

    if current_price > breakout_level:
        score += 15
        reasons.append("20 gun direnc ustu")

    if rsi14 is not None:
        if 48 <= rsi14 <= 62:
            score += 12
            reasons.append("RSI dengeli")
        elif 40 <= rsi14 <= 70:
            score += 6
            reasons.append("RSI kabul edilebilir")

    vol_ratio = atr20 / max(current_price, 1e-6)
    if 0.008 <= vol_ratio <= 0.045:
        score += 10
        reasons.append("Volatilite uygun")

    score = int(clamp(score, 0, 100))
    long_stop = min(ema20, current_price - (1.2 * atr20))
    long_stop = min(long_stop, current_price - 0.01)
    short_stop = max(ema20, current_price + (1.2 * atr20))
    short_stop = max(short_stop, current_price + 0.01)

    return {
        "score": score,
        "ema20": safe_round(ema20),
        "ema50": safe_round(ema50),
        "ema200": safe_round(ema200),
        "rsi14": safe_round(rsi14),
        "atr20": safe_round(atr20),
        "breakout_level": safe_round(breakout_level),
        "stop_long": safe_round(long_stop),
        "stop_short": safe_round(short_stop),
        "reasons": reasons[:5],
    }


def evaluate_fundamental(symbol: str) -> Dict[str, Any]:
    info = {}
    try:
        info = get_ticker(symbol).info or {}
    except Exception:
        info = {}

    score = 50
    reasons: List[str] = []

    pe = info.get("forwardPE") or info.get("trailingPE")
    pb = info.get("priceToBook")
    roe = info.get("returnOnEquity")
    debt_to_equity = info.get("debtToEquity")
    revenue_growth = info.get("revenueGrowth")
    earnings_growth = info.get("earningsGrowth")
    margin = info.get("profitMargins")

    if pe is not None:
        if 0 < pe < 12:
            score += 12
            reasons.append("F/K makul")
        elif pe > 30:
            score -= 10
            reasons.append("F/K yuksek")

    if pb is not None:
        if 0 < pb < 2.5:
            score += 8
            reasons.append("PD/DD dengeli")
        elif pb > 6:
            score -= 8
            reasons.append("PD/DD yuksek")

    if roe is not None:
        roe_pct = roe * 100 if roe < 3 else roe
        if roe_pct >= 18:
            score += 10
            reasons.append("ROE guclu")
        elif roe_pct < 8:
            score -= 8
            reasons.append("ROE zayif")

    if debt_to_equity is not None:
        if debt_to_equity < 80:
            score += 8
            reasons.append("Borc seviyesi makul")
        elif debt_to_equity > 250:
            score -= 10
            reasons.append("Borc seviyesi yuksek")

    if revenue_growth is not None:
        if revenue_growth > 0.10:
            score += 8
            reasons.append("Gelir buyumesi pozitif")
        elif revenue_growth < 0:
            score -= 8
            reasons.append("Gelir daralmasi")

    if earnings_growth is not None:
        if earnings_growth > 0.10:
            score += 8
            reasons.append("Kar buyumesi pozitif")
        elif earnings_growth < 0:
            score -= 8
            reasons.append("Kar daralmasi")

    if margin is not None:
        if margin > 0.12:
            score += 6
            reasons.append("Marj guclu")
        elif margin < 0.03:
            score -= 6
            reasons.append("Marj zayif")

    return {
        "score": int(clamp(score, 0, 100)),
        "reasons": reasons[:5],
        "pe": safe_round(pe),
        "pb": safe_round(pb),
        "roe": safe_round(roe),
        "debt_to_equity": safe_round(debt_to_equity),
    }


def evaluate_news(symbol: str) -> Dict[str, Any]:
    score = 50
    reasons: List[str] = []
    now_ts = time.time()
    lookback_sec = NEWS_LOOKBACK_HOURS * 3600

    positive_keywords = [
        "ihale", "sozlesme", "sözleşme", "onay", "temettu", "temettu", "geri alim", "geri alım",
        "buyback", "new order", "approval", "upgrade", "capacity", "yatirim", "yatırım", "kar artisi",
    ]
    negative_keywords = [
        "ceza", "dava", "zarar", "sorusturma", "soruşturma", "downgrade", "risk", "iptal",
        "cancel", "default", "iflas", "borc", "borç", "satış baskisi", "satis baskisi",
    ]

    try:
        news_items = get_ticker(symbol).news or []
    except Exception:
        news_items = []

    recent_titles: List[str] = []
    for item in news_items[:20]:
        ts = item.get("providerPublishTime")
        if ts is None:
            continue
        if now_ts - float(ts) > lookback_sec:
            continue
        title = (item.get("title") or "").strip().lower()
        if title:
            recent_titles.append(title)

    if not recent_titles:
        reasons.append("Son haber etkisi notr")
        return {"score": score, "reasons": reasons}

    pos_hits = 0
    neg_hits = 0
    for title in recent_titles:
        if any(k in title for k in positive_keywords):
            pos_hits += 1
        if any(k in title for k in negative_keywords):
            neg_hits += 1

    score += min(30, pos_hits * 8)
    score -= min(30, neg_hits * 8)

    if pos_hits > 0:
        reasons.append("Pozitif haber/katalizor var")
    if neg_hits > 0:
        reasons.append("Negatif haber riski var")
    if pos_hits == 0 and neg_hits == 0:
        reasons.append("Haber etkisi dengeli")

    return {"score": int(clamp(score, 0, 100)), "reasons": reasons[:3]}


def evaluate_market_regime() -> Dict[str, Any]:
    now_ts = time.time()
    if now_ts - float(_regime_cache.get("updated_at", 0.0)) < ANALYSIS_REFRESH_SEC:
        return _regime_cache

    symbols = ["XU100.IS", "^XU100", "XU030.IS"]
    for regime_symbol in symbols:
        try:
            hist = yf.Ticker(regime_symbol).history(period="1y", interval="1d", actions=False, timeout=8)
            if hist is None or hist.empty or len(hist) < 60:
                continue

            close = hist["Close"]
            ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
            ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
            last_close = float(close.iloc[-1])

            if last_close > ema20 > ema50:
                score, reason = 80.0, "Piyasa rejimi pozitif"
            elif last_close > ema50:
                score, reason = 62.0, "Piyasa rejimi dengeli"
            elif ema20 < ema50:
                score, reason = 35.0, "Piyasa rejimi zayif"
            else:
                score, reason = 50.0, "Piyasa rejimi notr"

            _regime_cache.update({
                "score": score,
                "reason": reason,
                "symbol": regime_symbol,
                "updated_at": now_ts,
            })
            return _regime_cache
        except Exception:
            continue

    _regime_cache.update({"score": 55.0, "reason": "Piyasa rejimi varsayilan notr", "updated_at": now_ts})
    return _regime_cache


def _regime_series_for_backtest(length: int):
    try:
        idx = yf.Ticker("^XU100").history(period="2y", interval="1d", actions=False, timeout=8)
        if idx is None or idx.empty or len(idx) < 80:
            return [55.0] * length
        idx_close = idx["Close"]
        idx_ema20 = idx_close.ewm(span=20, adjust=False).mean()
        idx_ema50 = idx_close.ewm(span=50, adjust=False).mean()

        scores = []
        for c, e20, e50 in zip(idx_close.tail(length), idx_ema20.tail(length), idx_ema50.tail(length)):
            if c > e20 > e50:
                scores.append(80.0)
            elif c > e50:
                scores.append(62.0)
            elif e20 < e50:
                scores.append(35.0)
            else:
                scores.append(50.0)
        if len(scores) < length:
            return [55.0] * (length - len(scores)) + scores
        return scores
    except Exception:
        return [55.0] * length


def _select_runtime_preset(regime_score: float) -> str:
    if not AUTO_PRESET_BY_REGIME:
        return STRATEGY_PRESET
    if regime_score >= 72:
        return "AGRESIF"
    if regime_score <= 45:
        return "KORUMACI"
    return "DENGELI"


def _runtime_params(regime_score: float) -> Dict[str, Any]:
    preset_name = _select_runtime_preset(regime_score)
    conf = PRESET_CONFIGS.get(preset_name, PRESET_CONFIGS["DENGELI"])
    return {
        "preset": preset_name,
        "al_threshold": int(conf["AL_THRESHOLD"]),
        "sat_threshold": int(conf["SAT_THRESHOLD"]),
        "weights": {
            "technical": float(conf["TECH_WEIGHT"]),
            "fundamental": float(conf["FUND_WEIGHT"]),
            "news": float(conf["NEWS_WEIGHT"]),
            "regime": float(conf["REGIME_WEIGHT"]),
        },
    }


def _append_performance_event_locked(event: Dict[str, Any]) -> None:
    _ensure_risk_day_locked()
    events = _performance_state.setdefault("recent_events", [])
    events.append(event)
    if len(events) > 80:
        del events[: len(events) - 80]


def _register_decision_locked(action: str) -> None:
    _ensure_risk_day_locked()
    counts = _performance_state.setdefault("decision_counts", {"AL": 0, "BEKLE": 0, "SAT": 0})
    if action not in counts:
        counts[action] = 0
    counts[action] += 1


def _register_position_event_locked(event: Dict[str, Any]) -> None:
    _ensure_risk_day_locked()
    _append_performance_event_locked(event)

    if event.get("type") == "partial_tp1":
        _performance_state["partial_exits"] = int(_performance_state.get("partial_exits", 0)) + 1
        return

    if event.get("type") == "close":
        pnl = float(event.get("pnl") or 0.0)
        _performance_state["daily_realized_pnl"] = float(_performance_state.get("daily_realized_pnl", 0.0)) + pnl
        _performance_state["closed_trades"] = int(_performance_state.get("closed_trades", 0)) + 1
        if pnl >= 0:
            _performance_state["wins"] = int(_performance_state.get("wins", 0)) + 1
        else:
            _performance_state["losses"] = int(_performance_state.get("losses", 0)) + 1


def _performance_snapshot_locked() -> Dict[str, Any]:
    _ensure_risk_day_locked()
    closed = int(_performance_state.get("closed_trades", 0))
    wins = int(_performance_state.get("wins", 0))
    losses = int(_performance_state.get("losses", 0))
    daily_realized = float(_performance_state.get("daily_realized_pnl", 0.0))
    expectancy = (daily_realized / closed) if closed > 0 else 0.0
    win_rate = (wins / closed) * 100.0 if closed > 0 else 0.0
    return {
        "date": _performance_state.get("date"),
        "daily_realized_pnl": safe_round(daily_realized),
        "closed_trades": closed,
        "partial_exits": int(_performance_state.get("partial_exits", 0)),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": safe_round(win_rate),
        "expectancy": safe_round(expectancy),
        "decision_counts": dict(_performance_state.get("decision_counts", {})),
        "recent_events": list(_performance_state.get("recent_events", [])[-20:]),
        "reports_sent": bool(_performance_state.get("reports_sent", False)),
    }


def _maybe_send_daily_report_locked(now_ts: float) -> None:
    _ensure_risk_day_locked()
    if not TOKEN or not CHAT_ID:
        return

    now = datetime.fromtimestamp(now_ts, ZoneInfo("Europe/Istanbul"))
    if now.hour != DAILY_REPORT_HOUR:
        return
    if _performance_state.get("reports_sent"):
        return

    perf = _performance_snapshot_locked()
    risk_budget = ACCOUNT_SIZE * (DAILY_RISK_CAP_PERCENT / 100.0)
    used = float(_risk_state.get("daily_used_risk", 0.0))

    msg = (
        f"📊 GUNLUK SISTEM OZETI\n"
        f"Tarih: {perf.get('date')}\n"
        f"Gerceklesen PnL: {perf.get('daily_realized_pnl')}\n"
        f"Kapanan Islem: {perf.get('closed_trades')}\n"
        f"Win Rate: {perf.get('win_rate_pct')}%\n"
        f"Expectancy: {perf.get('expectancy')}\n"
        f"Risk Kullanimi: {safe_round(used)} / {safe_round(risk_budget)}\n"
        f"Karar Sayilari: {perf.get('decision_counts')}"
    )
    send_telegram(msg)
    _performance_state["reports_sent"] = True


def run_backtest(symbol: str, days: int, initial_capital: float) -> Dict[str, Any]:
    hist = fetch_daily_history(symbol)
    if hist is None or len(hist) < 260:
        return {"error": "Not enough historical data"}

    days = int(clamp(days, 180, 730))
    data = hist.tail(days + 220).copy()
    close = data["Close"]
    high = data["High"]
    low = data["Low"]

    data["ema20"] = close.ewm(span=20, adjust=False).mean()
    data["ema50"] = close.ewm(span=50, adjust=False).mean()
    data["ema200"] = close.ewm(span=200, adjust=False).mean()
    data["atr20"] = (high - low).rolling(20).mean()
    data["rsi14"] = (100 - (100 / (1 + (
        close.diff().clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        / ((-close.diff().clip(upper=0)).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean().replace(0, 1e-9))
    ))))
    data["breakout"] = high.rolling(20).max().shift(1)

    data = data.dropna().tail(days)
    if data.empty:
        return {"error": "Not enough calculated bars"}

    regime_scores = _regime_series_for_backtest(len(data))
    w_tech, w_fund, w_news, w_regime = normalize_weights()

    equity = float(initial_capital)
    peak = equity
    max_dd = 0.0
    trades: List[Dict[str, Any]] = []
    decision_counts = {"AL": 0, "BEKLE": 0, "SAT": 0}

    position = None

    for i, (idx, row) in enumerate(data.iterrows()):
        price = float(row["Close"])
        ema20 = float(row["ema20"])
        ema50 = float(row["ema50"])
        ema200 = float(row["ema200"])
        rsi14 = float(row["rsi14"])
        atr20 = float(row["atr20"])
        breakout = float(row["breakout"])

        tech_score = 0
        if ema20 > ema50 > ema200:
            tech_score += 35
        elif ema20 < ema50 < ema200:
            tech_score += 5
        else:
            tech_score += 18
        if price > ema50:
            tech_score += 10
        if abs(price - ema20) / max(price, 1e-6) <= 0.015:
            tech_score += 15
        if price > breakout:
            tech_score += 15
        if 48 <= rsi14 <= 62:
            tech_score += 12
        elif 40 <= rsi14 <= 70:
            tech_score += 6
        if 0.008 <= (atr20 / max(price, 1e-6)) <= 0.045:
            tech_score += 10
        tech_score = clamp(tech_score, 0, 100)

        regime_score = float(regime_scores[i])
        total_score = (
            tech_score * w_tech
            + 50.0 * w_fund
            + 50.0 * w_news
            + regime_score * w_regime
        )

        if total_score >= AL_THRESHOLD and tech_score >= 60 and regime_score >= 50:
            action = "AL"
        elif total_score <= SAT_THRESHOLD or (tech_score <= 35 and regime_score < 45):
            action = "SAT"
        else:
            action = "BEKLE"
        decision_counts[action] += 1

        if position is None and action == "AL":
            stop = min(ema20, price - (1.2 * atr20))
            stop = min(stop, price - 0.01)
            if stop_distance_allowed(price, stop):
                risk_per_share = price - stop
                lot = int((equity * (RISK_PERCENT / 100.0)) / max(risk_per_share, 1e-6))
                if lot > 0:
                    position = {
                        "entry_price": price,
                        "entry_date": str(idx.date()),
                        "stop": stop,
                        "target": price + (2 * risk_per_share),
                        "lot": lot,
                    }
        elif position is not None:
            exit_reason = None
            exit_price = None
            if float(row["Low"]) <= position["stop"]:
                exit_price = position["stop"]
                exit_reason = "STOP"
            elif float(row["High"]) >= position["target"]:
                exit_price = position["target"]
                exit_reason = "TARGET"
            elif action == "SAT":
                exit_price = price
                exit_reason = "SAT_SIGNAL"

            if exit_price is not None:
                pnl = (exit_price - position["entry_price"]) * position["lot"]
                trade_ret_pct = ((exit_price / position["entry_price"]) - 1.0) * 100.0
                equity += pnl
                trades.append(
                    {
                        "entry_date": position["entry_date"],
                        "exit_date": str(idx.date()),
                        "entry": safe_round(position["entry_price"]),
                        "exit": safe_round(exit_price),
                        "lot": position["lot"],
                        "pnl": safe_round(pnl),
                        "return_pct": safe_round(trade_ret_pct),
                        "reason": exit_reason,
                    }
                )
                position = None

        peak = max(peak, equity)
        dd = ((peak - equity) / max(peak, 1e-6)) * 100.0
        max_dd = max(max_dd, dd)

    if position is not None:
        last_price = float(data["Close"].iloc[-1])
        pnl = (last_price - position["entry_price"]) * position["lot"]
        trade_ret_pct = ((last_price / position["entry_price"]) - 1.0) * 100.0
        equity += pnl
        trades.append(
            {
                "entry_date": position["entry_date"],
                "exit_date": str(data.index[-1].date()),
                "entry": safe_round(position["entry_price"]),
                "exit": safe_round(last_price),
                "lot": position["lot"],
                "pnl": safe_round(pnl),
                "return_pct": safe_round(trade_ret_pct),
                "reason": "FORCED_EXIT",
            }
        )

    total_trades = len(trades)
    wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
    losses = sum(1 for t in trades if (t.get("pnl") or 0) < 0)
    gross_profit = sum((t.get("pnl") or 0) for t in trades if (t.get("pnl") or 0) > 0)
    gross_loss_abs = abs(sum((t.get("pnl") or 0) for t in trades if (t.get("pnl") or 0) < 0))
    avg_pnl = (sum((t.get("pnl") or 0) for t in trades) / total_trades) if total_trades > 0 else 0.0
    win_rate = (wins / total_trades) * 100.0 if total_trades > 0 else 0.0
    profit_factor = (gross_profit / gross_loss_abs) if gross_loss_abs > 0 else None

    return {
        "symbol": symbol,
        "period_days": days,
        "initial_capital": safe_round(initial_capital),
        "final_capital": safe_round(equity),
        "total_return_pct": safe_round(((equity / initial_capital) - 1.0) * 100.0),
        "max_drawdown_pct": safe_round(max_dd),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": safe_round(win_rate),
        "profit_factor": safe_round(profit_factor) if profit_factor is not None else None,
        "avg_pnl": safe_round(avg_pnl),
        "decision_counts": decision_counts,
        "assumptions": {
            "fundamental_score_backtest": 50,
            "news_score_backtest": 50,
            "note": "Backtest, teknik+rejim agirlikli tahmini simülasyondur.",
        },
        "trades": trades[-30:],
    }


def _simulate_segment(data, regime_scores: List[float], params: Dict[str, Any], initial_capital: float) -> Dict[str, Any]:
    weights = params["weights"]
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        weight_sum = 1.0
    w_tech = weights["technical"] / weight_sum
    w_fund = weights["fundamental"] / weight_sum
    w_news = weights["news"] / weight_sum
    w_regime = weights["regime"] / weight_sum

    al_th = int(params["AL_THRESHOLD"])
    sat_th = int(params["SAT_THRESHOLD"])

    equity = float(initial_capital)
    peak = equity
    max_dd = 0.0
    trades = 0
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss_abs = 0.0
    position = None

    for i, (_, row) in enumerate(data.iterrows()):
        price = float(row["Close"])
        ema20 = float(row["ema20"])
        ema50 = float(row["ema50"])
        ema200 = float(row["ema200"])
        rsi14 = float(row["rsi14"])
        atr20 = float(row["atr20"])
        breakout = float(row["breakout"])

        tech_score = 0
        if ema20 > ema50 > ema200:
            tech_score += 35
        elif ema20 < ema50 < ema200:
            tech_score += 5
        else:
            tech_score += 18
        if price > ema50:
            tech_score += 10
        if abs(price - ema20) / max(price, 1e-6) <= 0.015:
            tech_score += 15
        if price > breakout:
            tech_score += 15
        if 48 <= rsi14 <= 62:
            tech_score += 12
        elif 40 <= rsi14 <= 70:
            tech_score += 6
        if 0.008 <= (atr20 / max(price, 1e-6)) <= 0.045:
            tech_score += 10
        tech_score = clamp(tech_score, 0, 100)

        regime_score = float(regime_scores[i]) if i < len(regime_scores) else 55.0
        total_score = tech_score * w_tech + 50.0 * w_fund + 50.0 * w_news + regime_score * w_regime

        if total_score >= al_th and tech_score >= 60 and regime_score >= 50:
            action = "AL"
        elif total_score <= sat_th or (tech_score <= 35 and regime_score < 45):
            action = "SAT"
        else:
            action = "BEKLE"

        if position is None and action == "AL":
            stop = min(ema20, price - (1.2 * atr20))
            stop = min(stop, price - 0.01)
            if stop_distance_allowed(price, stop):
                risk_per_share = price - stop
                lot = int((equity * (RISK_PERCENT / 100.0)) / max(risk_per_share, 1e-6))
                if lot > 0:
                    position = {
                        "entry": price,
                        "stop": stop,
                        "target": price + (2 * risk_per_share),
                        "lot": lot,
                    }
        elif position is not None:
            exit_price = None
            if float(row["Low"]) <= position["stop"]:
                exit_price = position["stop"]
            elif float(row["High"]) >= position["target"]:
                exit_price = position["target"]
            elif action == "SAT":
                exit_price = price

            if exit_price is not None:
                pnl = (exit_price - position["entry"]) * position["lot"]
                equity += pnl
                trades += 1
                if pnl >= 0:
                    wins += 1
                    gross_profit += pnl
                else:
                    losses += 1
                    gross_loss_abs += abs(pnl)
                position = None

        peak = max(peak, equity)
        dd = ((peak - equity) / max(peak, 1e-6)) * 100.0
        max_dd = max(max_dd, dd)

    total_return_pct = ((equity / initial_capital) - 1.0) * 100.0
    profit_factor = (gross_profit / gross_loss_abs) if gross_loss_abs > 0 else (2.0 if gross_profit > 0 else 0.0)
    return {
        "total_return_pct": safe_round(total_return_pct),
        "max_drawdown_pct": safe_round(max_dd),
        "profit_factor": safe_round(profit_factor),
        "trades": trades,
        "wins": wins,
        "losses": losses,
    }


def run_walkforward_calibration(symbol: str, total_days: int, train_days: int, test_days: int, initial_capital: float) -> Dict[str, Any]:
    hist = fetch_daily_history(symbol)
    if hist is None or len(hist) < (train_days + test_days + 220):
        return {"error": "Not enough data for walk-forward"}

    total_days = int(clamp(total_days, train_days + test_days, 730))
    data = hist.tail(total_days + 220).copy()
    close = data["Close"]
    high = data["High"]
    low = data["Low"]
    data["ema20"] = close.ewm(span=20, adjust=False).mean()
    data["ema50"] = close.ewm(span=50, adjust=False).mean()
    data["ema200"] = close.ewm(span=200, adjust=False).mean()
    data["atr20"] = (high - low).rolling(20).mean()
    data["rsi14"] = (100 - (100 / (1 + (
        close.diff().clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        / ((-close.diff().clip(upper=0)).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean().replace(0, 1e-9))
    ))))
    data["breakout"] = high.rolling(20).max().shift(1)
    data = data.dropna().tail(total_days)
    if len(data) < (train_days + test_days):
        return {"error": "Not enough calculated bars for walk-forward"}

    regime_scores = _regime_series_for_backtest(len(data))
    presets = {
        "AGRESIF": {
            "AL_THRESHOLD": PRESET_CONFIGS["AGRESIF"]["AL_THRESHOLD"],
            "SAT_THRESHOLD": PRESET_CONFIGS["AGRESIF"]["SAT_THRESHOLD"],
            "weights": {
                "technical": PRESET_CONFIGS["AGRESIF"]["TECH_WEIGHT"],
                "fundamental": PRESET_CONFIGS["AGRESIF"]["FUND_WEIGHT"],
                "news": PRESET_CONFIGS["AGRESIF"]["NEWS_WEIGHT"],
                "regime": PRESET_CONFIGS["AGRESIF"]["REGIME_WEIGHT"],
            },
        },
        "DENGELI": {
            "AL_THRESHOLD": PRESET_CONFIGS["DENGELI"]["AL_THRESHOLD"],
            "SAT_THRESHOLD": PRESET_CONFIGS["DENGELI"]["SAT_THRESHOLD"],
            "weights": {
                "technical": PRESET_CONFIGS["DENGELI"]["TECH_WEIGHT"],
                "fundamental": PRESET_CONFIGS["DENGELI"]["FUND_WEIGHT"],
                "news": PRESET_CONFIGS["DENGELI"]["NEWS_WEIGHT"],
                "regime": PRESET_CONFIGS["DENGELI"]["REGIME_WEIGHT"],
            },
        },
        "KORUMACI": {
            "AL_THRESHOLD": PRESET_CONFIGS["KORUMACI"]["AL_THRESHOLD"],
            "SAT_THRESHOLD": PRESET_CONFIGS["KORUMACI"]["SAT_THRESHOLD"],
            "weights": {
                "technical": PRESET_CONFIGS["KORUMACI"]["TECH_WEIGHT"],
                "fundamental": PRESET_CONFIGS["KORUMACI"]["FUND_WEIGHT"],
                "news": PRESET_CONFIGS["KORUMACI"]["NEWS_WEIGHT"],
                "regime": PRESET_CONFIGS["KORUMACI"]["REGIME_WEIGHT"],
            },
        },
    }

    folds = []
    i = 0
    while (i + train_days + test_days) <= len(data):
        train_slice = data.iloc[i : i + train_days]
        test_slice = data.iloc[i + train_days : i + train_days + test_days]
        train_regime = regime_scores[i : i + train_days]
        test_regime = regime_scores[i + train_days : i + train_days + test_days]

        train_scores = {}
        for name, conf in presets.items():
            train_res = _simulate_segment(train_slice, train_regime, conf, initial_capital)
            score = (float(train_res.get("total_return_pct") or 0.0) * 1.0) + (float(train_res.get("profit_factor") or 0.0) * 8.0) - (float(train_res.get("max_drawdown_pct") or 0.0) * 0.7)
            train_scores[name] = {"metrics": train_res, "score": safe_round(score, 3)}

        best_name = max(train_scores.keys(), key=lambda k: float(train_scores[k]["score"] or -9999))
        test_res = _simulate_segment(test_slice, test_regime, presets[best_name], initial_capital)
        folds.append(
            {
                "fold": len(folds) + 1,
                "selected_preset": best_name,
                "train_scores": train_scores,
                "test_metrics": test_res,
            }
        )
        i += test_days

    if not folds:
        return {"error": "No folds generated"}

    preset_pick_counts = {"AGRESIF": 0, "DENGELI": 0, "KORUMACI": 0}
    total_test_return = 0.0
    total_test_dd = 0.0
    total_test_pf = 0.0
    pf_count = 0
    total_test_trades = 0
    for f in folds:
        preset_pick_counts[f["selected_preset"]] += 1
        total_test_return += float(f["test_metrics"].get("total_return_pct") or 0.0)
        total_test_dd += float(f["test_metrics"].get("max_drawdown_pct") or 0.0)
        pf_val = f["test_metrics"].get("profit_factor")
        if pf_val is not None:
            total_test_pf += float(pf_val)
            pf_count += 1
        total_test_trades += int(f["test_metrics"].get("trades") or 0)

    recommended = max(preset_pick_counts.keys(), key=lambda k: preset_pick_counts[k])
    return {
        "symbol": symbol,
        "train_days": train_days,
        "test_days": test_days,
        "folds": len(folds),
        "recommended_preset": recommended,
        "preset_pick_counts": preset_pick_counts,
        "aggregate_test": {
            "avg_return_pct": safe_round(total_test_return / len(folds)),
            "avg_max_drawdown_pct": safe_round(total_test_dd / len(folds)),
            "avg_profit_factor": safe_round(total_test_pf / pf_count) if pf_count > 0 else None,
            "avg_trades": safe_round(total_test_trades / len(folds), 2),
        },
        "fold_details": folds,
    }


def build_decision(symbol: str, current_price: float) -> Optional[Dict[str, Any]]:
    technical = evaluate_technical(symbol, current_price)
    if technical is None:
        return None

    fundamental = evaluate_fundamental(symbol)
    news = evaluate_news(symbol)
    regime = evaluate_market_regime()

    params = _runtime_params(float(regime.get("score") or 55.0))
    weights = params["weights"]
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        weight_sum = 1.0
    w_tech = weights["technical"] / weight_sum
    w_fund = weights["fundamental"] / weight_sum
    w_news = weights["news"] / weight_sum
    w_regime = weights["regime"] / weight_sum

    total_score = (
        technical["score"] * w_tech
        + fundamental["score"] * w_fund
        + news["score"] * w_news
        + regime["score"] * w_regime
    )
    total_score = int(round(clamp(total_score, 0, 100)))

    if total_score >= int(params["al_threshold"]) and technical["score"] >= 60 and regime["score"] >= 50:
        action = "AL"
    elif total_score <= int(params["sat_threshold"]) or (technical["score"] <= 35 and regime["score"] < 45):
        action = "SAT"
    else:
        action = "BEKLE"

    entry_mid = current_price
    entry_low = current_price * 0.997
    entry_high = current_price * 1.003
    stop = None
    target1 = None
    target2 = None
    lot = None
    total_risk = None
    rr = None

    if action == "AL":
        stop = float(technical["stop_long"])
        if not stop_distance_allowed(entry_mid, stop):
            action = "BEKLE"
        else:
            risk_per_share = entry_mid - stop
            target1 = entry_mid + (2 * risk_per_share)
            target2 = entry_mid + (3 * risk_per_share)
            lot, total_risk = calculate_position(entry_mid, stop)
            rr = 2.0

    elif action == "SAT":
        stop = float(technical["stop_short"])
        if not stop_distance_allowed(entry_mid, stop):
            action = "BEKLE"
        else:
            risk_per_share = stop - entry_mid
            target1 = entry_mid - (2 * risk_per_share)
            target2 = entry_mid - (3 * risk_per_share)
            lot, total_risk = calculate_position(entry_mid, stop)
            rr = 2.0

    reasons: List[str] = []
    reasons.extend(technical.get("reasons", [])[:2])
    reasons.extend(fundamental.get("reasons", [])[:2])
    reasons.extend(news.get("reasons", [])[:1])
    reasons.append(regime.get("reason", ""))
    reasons = [r for r in reasons if r][:5]

    return {
        "symbol": symbol,
        "action": action,
        "score": total_score,
        "entry_low": safe_round(entry_low),
        "entry_high": safe_round(entry_high),
        "stop": safe_round(stop),
        "target1": safe_round(target1),
        "target2": safe_round(target2),
        "rr": safe_round(rr),
        "lot": lot,
        "risk": safe_round(total_risk),
        "reasons": reasons,
        "factors": {
            "technical": technical["score"],
            "fundamental": fundamental["score"],
            "news": news["score"],
            "regime": safe_round(regime["score"]),
        },
        "indicators": {
            "ema20": technical.get("ema20"),
            "ema50": technical.get("ema50"),
            "ema200": technical.get("ema200"),
            "rsi14": technical.get("rsi14"),
        },
        "runtime_preset": params["preset"],
        "runtime_thresholds": {
            "al": int(params["al_threshold"]),
            "sat": int(params["sat_threshold"]),
        },
    }


def format_decision_message(decision: Dict[str, Any]) -> str:
    lines = [
        f"🧭 KARAR {decision.get('action')}",
        decision.get("symbol", ""),
        f"Guven: {decision.get('score')}/100",
    ]

    if decision.get("entry_low") is not None and decision.get("entry_high") is not None:
        lines.append(f"Giris: {decision.get('entry_low')} - {decision.get('entry_high')}")
    if decision.get("stop") is not None:
        lines.append(f"Stop: {decision.get('stop')}")
    if decision.get("target1") is not None:
        lines.append(f"Hedef1: {decision.get('target1')}")
    if decision.get("target2") is not None:
        lines.append(f"Hedef2: {decision.get('target2')}")
    if decision.get("rr") is not None:
        lines.append(f"R/R: {decision.get('rr')}")

    factors = decision.get("factors", {})
    if factors:
        lines.append(
            "Faktorler: "
            f"T{factors.get('technical')} "
            f"F{factors.get('fundamental')} "
            f"N{factors.get('news')} "
            f"R{factors.get('regime')}"
        )

    reasons = decision.get("reasons", [])
    if reasons:
        lines.append("Nedenler:")
        lines.extend([f"- {r}" for r in reasons])

    return "\n".join(lines)


# ================= MONITOR =================
def price_monitor_loop():
    while True:
        try:
            is_market_open = market_open()

            if STRICT_MARKET_HOURS and not is_market_open:
                time.sleep(60)
                continue

            with _state_lock:
                symbols = list(WATCHLIST.keys())

            for symbol in symbols:
                price = fetch_last_price(symbol)
                if price is None:
                    continue

                now_ts = time.time()
                should_refresh_analysis = False
                position_events: List[Dict[str, Any]] = []

                with _state_lock:
                    st = WATCHLIST.get(symbol)
                    if not st:
                        continue

                    _ensure_risk_day_locked()
                    position_events = manage_open_position_locked(symbol, price, now_ts)

                    st.setdefault("last_alert_at", 0.0)
                    st.setdefault("last_analysis_at", 0.0)
                    st.setdefault("last_decision_alert_at", 0.0)

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
                        stop = upper
                        new_lower, new_upper = recenter_band(st, price)
                        st["alerted"] = "lower"

                        if now_ts - float(st.get("last_alert_at", 0.0)) >= ALERT_COOLDOWN_SEC and stop_distance_allowed(price, stop):
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
                        stop = lower
                        new_lower, new_upper = recenter_band(st, price)
                        st["alerted"] = "upper"

                        if now_ts - float(st.get("last_alert_at", 0.0)) >= ALERT_COOLDOWN_SEC and stop_distance_allowed(price, stop):
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

                for ev in position_events:
                    with _state_lock:
                        _register_position_event_locked(ev)
                    send_telegram(format_position_event_message(ev))

                if should_refresh_analysis:
                    decision = build_decision(symbol, price)
                    if decision is None:
                        continue

                    send_decision = False
                    with _state_lock:
                        st = WATCHLIST.get(symbol)
                        if not st:
                            continue

                        prev_decision = st.get("decision") or {}
                        prev_action = prev_decision.get("action")
                        decision = apply_risk_controls_locked(symbol, decision, now_ts)
                        st["decision"] = decision
                        st["last_analysis_at"] = now_ts

                        score_shift = abs(float(decision.get("score", 0)) - float(prev_decision.get("score", 0)))
                        if decision.get("action") != prev_action or score_shift >= 4:
                            append_decision_log(st, symbol, decision, price, now_ts)

                        _register_decision_locked(str(decision.get("action") or "BEKLE"))

                        action_changed = decision.get("action") != prev_action
                        decision_is_actionable = decision.get("action") in {"AL", "SAT"}
                        cooldown_done = now_ts - float(st.get("last_decision_alert_at", 0.0)) >= DECISION_ALERT_COOLDOWN_SEC

                        market_alert_allowed = is_market_open or ALLOW_DECISION_ALERTS_OUTSIDE_MARKET
                        if action_changed and decision_is_actionable and cooldown_done and market_alert_allowed:
                            st["last_decision_alert_at"] = now_ts
                            send_decision = True

                    if send_decision:
                        send_telegram(format_decision_message(decision))

            with _state_lock:
                _maybe_send_daily_report_locked(time.time())

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
        _ensure_risk_day_locked()
        risk_state = {
            "date": _risk_state.get("date"),
            "daily_used_risk": safe_round(_risk_state.get("daily_used_risk", 0.0)),
            "daily_risk_budget": safe_round(ACCOUNT_SIZE * (DAILY_RISK_CAP_PERCENT / 100.0)),
            "active_positions": len(_risk_state.get("open_positions", {})),
            "open_positions": _risk_state.get("open_positions", {}).copy(),
        }
        performance = _performance_snapshot_locked()

    prices = {}
    band_signals = {}
    decisions = {}

    for s, d in snapshot.items():
        p = fetch_last_price(s)
        prices[s] = safe_round(p)

        if p is None:
            band_signals[s] = "VERI YOK"
        elif p <= float(d["lower"]):
            band_signals[s] = "AL"
        elif p >= float(d["upper"]):
            band_signals[s] = "SAT"
        else:
            band_signals[s] = "BEKLE"

        decisions[s] = d.get("decision")

    return jsonify({
        "prices": prices,
        "watchlist": snapshot,
        "band_signals": band_signals,
        "decisions": decisions,
        "strategy": EFFECTIVE_STRATEGY,
        "risk_state": risk_state,
        "performance": performance,
    })


@app.route("/api/risk-state", methods=["GET"])
def api_risk_state():
    with _state_lock:
        _ensure_risk_day_locked()
        return jsonify(
            {
                "date": _risk_state.get("date"),
                "daily_used_risk": safe_round(_risk_state.get("daily_used_risk", 0.0)),
                "daily_risk_budget": safe_round(ACCOUNT_SIZE * (DAILY_RISK_CAP_PERCENT / 100.0)),
                "active_positions": len(_risk_state.get("open_positions", {})),
                "open_positions": _risk_state.get("open_positions", {}),
                "limits": {
                    "daily_risk_cap_percent": DAILY_RISK_CAP_PERCENT,
                    "max_active_positions": MAX_ACTIVE_POSITIONS,
                    "max_positions_per_sector": MAX_POSITIONS_PER_SECTOR,
                },
            }
        )


@app.route("/api/performance", methods=["GET"])
def api_performance():
    with _state_lock:
        _ensure_risk_day_locked()
        perf = _performance_snapshot_locked()
        perf["risk_usage"] = {
            "used": safe_round(_risk_state.get("daily_used_risk", 0.0)),
            "budget": safe_round(ACCOUNT_SIZE * (DAILY_RISK_CAP_PERCENT / 100.0)),
            "open_positions": len(_risk_state.get("open_positions", {})),
        }
        return jsonify(perf)


@app.route("/api/decision-log", methods=["GET"])
def api_decision_log():
    symbol = (request.args.get("symbol") or "").strip().upper()
    limit_raw = request.args.get("limit")
    try:
        limit = int(limit_raw) if limit_raw else 50
    except Exception:
        limit = 50
    limit = max(1, min(limit, DECISION_LOG_LIMIT))

    with _state_lock:
        if symbol:
            st = WATCHLIST.get(symbol)
            logs = (st or {}).get("decision_log", [])
            return jsonify({"symbol": symbol, "count": len(logs[-limit:]), "logs": logs[-limit:]})

        all_logs = []
        for s, st in WATCHLIST.items():
            for row in st.get("decision_log", []):
                all_logs.append({**row, "symbol": s})
        all_logs.sort(key=lambda x: x.get("ts", 0.0), reverse=True)
        logs = all_logs[:limit]
        return jsonify({"symbol": None, "count": len(logs), "logs": logs})


@app.route("/api/backtest", methods=["GET"])
def api_backtest():
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    days_raw = request.args.get("days")
    capital_raw = request.args.get("capital")

    try:
        days = int(days_raw) if days_raw else 365
    except Exception:
        days = 365

    try:
        capital = float(capital_raw.replace(",", ".")) if capital_raw else BACKTEST_INITIAL_CAPITAL
    except Exception:
        capital = BACKTEST_INITIAL_CAPITAL

    result = run_backtest(symbol=symbol, days=days, initial_capital=capital)
    if result.get("error"):
        return jsonify(result), 400
    result["strategy"] = EFFECTIVE_STRATEGY
    return jsonify(result)


@app.route("/api/calibrate", methods=["GET"])
def api_calibrate():
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    try:
        total_days = int((request.args.get("days") or "540").strip())
    except Exception:
        total_days = 540
    try:
        train_days = int((request.args.get("train") or "180").strip())
    except Exception:
        train_days = 180
    try:
        test_days = int((request.args.get("test") or "60").strip())
    except Exception:
        test_days = 60
    try:
        capital = float((request.args.get("capital") or str(BACKTEST_INITIAL_CAPITAL)).replace(",", "."))
    except Exception:
        capital = BACKTEST_INITIAL_CAPITAL

    if train_days < 120 or test_days < 20:
        return jsonify({"error": "train>=120 and test>=20 required"}), 400

    result = run_walkforward_calibration(
        symbol=symbol,
        total_days=total_days,
        train_days=train_days,
        test_days=test_days,
        initial_capital=capital,
    )
    if result.get("error"):
        return jsonify(result), 400
    result["current_strategy"] = EFFECTIVE_STRATEGY
    return jsonify(result)


# ================= PANEL =================
@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        symbol = request.form.get("symbol")
        lower_raw = (request.form.get("lower") or "").strip().replace(",", ".")
        upper_raw = (request.form.get("upper") or "").strip().replace(",", ".")

        try:
            lower = float(lower_raw)
            upper = float(upper_raw)
        except Exception:
            lower = None
            upper = None

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
    <title>BIST Decision Panel v3</title>
    </head>
    <body>
    <h1>BIST Alarm + Karar Paneli (v3)</h1>
    <p>Preset: {{strategy["preset"]}} | AL: {{strategy["al_threshold"]}} | SAT: {{strategy["sat_threshold"]}}</p>

    <table border="1" cellpadding="10">
    <tr>
        <th>Hisse</th>
        <th>Fiyat</th>
        <th>Alt</th>
        <th>Ust</th>
        <th>Bant</th>
        <th>Karar</th>
        <th>Guven</th>
    </tr>
    {% for s in watchlist %}
    <tr>
        <td>{{s}}</td>
        <td id="price-{{s}}">-</td>
        <td id="lower-{{s}}">{{watchlist[s]["lower"]}}</td>
        <td id="upper-{{s}}">{{watchlist[s]["upper"]}}</td>
        <td id="band-{{s}}">-</td>
        <td id="decision-{{s}}">-</td>
        <td id="score-{{s}}">-</td>
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
    <input name="upper" placeholder="Ust Limit">
    <button type="submit">Guncelle</button>
    </form>

    <script>
    async function refresh(){
        const r = await fetch("/api/data");
        const d = await r.json();
        for(const s in d.prices){
            document.getElementById("price-"+s).innerText =
                d.prices[s]===null ? "Veri Yok" : d.prices[s];
            document.getElementById("band-"+s).innerText = d.band_signals[s] || "-";

            const decision = d.decisions && d.decisions[s] ? d.decisions[s] : null;
            if (decision) {
                document.getElementById("decision-"+s).innerText = decision.action || "-";
                document.getElementById("score-"+s).innerText = (decision.score ?? "-") + "/100";
            } else {
                document.getElementById("decision-"+s).innerText = "-";
                document.getElementById("score-"+s).innerText = "-";
            }

            if (d.watchlist && d.watchlist[s]) {
                document.getElementById("lower-" + s).innerText = d.watchlist[s].lower;
                document.getElementById("upper-" + s).innerText = d.watchlist[s].upper;
            }
        }
    }
    setInterval(refresh, 15000);
    refresh();
    </script>

    </body>
    </html>
    """

    return render_template_string(html, watchlist=snapshot, strategy=EFFECTIVE_STRATEGY)


if __name__ == "__main__":
    if RUN_MONITOR_IN_WEB:
        ensure_monitor_started()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
