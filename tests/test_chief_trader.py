import pytest
from agents.chief_trader.agent import ChiefTrader


def test_fallback_bullish():
    ct = ChiefTrader(fake_redis=True)
    result = ct._fallback_decision(
        {"score": 72, "direction": "up", "atr": 150},
        {"score": 65, "direction": "up"},
        {"score": 60, "direction": "up"},
        {"score": 70, "direction": "up"},
    )
    assert result["direction"] == "LONG"
    assert result["confidence"] >= 0.7


def test_fallback_bearish():
    ct = ChiefTrader(fake_redis=True)
    result = ct._fallback_decision(
        {"score": 30, "direction": "down"},
        {"score": 35, "direction": "down"},
        {"score": 40, "direction": "down"},
        {"score": 32, "direction": "neutral"},
    )
    assert result["direction"] == "SHORT"


def test_circuit_breaker_losses():
    ct = ChiefTrader(fake_redis=True)
    ct.consecutive_losses = 5
    result = ct.check_circuit_breakers()
    assert result is not None
    assert result["action"] == "BLOCKED"


def test_circuit_breaker_drawdown():
    ct = ChiefTrader(fake_redis=True, bankroll=80)
    ct.initial = 100
    result = ct.check_circuit_breakers()
    assert result is not None
    assert "drawdown" in result["reason"]


def test_compute_trade():
    ct = ChiefTrader(fake_redis=True, bankroll=1000)
    decision = {"score": 72, "direction": "LONG", "confidence": 0.8, "reasoning": "test"}
    trade = ct.compute_trade(decision, atr=150)
    assert trade["action"] == "TRADE"
    assert trade["direction"] == "LONG"
    assert trade["stake"] > 0
    assert trade["trailing_stop_distance"] == 225.0


def test_cooldown_configurable():
    ct = ChiefTrader(fake_redis=True, cooldown=30)
    assert ct.cooldown == 30
    ct2 = ChiefTrader(fake_redis=True, cooldown=120)
    assert ct2.cooldown == 120
