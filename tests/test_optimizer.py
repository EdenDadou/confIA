import pytest
from shared.optimizer import ParameterOptimizer


def test_initial_params():
    opt = ParameterOptimizer()
    params = opt.get_params()
    assert "min_quality" in params
    assert "stop_multiplier" in params
    assert "agent_weights" in params


def test_learn_from_winning_trades():
    opt = ParameterOptimizer()
    # Feed 10 winning trades with high quality setups
    for _ in range(10):
        opt.record_outcome(
            quality=0.75, direction="LONG", won=True, pnl=1.5,
            score=72, agent_agreement=3, stop_multiplier=0.8
        )
    params = opt.get_params()
    # After wins, optimizer should maintain or lower quality threshold
    assert params["min_quality"] <= 0.55


def test_learn_from_losing_trades():
    opt = ParameterOptimizer()
    # Feed 10 losing trades
    for _ in range(10):
        opt.record_outcome(
            quality=0.55, direction="LONG", won=False, pnl=-2.0,
            score=55, agent_agreement=2, stop_multiplier=0.8
        )
    params = opt.get_params()
    # After losses, should raise quality threshold (be more selective)
    assert params["min_quality"] > 0.55


def test_export_and_load():
    opt = ParameterOptimizer()
    for _ in range(5):
        opt.record_outcome(quality=0.7, direction="LONG", won=True, pnl=1.0,
                           score=70, agent_agreement=3, stop_multiplier=0.8)
    data = opt.export_state()
    opt2 = ParameterOptimizer()
    opt2.load_state(data)
    assert opt2.get_params() == opt.get_params()
