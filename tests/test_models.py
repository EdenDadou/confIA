from shared.models import Signal, Order, TradeResult, AgentHeartbeat
from decimal import Decimal
import pytest


def test_signal_score_bounds():
    with pytest.raises(Exception):
        Signal(source="market", score=150, direction="up", confidence=0.8)


def test_signal_valid():
    s = Signal(source="market", score=75, direction="up", confidence=0.8)
    assert s.score == 75
    assert s.direction == "up"


def test_order_valid():
    o = Order(market_id="btc-100k-march", side="yes", amount_usdc=Decimal("10.50"), score=72)
    assert o.side == "yes"
    assert o.amount_usdc == Decimal("10.50")


def test_trade_result():
    r = TradeResult(order_id="abc123", filled=True, fill_price=Decimal("0.62"))
    assert r.filled is True


def test_heartbeat():
    h = AgentHeartbeat(agent_name="market", status="ok")
    assert h.status == "ok"
