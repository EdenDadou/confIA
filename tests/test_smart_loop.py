from shared.setup_scorer import SetupScorer
from shared.optimizer import ParameterOptimizer
from shared.leverage import compute_leverage


def test_smart_loop_integration():
    """Test the decision pipeline: scorer -> optimizer -> trade/skip."""
    scorer = SetupScorer(min_quality=0.55, min_wait_seconds=120)
    optimizer = ParameterOptimizer()

    # Strong setup — should trade
    result = scorer.evaluate(
        tech={"score": 75, "direction": "up", "atr": 150, "confidence": 0.8},
        sent={"score": 70, "direction": "up", "confidence": 0.7},
        chain={"score": 68, "direction": "up", "confidence": 0.65},
        flow={"score": 72, "direction": "up", "confidence": 0.7},
        seconds_since_last_trade=300,
    )
    assert result["should_trade"] is True

    # Compute leverage for this trade
    lev = compute_leverage(confidence=0.8, atr_pct=0.15, win_streak=2)
    assert 5 <= lev <= 50

    # Record outcome for optimizer
    optimizer.record_outcome(
        quality=result["quality"], direction=result["direction"],
        won=True, pnl=1.5, score=72, agent_agreement=result["agreement"],
        stop_multiplier=0.8,
    )
    params = optimizer.get_params()
    assert params["min_quality"] > 0


def test_weak_setup_rejected():
    scorer = SetupScorer(min_quality=0.55, min_wait_seconds=120)
    result = scorer.evaluate(
        tech={"score": 52, "direction": "neutral", "atr": 100, "confidence": 0.5},
        sent={"score": 48, "direction": "neutral", "confidence": 0.45},
        chain={"score": 50, "direction": "neutral", "confidence": 0.4},
        flow={"score": 51, "direction": "neutral", "confidence": 0.45},
        seconds_since_last_trade=300,
    )
    assert result["should_trade"] is False
