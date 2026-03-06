import pytest
import numpy as np
from agents.technical.agent import TechnicalAgent


@pytest.fixture
def agent():
    return TechnicalAgent(fake_redis=True)


def make_candles(n=60, base=95000):
    np.random.seed(42)
    candles = []
    price = base
    for i in range(n):
        o = price
        c = o + np.random.randn() * 50
        h = max(o, c) + abs(np.random.randn() * 20)
        l = min(o, c) - abs(np.random.randn() * 20)
        candles.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000, "time": i})
        price = c
    return candles


def test_compute_indicators(agent):
    candles = make_candles(60)
    ind = agent.compute_indicators(candles)
    assert "rsi" in ind
    assert "macd" in ind
    assert "bollinger" in ind
    assert "atr" in ind
    assert "vwap" in ind
    assert "stoch_rsi" in ind
    assert "obv" in ind
    assert "ema" in ind
    assert "momentum" in ind
    assert "divergence" in ind
    assert ind["atr"] > 0


def test_fallback_score(agent):
    candles = make_candles(60)
    ind = agent.compute_indicators(candles)
    result = agent._fallback_score(ind)
    assert 0 <= result["score"] <= 100
    assert result["direction"] in ("up", "down", "neutral")
    assert 0 <= result["confidence"] <= 1.0
    assert len(result["reasoning"]) > 0
