from pydantic import BaseModel, field_validator
from decimal import Decimal
from datetime import datetime, timezone
from typing import Literal
import uuid


class Signal(BaseModel):
    id: str = None
    source: Literal["market", "sentiment", "onchain", "polymarket"]
    score: int  # 0-100
    direction: Literal["up", "down", "neutral"]
    confidence: float  # 0.0 - 1.0
    timestamp: datetime = None

    @field_validator("score")
    @classmethod
    def score_in_range(cls, v):
        if not 0 <= v <= 100:
            raise ValueError(f"score must be 0-100, got {v}")
        return v

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v):
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be 0.0-1.0, got {v}")
        return v

    def model_post_init(self, _):
        if self.id is None:
            self.id = str(uuid.uuid4())
        if self.timestamp is None:
            self.timestamp = datetime.now(tz=timezone.utc)


class Order(BaseModel):
    id: str = None
    market_id: str
    side: Literal["yes", "no"]
    amount_usdc: Decimal
    score: int
    created_at: datetime = None

    def model_post_init(self, _):
        if self.id is None:
            self.id = str(uuid.uuid4())
        if self.created_at is None:
            self.created_at = datetime.now(tz=timezone.utc)


class TradeResult(BaseModel):
    order_id: str
    filled: bool
    fill_price: Decimal | None = None
    error: str | None = None
    timestamp: datetime = None

    def model_post_init(self, _):
        if self.timestamp is None:
            self.timestamp = datetime.now(tz=timezone.utc)


class AgentHeartbeat(BaseModel):
    agent_name: str
    status: Literal["ok", "degraded", "error"]
    message: str = ""
    timestamp: datetime = None

    def model_post_init(self, _):
        if self.timestamp is None:
            self.timestamp = datetime.now(tz=timezone.utc)
