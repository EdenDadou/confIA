import pytest
from agents.position.manager import PositionManager, Position


def test_open_long():
    pm = PositionManager(fake_redis=True)
    pos = pm.open_position("LONG", 95000, 5.0, atr=150)
    assert pos.direction == "LONG"
    assert pos.entry_price == 95000
    assert pos.stop_loss == 95000 - 150 * 1.5


def test_open_short():
    pm = PositionManager(fake_redis=True)
    pos = pm.open_position("SHORT", 95000, 5.0, atr=150)
    assert pos.stop_loss == 95000 + 150 * 1.5


def test_trailing_stop_long_moves_up():
    pm = PositionManager(fake_redis=True)
    pm.open_position("LONG", 95000, 5.0, atr=100)
    initial_stop = pm.position.stop_loss
    pm.update_tick(95200)
    assert pm.position.stop_loss > initial_stop
    assert pm.position.highest_price == 95200


def test_trailing_stop_long_doesnt_move_down():
    pm = PositionManager(fake_redis=True)
    pm.open_position("LONG", 95000, 5.0, atr=100)
    pm.update_tick(95200)
    stop_after_up = pm.position.stop_loss
    pm.update_tick(95100)
    assert pm.position.stop_loss == stop_after_up


def test_stop_loss_triggered_long():
    pm = PositionManager(fake_redis=True)
    pm.open_position("LONG", 95000, 5.0, atr=100)
    result = pm.update_tick(94800)
    assert result["action"] == "CLOSE"
    assert result["reason"] == "trailing_stop"


def test_signal_reversal_closes():
    pm = PositionManager(fake_redis=True)
    pm.open_position("LONG", 95000, 5.0, atr=100)
    result = pm.check_signal_exit({"direction": "down", "score": 35})
    assert result["action"] == "CLOSE"
    assert result["reason"] == "signal_reversal"


def test_position_stays_open_if_profitable_and_confirmed():
    pm = PositionManager(fake_redis=True)
    pm.open_position("LONG", 95000, 5.0, atr=100)
    pm.update_tick(95100)
    result = pm.check_cooldown_review(current_price=95100, signal={"direction": "up", "score": 65})
    assert result["action"] == "HOLD"


def test_position_closes_at_cooldown_if_losing():
    pm = PositionManager(fake_redis=True)
    pm.open_position("LONG", 95000, 5.0, atr=100)
    result = pm.check_cooldown_review(current_price=94950, signal={"direction": "up", "score": 65})
    assert result["action"] == "CLOSE"
    assert result["reason"] == "cooldown_losing"


def test_pnl_long():
    pm = PositionManager(fake_redis=True)
    pos = pm.open_position("LONG", 95000, 5.0, atr=100)
    pnl = pos.unrealized_pnl(95500)
    assert pnl > 0


def test_pnl_short():
    pm = PositionManager(fake_redis=True)
    pos = pm.open_position("SHORT", 95000, 5.0, atr=100)
    pnl = pos.unrealized_pnl(94500)
    assert pnl > 0


def test_close_position():
    pm = PositionManager(fake_redis=True)
    pm.open_position("LONG", 95000, 5.0, atr=100)
    result = pm.close_position(95500, reason="test")
    assert result["action"] == "CLOSED"
    assert result["won"] is True
    assert pm.position is None


def test_get_state():
    pm = PositionManager(fake_redis=True)
    assert pm.get_state() is None
    pm.open_position("LONG", 95000, 5.0, atr=100, reasoning="Test trade", score=72)
    state = pm.get_state()
    assert state["direction"] == "LONG"
    assert state["reasoning"] == "Test trade"
