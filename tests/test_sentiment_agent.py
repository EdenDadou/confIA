import pytest
from agents.sentiment.agent import SentimentAgent


def test_fallback_extreme_fear():
    agent = SentimentAgent(fake_redis=True)
    result = agent._fallback_score(10)
    assert result["score"] > 60
    assert result["direction"] == "up"


def test_fallback_extreme_greed():
    agent = SentimentAgent(fake_redis=True)
    result = agent._fallback_score(90)
    assert result["score"] < 40
    assert result["direction"] == "down"


def test_fallback_neutral():
    agent = SentimentAgent(fake_redis=True)
    result = agent._fallback_score(50)
    assert 40 <= result["score"] <= 60


def test_fallback_has_reasoning():
    agent = SentimentAgent(fake_redis=True)
    result = agent._fallback_score(15)
    assert "reasoning" in result
    assert len(result["reasoning"]) > 0
