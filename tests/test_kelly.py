from shared.kelly import half_kelly, bet_size


def test_half_kelly_positive_edge():
    k = half_kelly(0.7, 1.0)
    assert 0 < k < 0.5


def test_half_kelly_no_edge():
    k = half_kelly(0.5, 1.0)
    assert k == 0.0


def test_half_kelly_negative_edge():
    k = half_kelly(0.3, 1.0)
    assert k == 0.0


def test_bet_size_capped_at_5pct():
    size = bet_size(1000, 0.95)
    assert size <= 50.0


def test_bet_size_min_stake():
    size = bet_size(1000, 0.52)
    assert size >= 15.0
