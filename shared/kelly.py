def half_kelly(win_prob: float, odds: float = 1.0) -> float:
    k = (win_prob * odds - (1 - win_prob)) / odds
    return max(k / 2.0, 0.0)


def bet_size(bankroll: float, win_prob: float) -> float:
    kelly_stake = bankroll * half_kelly(win_prob)
    min_stake = round(bankroll * 0.015, 2)
    stake = max(kelly_stake, min_stake)
    stake = min(stake, round(bankroll * 0.05, 2))
    return round(stake, 2)
