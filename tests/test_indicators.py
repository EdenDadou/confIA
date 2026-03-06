import pytest
import numpy as np
from shared.indicators import (
    compute_rsi, compute_macd, compute_bollinger, compute_atr,
    compute_vwap, compute_ema, compute_stoch_rsi, compute_obv,
    compute_momentum, detect_divergence,
)


def make_closes(n=50, base=95000, volatility=50):
    np.random.seed(42)
    return list(base + np.cumsum(np.random.randn(n) * volatility))


def make_ohlcv(n=50, base=95000):
    np.random.seed(42)
    candles = []
    price = base
    for _ in range(n):
        o = price
        c = o + np.random.randn() * 50
        h = max(o, c) + abs(np.random.randn() * 20)
        l = min(o, c) - abs(np.random.randn() * 20)
        v = abs(np.random.randn() * 1000) + 100
        candles.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
        price = c
    return candles


def test_rsi_range():
    closes = make_closes(50)
    rsi = compute_rsi(closes, 14)
    assert 0 <= rsi <= 100


def test_rsi_short_input():
    assert compute_rsi([100, 101], 14) == 50.0


def test_macd_returns_three_values():
    closes = make_closes(50)
    macd_line, signal_line, histogram = compute_macd(closes)
    assert isinstance(histogram, float)


def test_bollinger_bands():
    closes = make_closes(50)
    upper, middle, lower = compute_bollinger(closes, 20, 2.0)
    assert upper > middle > lower


def test_atr_positive():
    candles = make_ohlcv(50)
    atr = compute_atr(candles, 14)
    assert atr > 0


def test_vwap():
    candles = make_ohlcv(50)
    vwap = compute_vwap(candles)
    assert vwap > 0


def test_ema():
    closes = make_closes(50)
    ema = compute_ema(closes, 12)
    assert len(ema) == len(closes)


def test_stoch_rsi():
    closes = make_closes(50)
    k, d = compute_stoch_rsi(closes, 14)
    assert 0 <= k <= 100
    assert 0 <= d <= 100


def test_obv():
    candles = make_ohlcv(50)
    obv = compute_obv(candles)
    assert isinstance(obv, float)


def test_momentum():
    closes = make_closes(50)
    mom = compute_momentum(closes, 3)
    assert mom in ("up", "down", "neutral")


def test_divergence():
    closes = make_closes(50)
    div = detect_divergence(closes)
    assert div["type"] in ("bullish", "bearish", "none")
    assert 0 <= div["score"] <= 100
