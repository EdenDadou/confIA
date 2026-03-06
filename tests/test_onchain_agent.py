import pytest
from agents.onchain.agent import OnChainAgent


def test_fallback_bearish():
    agent = OnChainAgent(fake_redis=True)
    result = agent._fallback_score({
        "funding_rate": 0.0005,
        "long_short_ratio": 2.5,
    })
    assert result["score"] < 40
    assert result["direction"] == "down"


def test_fallback_bullish():
    agent = OnChainAgent(fake_redis=True)
    result = agent._fallback_score({
        "funding_rate": -0.0004,
        "long_short_ratio": 0.7,
    })
    assert result["score"] > 58
    assert result["direction"] == "up"


def test_fallback_neutral():
    agent = OnChainAgent(fake_redis=True)
    result = agent._fallback_score({
        "funding_rate": 0.0001,
        "long_short_ratio": 1.0,
    })
    assert 40 <= result["score"] <= 60


def test_fallback_has_reasoning():
    agent = OnChainAgent(fake_redis=True)
    result = agent._fallback_score({"funding_rate": 0.0001, "long_short_ratio": 1.0})
    assert "reasoning" in result
    assert len(result["reasoning"]) > 0
