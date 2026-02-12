import pandas as pd

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss.replace(0, pd.NA))
    return 100 - (100 / (1 + rs))

def analyze_symbol(df: pd.DataFrame):
    """
    df: columns = ['open','high','low','close'] (daily), index datetime
    returns: dict with signal + levels
    """
    close = df["close"]
    low = df["low"]

    ema50 = ema(close, 50).iloc[-1]
    ema200 = ema(close, 200).iloc[-1]
    price = close.iloc[-1]

    rsi14 = rsi(close, 14).iloc[-1]

    support = low.tail(20).min()
    dist_to_support = (price - support) / price  # 0.03 = %3

    trend_ok = (price > ema50) and (ema50 > ema200)

    # RSI: iki mod
    rsi_aggr = (rsi14 < 30)
    rsi_norm = (40 <= rsi14 <= 55)
    rsi_ok = rsi_aggr or rsi_norm

    support_ok = dist_to_support <= 0.03

    stop = support * 0.98
    risk = price - stop
    target = price + 2 * risk  # 1:2

    rr = (target - price) / risk if risk > 0 else 0
    rr_ok = rr >= 2

    signal = "AL" if (trend_ok and rsi_ok and support_ok and rr_ok) else "BEKLE"

    # Kademeli limit önerisi (piyasanın ALTINDAN)
    # Çok basit: %0.5 ve %1.2 aşağı. İstersen ATR ile dinamik yaparız.
    k1 = round(price * 0.995, 2)
    k2 = round(price * 0.988, 2)

    return {
        "price": float(price),
        "ema50": float(ema50),
        "ema200": float(ema200),
        "rsi14": float(rsi14),
        "support": float(support),
        "stop": float(stop),
        "target": float(target),
        "rr": float(rr),
        "trend_ok": bool(trend_ok),
        "rsi_ok": bool(rsi_ok),
        "support_ok": bool(support_ok),
        "rr_ok": bool(rr_ok),
        "kademeler": [k1, k2],
        "signal": signal,
    }
