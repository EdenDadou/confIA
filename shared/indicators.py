"""Shared technical analysis indicators library (pure-numpy)."""

import numpy as np


def compute_rsi(closes: list, period: int = 14) -> float:
    """RSI 0-100, returns 50.0 if insufficient data."""
    if len(closes) < period + 1:
        return 50.0

    closes = np.array(closes, dtype=float)
    deltas = np.diff(closes)

    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Wilder's smoothing: seed with SMA, then exponential
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def compute_ema(data: list, period: int) -> list:
    """Exponential moving average, iterative."""
    if not data:
        return []

    data = np.array(data, dtype=float)
    k = 2.0 / (period + 1)
    ema = np.empty_like(data)
    ema[0] = data[0]

    for i in range(1, len(data)):
        ema[i] = data[i] * k + ema[i - 1] * (1 - k)

    return ema.tolist()


def compute_macd(
    closes: list, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple:
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)

    macd_line_arr = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_arr = compute_ema(macd_line_arr, signal)

    macd_val = macd_line_arr[-1]
    signal_val = signal_arr[-1]
    histogram = macd_val - signal_val

    return (float(macd_val), float(signal_val), float(histogram))


def compute_bollinger(
    closes: list, period: int = 20, std_dev: float = 2.0
) -> tuple:
    """Returns (upper, middle, lower)."""
    closes = np.array(closes, dtype=float)
    if len(closes) < period:
        middle = float(np.mean(closes))
        sd = float(np.std(closes))
    else:
        middle = float(np.mean(closes[-period:]))
        sd = float(np.std(closes[-period:]))

    upper = middle + std_dev * sd
    lower = middle - std_dev * sd
    return (float(upper), float(middle), float(lower))


def compute_atr(candles: list, period: int = 14) -> float:
    """Average True Range."""
    if len(candles) < 2:
        return 0.0

    true_ranges = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        prev_c = candles[i - 1]["close"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return float(np.mean(true_ranges))

    # Wilder's smoothing
    atr = np.mean(true_ranges[:period])
    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period

    return float(atr)


def compute_vwap(candles: list) -> float:
    """Volume Weighted Average Price."""
    if not candles:
        return 0.0

    cum_tp_vol = 0.0
    cum_vol = 0.0

    for c in candles:
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        vol = c["volume"]
        cum_tp_vol += tp * vol
        cum_vol += vol

    if cum_vol == 0:
        return 0.0

    return float(cum_tp_vol / cum_vol)


def compute_stoch_rsi(closes: list, period: int = 14) -> tuple:
    """Returns (%K, %D) both 0-100."""
    if len(closes) < period + 1:
        return (50.0, 50.0)

    # Compute rolling RSI values
    rsi_values = []
    for i in range(period + 1, len(closes) + 1):
        rsi_val = compute_rsi(closes[:i], period)
        rsi_values.append(rsi_val)

    if len(rsi_values) < period:
        return (50.0, 50.0)

    # Stochastic formula on last `period` RSI values
    window = rsi_values[-period:]
    rsi_min = min(window)
    rsi_max = max(window)

    if rsi_max == rsi_min:
        k = 50.0
    else:
        k = ((rsi_values[-1] - rsi_min) / (rsi_max - rsi_min)) * 100.0

    # %D is 3-period SMA of %K — approximate using last 3 stoch values
    stoch_vals = []
    for i in range(max(0, len(rsi_values) - 3), len(rsi_values)):
        w = rsi_values[max(0, i - period + 1) : i + 1]
        if len(w) < 2:
            stoch_vals.append(50.0)
            continue
        lo = min(w)
        hi = max(w)
        if hi == lo:
            stoch_vals.append(50.0)
        else:
            stoch_vals.append(((rsi_values[i] - lo) / (hi - lo)) * 100.0)

    d = float(np.mean(stoch_vals))

    return (float(np.clip(k, 0, 100)), float(np.clip(d, 0, 100)))


def compute_obv(candles: list) -> float:
    """On-Balance Volume."""
    if not candles:
        return 0.0

    obv = 0.0
    for i in range(1, len(candles)):
        if candles[i]["close"] > candles[i - 1]["close"]:
            obv += candles[i]["volume"]
        elif candles[i]["close"] < candles[i - 1]["close"]:
            obv -= candles[i]["volume"]

    return float(obv)


def compute_momentum(closes: list, n: int = 3) -> str:
    """Returns 'up', 'down', or 'neutral'."""
    if len(closes) < n + 1:
        return "neutral"

    recent = closes[-(n + 1) :]
    ups = 0
    downs = 0
    for i in range(1, len(recent)):
        if recent[i] > recent[i - 1]:
            ups += 1
        elif recent[i] < recent[i - 1]:
            downs += 1

    if ups > downs:
        return "up"
    elif downs > ups:
        return "down"
    else:
        return "neutral"


def detect_divergence(closes: list) -> dict:
    """RSI vs Price divergence detection."""
    window = 20
    if len(closes) < window:
        return {"type": "none", "label": "insufficient data", "score": 0}

    recent = closes[-window:]
    mid = window // 2

    price_first_half = recent[:mid]
    price_second_half = recent[mid:]

    price_low_1 = min(price_first_half)
    price_low_2 = min(price_second_half)

    # Compute RSI at midpoint and at end
    rsi_mid = compute_rsi(closes[: -window + mid], 14)
    rsi_end = compute_rsi(closes, 14)

    price_delta = price_low_2 - price_low_1
    rsi_delta = rsi_end - rsi_mid

    # Bullish divergence: price makes lower low but RSI makes higher low
    if price_delta < 0 and rsi_delta > 0:
        score = min(100, int(abs(rsi_delta) * 2))
        return {
            "type": "bullish",
            "label": "price lower low, RSI higher low",
            "score": score,
        }

    # Bearish divergence: price makes higher high but RSI makes lower high
    price_high_1 = max(price_first_half)
    price_high_2 = max(price_second_half)
    price_high_delta = price_high_2 - price_high_1

    if price_high_delta > 0 and rsi_delta < 0:
        score = min(100, int(abs(rsi_delta) * 2))
        return {
            "type": "bearish",
            "label": "price higher high, RSI lower high",
            "score": score,
        }

    return {"type": "none", "label": "no divergence detected", "score": 0}
