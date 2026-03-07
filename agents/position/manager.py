import time
from dataclasses import dataclass, field
from typing import Optional
from shared.redis_client import RedisClient


@dataclass
class Position:
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    stake: float
    atr: float
    stop_multiplier: float = 0.8
    stop_distance: float = 0.0
    stop_loss: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = float("inf")
    open_time: float = field(default_factory=time.time)
    reasoning: str = ""
    score: int = 50
    win_prob: float = 0.5

    def __post_init__(self):
        self.stop_distance = self.atr * self.stop_multiplier
        if self.direction == "LONG":
            self.stop_loss = self.entry_price - self.stop_distance
            self.highest_price = self.entry_price
        else:
            self.stop_loss = self.entry_price + self.stop_distance
            self.lowest_price = self.entry_price

    def unrealized_pnl(self, current_price: float) -> float:
        if self.direction == "LONG":
            pct = (current_price - self.entry_price) / self.entry_price
        else:
            pct = (self.entry_price - current_price) / self.entry_price
        return round(self.stake * pct * 50, 4)  # 50x leverage — aggressive

    def duration(self) -> float:
        return round(time.time() - self.open_time, 1)

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stake": self.stake,
            "stop_loss": round(self.stop_loss, 2),
            "highest_price": round(self.highest_price, 2),
            "lowest_price": round(self.lowest_price, 2) if self.lowest_price != float("inf") else 0,
            "duration": self.duration(),
            "reasoning": self.reasoning,
            "score": self.score,
            "win_prob": self.win_prob,
        }


class PositionManager:
    def __init__(self, redis_url: str = "redis://localhost:6379", fake_redis: bool = False):
        self.rc = RedisClient(url=redis_url, fake=fake_redis)
        self.position: Optional[Position] = None

    def open_position(self, direction: str, price: float, stake: float,
                      atr: float, reasoning: str = "", score: int = 50,
                      win_prob: float = 0.5, stop_multiplier: float = 0.8) -> Position:
        self.position = Position(
            direction=direction, entry_price=price, stake=stake, atr=atr,
            stop_multiplier=stop_multiplier,
            reasoning=reasoning, score=score, win_prob=win_prob,
        )
        return self.position

    def close_position(self, current_price: float, reason: str = "manual") -> dict:
        if not self.position:
            return {"action": "NONE"}
        pnl = self.position.unrealized_pnl(current_price)
        result = {
            "action": "CLOSED",
            "reason": reason,
            "direction": self.position.direction,
            "entry_price": self.position.entry_price,
            "exit_price": current_price,
            "stake": self.position.stake,
            "pnl": pnl,
            "won": pnl > 0,
            "duration": self.position.duration(),
            "reasoning": self.position.reasoning,
            "score": self.position.score,
        }
        self.position = None
        return result

    def update_tick(self, price: float) -> dict:
        if not self.position:
            return {"action": "NONE"}
        pos = self.position
        if pos.direction == "LONG":
            if price > pos.highest_price:
                pos.highest_price = price
                pos.stop_loss = max(pos.stop_loss, price - pos.stop_distance)
            if price <= pos.stop_loss:
                return {"action": "CLOSE", "reason": "trailing_stop",
                        "price": price, "stop": pos.stop_loss}
        else:
            if price < pos.lowest_price:
                pos.lowest_price = price
                pos.stop_loss = min(pos.stop_loss, price + pos.stop_distance)
            if price >= pos.stop_loss:
                return {"action": "CLOSE", "reason": "trailing_stop",
                        "price": price, "stop": pos.stop_loss}
        return {"action": "HOLD", "pnl": pos.unrealized_pnl(price),
                "stop_loss": round(pos.stop_loss, 2)}

    def check_signal_exit(self, signal: dict) -> dict:
        """Only exit on STRONG signal reversal — 3+ agents must disagree."""
        if not self.position:
            return {"action": "NONE"}
        pos = self.position
        sig_dir = signal.get("direction", "neutral")
        score = int(signal.get("score", 50))
        # Need a strong reversal (score < 30 or > 70) to override an open position
        if pos.direction == "LONG" and sig_dir in ("down", "SHORT") and score < 30:
            return {"action": "CLOSE", "reason": "strong_reversal"}
        if pos.direction == "SHORT" and sig_dir in ("up", "LONG") and score > 70:
            return {"action": "CLOSE", "reason": "strong_reversal"}
        return {"action": "HOLD"}

    def check_cooldown_review(self, current_price: float, signal: dict) -> dict:
        """Called when cooldown expires. Smart hold logic — stay in if loss is small."""
        if not self.position:
            return {"action": "NONE"}
        pnl = self.position.unrealized_pnl(current_price)
        loss_pct = abs(pnl) / self.position.stake if self.position.stake > 0 else 0
        sig_dir = signal.get("direction", "neutral")
        pos_dir = self.position.direction

        confirms = (
            (pos_dir == "LONG" and sig_dir in ("up", "neutral", "LONG")) or
            (pos_dir == "SHORT" and sig_dir in ("down", "neutral", "SHORT"))
        )
        contradicts = (
            (pos_dir == "LONG" and sig_dir in ("down", "SHORT")) or
            (pos_dir == "SHORT" and sig_dir in ("up", "LONG"))
        )

        # In profit → let the trailing stop handle exit, don't cut winners
        if pnl > 0:
            return {"action": "HOLD", "reason": "profitable", "pnl": pnl}

        # Small loss (< 30% of stake) + signals still OK → hold, give it room
        if loss_pct < 0.30 and not contradicts:
            return {"action": "HOLD", "reason": "small_loss_holding", "pnl": pnl}

        # Small loss but signals reversed → cut early
        if contradicts:
            return {"action": "CLOSE", "reason": "loss_signals_reversed", "pnl": pnl}

        # Big loss (≥ 30% of stake) → close regardless
        return {"action": "CLOSE", "reason": "stop_loss_pct", "pnl": pnl}

    @property
    def has_position(self) -> bool:
        return self.position is not None

    def get_state(self) -> Optional[dict]:
        if self.position:
            return self.position.to_dict()
        return None
