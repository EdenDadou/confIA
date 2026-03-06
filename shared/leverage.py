def compute_leverage(confidence: float, atr_pct: float, win_streak: int,
                     max_leverage: int = 50) -> int:
    """
    Dynamic leverage based on:
    - confidence (0-1): higher = more leverage
    - atr_pct: ATR as % of price (higher = more volatile = less leverage)
    - win_streak: consecutive wins boost leverage slightly
    """
    # Base leverage from confidence (5-40 range)
    base = 5 + (confidence - 0.5) * 70  # 0.5->5, 0.85->29.5, 1.0->40
    base = max(5, min(40, base))

    # Volatility discount: high ATR% -> reduce leverage
    if atr_pct > 1.0:
        vol_mult = 0.5
    elif atr_pct > 0.5:
        vol_mult = 0.75
    else:
        vol_mult = 1.0

    # Win streak bonus (max +10)
    streak_bonus = min(win_streak * 2, 10)

    leverage = int(base * vol_mult + streak_bonus)
    return max(5, min(leverage, max_leverage))
