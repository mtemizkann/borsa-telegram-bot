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

ACCOUNT_SIZE = float(os.environ.get("ACCOUNT_SIZE", "150000").replace(",", "."))
RISK_PERCENT = float(os.environ.get("RISK_PERCENT", "2").replace(",", "."))
BAND_SIZE_TL = float(os.environ.get("BAND_SIZE_TL", "1").replace(",", "."))
MIN_STOP_DISTANCE_TL = float(os.environ.get("MIN_STOP_DISTANCE_TL", "0.5").replace(",", "."))
MAX_STOP_DISTANCE_TL = float(os.environ.get("MAX_STOP_DISTANCE_TL", "20").replace(",", "."))
ALERT_COOLDOWN_SEC = int(float(os.environ.get("ALERT_COOLDOWN_SEC", "180").replace(",", ".")))

ANALYSIS_REFRESH_SEC = int(float(os.environ.get("ANALYSIS_REFRESH_SEC", "300").replace(",", ".")))
DECISION_ALERT_COOLDOWN_SEC = int(float(os.environ.get("DECISION_ALERT_COOLDOWN_SEC", "3600").replace(",", ".")))
NEWS_LOOKBACK_HOURS = int(float(os.environ.get("NEWS_LOOKBACK_HOURS", "72").replace(",", ".")))

AL_THRESHOLD = int(float(os.environ.get("AL_THRESHOLD", "72").replace(",", ".")))
SAT_THRESHOLD = int(float(os.environ.get("SAT_THRESHOLD", "38").replace(",", ".")))

TECH_WEIGHT = float(os.environ.get("TECH_WEIGHT", "0.45").replace(",", "."))
FUND_WEIGHT = float(os.environ.get("FUND_WEIGHT", "0.25").replace(",", "."))
NEWS_WEIGHT = float(os.environ.get("NEWS_WEIGHT", "0.20").replace(",", "."))
REGIME_WEIGHT = float(os.environ.get("REGIME_WEIGHT", "0.10").replace(",", "."))
BACKTEST_INITIAL_CAPITAL = float(os.environ.get("BACKTEST_INITIAL_CAPITAL", "100000").replace(",", "."))
DECISION_LOG_LIMIT = int(float(os.environ.get("DECISION_LOG_LIMIT", "200").replace(",", ".")))

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


def build_decision(symbol: str, current_price: float) -> Optional[Dict[str, Any]]:
    technical = evaluate_technical(symbol, current_price)
    if technical is None:
        return None

    fundamental = evaluate_fundamental(symbol)
    news = evaluate_news(symbol)
    regime = evaluate_market_regime()

    w_tech, w_fund, w_news, w_regime = normalize_weights()
    total_score = (
        technical["score"] * w_tech
        + fundamental["score"] * w_fund
        + news["score"] * w_news
        + regime["score"] * w_regime
    )
    total_score = int(round(clamp(total_score, 0, 100)))

    if total_score >= AL_THRESHOLD and technical["score"] >= 60 and regime["score"] >= 50:
        action = "AL"
    elif total_score <= SAT_THRESHOLD or (technical["score"] <= 35 and regime["score"] < 45):
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

            with _state_lock:
                symbols = list(WATCHLIST.keys())

            for symbol in symbols:
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
                        st["decision"] = decision
                        st["last_analysis_at"] = now_ts

                        score_shift = abs(float(decision.get("score", 0)) - float(prev_decision.get("score", 0)))
                        if decision.get("action") != prev_action or score_shift >= 4:
                            append_decision_log(st, symbol, decision, price, now_ts)

                        action_changed = decision.get("action") != prev_action
                        decision_is_actionable = decision.get("action") in {"AL", "SAT"}
                        cooldown_done = now_ts - float(st.get("last_decision_alert_at", 0.0)) >= DECISION_ALERT_COOLDOWN_SEC

                        if action_changed and decision_is_actionable and cooldown_done:
                            st["last_decision_alert_at"] = now_ts
                            send_decision = True

                    if send_decision:
                        send_telegram(format_decision_message(decision))

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
    })


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

    return render_template_string(html, watchlist=snapshot)


if __name__ == "__main__":
    if RUN_MONITOR_IN_WEB:
        ensure_monitor_started()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
