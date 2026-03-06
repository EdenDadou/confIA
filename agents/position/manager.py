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
    stop_distance: float = 0.0
    stop_loss: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = float("inf")
    open_time: float = field(default_factory=time.time)
    reasoning: str = ""
    score: int = 50
    win_prob: float = 0.5

    def __post_init__(self):
        self.stop_distance = self.atr * 1.5
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
        return round(self.stake * pct * 10, 4)  # leveraged PnL

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
                      win_prob: float = 0.5) -> Position:
        self.position = Position(
            direction=direction, entry_price=price, stake=stake, atr=atr,
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
        if not self.position:
            return {"action": "NONE"}
        pos = self.position
        sig_dir = signal.get("direction", "neutral")
        score = int(signal.get("score", 50))
        if pos.direction == "LONG" and sig_dir in ("down", "SHORT") and score < 40:
            return {"action": "CLOSE", "reason": "signal_reversal"}
        if pos.direction == "SHORT" and sig_dir in ("up", "LONG") and score > 60:
            return {"action": "CLOSE", "reason": "signal_reversal"}
        return {"action": "HOLD"}

    def check_cooldown_review(self, current_price: float, signal: dict) -> dict:
        """Called when cooldown expires. Decide: keep open or close."""
        if not self.position:
            return {"action": "NONE"}
        pnl = self.position.unrealized_pnl(current_price)
        if pnl <= 0:
            return {"action": "CLOSE", "reason": "cooldown_losing", "pnl": pnl}
        sig_dir = signal.get("direction", "neutral")
        pos_dir = self.position.direction
        confirms = (
            (pos_dir == "LONG" and sig_dir in ("up", "neutral", "LONG")) or
            (pos_dir == "SHORT" and sig_dir in ("down", "neutral", "SHORT"))
        )
        if confirms:
            return {"action": "HOLD", "reason": "profitable_confirmed", "pnl": pnl}
        return {"action": "CLOSE", "reason": "cooldown_no_confirmation", "pnl": pnl}

    @property
    def has_position(self) -> bool:
        return self.position is not None

    def get_state(self) -> Optional[dict]:
        if self.position:
            return self.position.to_dict()
        return None
