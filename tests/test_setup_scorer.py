from shared.setup_scorer import SetupScorer


def test_strong_setup_all_agree():
    scorer = SetupScorer()
    result = scorer.evaluate(
        tech={"score": 75, "direction": "up", "atr": 150, "confidence": 0.8},
        sent={"score": 70, "direction": "up", "confidence": 0.7},
        chain={"score": 68, "direction": "up", "confidence": 0.65},
        flow={"score": 72, "direction": "up", "confidence": 0.7},
    )
    assert result["should_trade"] is True
    assert result["quality"] >= 0.7
    assert result["direction"] == "LONG"


def test_weak_setup_agents_disagree():
    scorer = SetupScorer()
    result = scorer.evaluate(
        tech={"score": 55, "direction": "up", "atr": 150, "confidence": 0.5},
        sent={"score": 45, "direction": "down", "confidence": 0.5},
        chain={"score": 52, "direction": "neutral", "confidence": 0.4},
        flow={"score": 48, "direction": "down", "confidence": 0.45},
    )
    assert result["should_trade"] is False
    assert result["quality"] < 0.5


def test_strong_short_setup():
    scorer = SetupScorer()
    result = scorer.evaluate(
        tech={"score": 25, "direction": "down", "atr": 200, "confidence": 0.8},
        sent={"score": 30, "direction": "down", "confidence": 0.7},
        chain={"score": 28, "direction": "down", "confidence": 0.75},
        flow={"score": 35, "direction": "down", "confidence": 0.6},
    )
    assert result["should_trade"] is True
    assert result["direction"] == "SHORT"


def test_minimum_quality_threshold():
    scorer = SetupScorer(min_quality=0.6)
    result = scorer.evaluate(
        tech={"score": 60, "direction": "up", "atr": 100, "confidence": 0.55},
        sent={"score": 55, "direction": "neutral", "confidence": 0.5},
        chain={"score": 58, "direction": "up", "confidence": 0.5},
        flow={"score": 52, "direction": "neutral", "confidence": 0.45},
    )
    # Marginal setup — quality should be below 0.6 threshold
    assert result["should_trade"] is False


def test_cooldown_respected():
    scorer = SetupScorer(min_wait_seconds=300)
    result = scorer.evaluate(
        tech={"score": 80, "direction": "up", "atr": 150, "confidence": 0.9},
        sent={"score": 75, "direction": "up", "confidence": 0.8},
        chain={"score": 78, "direction": "up", "confidence": 0.85},
        flow={"score": 76, "direction": "up", "confidence": 0.8},
        seconds_since_last_trade=60,  # only 60s since last trade
    )
    assert result["should_trade"] is False
    assert result["wait_reason"] == "cooldown"
