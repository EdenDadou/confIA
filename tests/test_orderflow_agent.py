import pytest
from agents.orderflow.agent import OrderFlowAgent


def test_fallback_bid_heavy():
    agent = OrderFlowAgent(fake_redis=True)
    result = agent._fallback_score({"ob_ratio": 0.7, "funding_rate": 0.0001})
    assert result["score"] > 55


def test_fallback_ask_heavy():
    agent = OrderFlowAgent(fake_redis=True)
    result = agent._fallback_score({"ob_ratio": 0.3, "funding_rate": 0.0003})
    assert result["score"] < 45


def test_fallback_has_fields():
    agent = OrderFlowAgent(fake_redis=True)
    result = agent._fallback_score({"ob_ratio": 0.5, "funding_rate": 0.0001})
    assert "score" in result
    assert "direction" in result
    assert "confidence" in result
    assert "reasoning" in result
