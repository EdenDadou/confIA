from shared.leverage import compute_leverage

def test_high_confidence_low_vol():
    lev = compute_leverage(confidence=0.85, atr_pct=0.3, win_streak=3, max_leverage=50)
    assert lev >= 30  # high confidence -> higher leverage

def test_low_confidence_high_vol():
    lev = compute_leverage(confidence=0.55, atr_pct=1.5, win_streak=0, max_leverage=50)
    assert lev <= 15  # low confidence + high vol -> lower leverage

def test_never_exceeds_max():
    lev = compute_leverage(confidence=0.99, atr_pct=0.1, win_streak=10, max_leverage=50)
    assert lev <= 50

def test_minimum_leverage():
    lev = compute_leverage(confidence=0.50, atr_pct=3.0, win_streak=0, max_leverage=50)
    assert lev >= 5  # never go below 5x
