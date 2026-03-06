# Polymarket BTC Multi-Agent Trading System — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a fully automated 24/7 multi-agent system that bets on BTC price movements on Polymarket using financial signals, with Claude agents supervising system health.

**Architecture:** Parallel specialized data agents feed signals via Redis Streams to a Chief Trader agent that applies 1/2 Kelly sizing and dispatches orders to an Execution agent. Two Claude supervisor agents (Health Checker every 15min, Strategy Reviewer every 2h) monitor and auto-correct the system. Circuit breakers are hard-coded in the Execution agent and cannot be overridden by any agent.

**Tech Stack:** Python 3.12, Redis Streams, PostgreSQL, Docker Compose, Anthropic SDK (claude-sonnet-4-6), Pydantic v2, ccxt, websockets, prometheus-client, Grafana

---

## Phase 1 — Fondations

---

### Task 1: Initialiser le projet Python

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`

**Step 1: Créer pyproject.toml**

```toml
[project]
name = "trade"
version = "0.1.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

**Step 2: Créer requirements.txt**

```
pydantic==2.7.1
redis==5.0.4
asyncpg==0.29.0
websockets==12.0
ccxt==4.3.17
anthropic==0.28.0
prometheus-client==0.20.0
python-dotenv==1.0.1
httpx==0.27.0
pandas==2.2.2
numpy==1.26.4
ta==0.11.0
pytest==8.2.0
pytest-asyncio==0.23.7
```

**Step 3: Créer .env.example**

```
BINANCE_API_KEY=
BINANCE_API_SECRET=
POLYMARKET_API_KEY=
POLYMARKET_PRIVATE_KEY=
ANTHROPIC_API_KEY=
COINMARKETCAP_API_KEY=
SANTIMENT_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
REDIS_URL=redis://redis:6379
DATABASE_URL=postgresql://trader:trader@postgres:5432/trade
BANKROLL_USDC=1000
PAPER_TRADING=true
```

**Step 4: Créer .gitignore**

```
.env
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
dist/
.ruff_cache/
```

**Step 5: Installer les dépendances**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Step 6: Commit**

```bash
git init
git add pyproject.toml requirements.txt .env.example .gitignore
git commit -m "chore: init project structure"
```

---

### Task 2: Modèles Pydantic partagés

**Files:**
- Create: `shared/__init__.py`
- Create: `shared/models.py`
- Create: `tests/__init__.py`
- Create: `tests/test_models.py`

**Step 1: Écrire les tests**

```python
# tests/test_models.py
from shared.models import Signal, Order, TradeResult, AgentHeartbeat
from decimal import Decimal
import pytest

def test_signal_score_bounds():
    with pytest.raises(Exception):
        Signal(source="market", score=150, direction="up", confidence=0.8)

def test_signal_valid():
    s = Signal(source="market", score=75, direction="up", confidence=0.8)
    assert s.score == 75
    assert s.direction == "up"

def test_order_valid():
    o = Order(market_id="btc-100k-march", side="yes", amount_usdc=Decimal("10.50"), score=72)
    assert o.side == "yes"
    assert o.amount_usdc == Decimal("10.50")

def test_trade_result():
    r = TradeResult(order_id="abc123", filled=True, fill_price=Decimal("0.62"))
    assert r.filled is True

def test_heartbeat():
    h = AgentHeartbeat(agent_name="market", status="ok")
    assert h.status == "ok"
```

**Step 2: Vérifier que les tests échouent**

```bash
pytest tests/test_models.py -v
```
Expected: FAIL — `shared.models` not found

**Step 3: Implémenter les modèles**

```python
# shared/__init__.py
# (vide)

# shared/models.py
from pydantic import BaseModel, field_validator
from decimal import Decimal
from datetime import datetime
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
            self.timestamp = datetime.utcnow()

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
            self.created_at = datetime.utcnow()

class TradeResult(BaseModel):
    order_id: str
    filled: bool
    fill_price: Decimal | None = None
    error: str | None = None
    timestamp: datetime = None

    def model_post_init(self, _):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()

class AgentHeartbeat(BaseModel):
    agent_name: str
    status: Literal["ok", "degraded", "error"]
    message: str = ""
    timestamp: datetime = None

    def model_post_init(self, _):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
```

**Step 4: Vérifier que les tests passent**

```bash
pytest tests/test_models.py -v
```
Expected: 5 PASSED

**Step 5: Commit**

```bash
git add shared/ tests/
git commit -m "feat: add shared Pydantic models (Signal, Order, TradeResult, Heartbeat)"
```

---

### Task 3: Client Redis partagé

**Files:**
- Create: `shared/redis_client.py`
- Create: `tests/test_redis_client.py`

**Step 1: Écrire les tests (nécessite Redis running)**

```python
# tests/test_redis_client.py
import pytest
import asyncio
from shared.redis_client import RedisClient
from shared.models import Signal

@pytest.mark.asyncio
async def test_publish_and_read_signal():
    client = RedisClient("redis://localhost:6379")
    await client.connect()

    signal = Signal(source="market", score=70, direction="up", confidence=0.75)
    await client.publish_signal(signal)

    messages = await client.read_signals(stream="signals:market", count=1)
    assert len(messages) == 1
    assert messages[0]["score"] == "70"

    await client.disconnect()

@pytest.mark.asyncio
async def test_heartbeat_roundtrip():
    from shared.models import AgentHeartbeat
    client = RedisClient("redis://localhost:6379")
    await client.connect()

    hb = AgentHeartbeat(agent_name="test_agent", status="ok")
    await client.publish_heartbeat(hb)

    result = await client.get_last_heartbeat("test_agent")
    assert result["status"] == "ok"

    await client.disconnect()
```

**Step 2: Lancer Redis en local pour les tests**

```bash
docker run -d --name redis-test -p 6379:6379 redis:7-alpine
```

**Step 3: Vérifier que les tests échouent**

```bash
pytest tests/test_redis_client.py -v
```
Expected: FAIL — `shared.redis_client` not found

**Step 4: Implémenter le client**

```python
# shared/redis_client.py
import redis.asyncio as aioredis
import json
from shared.models import Signal, AgentHeartbeat

STREAM_MAP = {
    "market": "signals:market",
    "sentiment": "signals:sentiment",
    "onchain": "signals:onchain",
    "polymarket": "signals:polymarket",
    "orders": "orders",
    "incidents": "incidents",
}

class RedisClient:
    def __init__(self, url: str):
        self.url = url
        self._redis = None

    async def connect(self):
        self._redis = await aioredis.from_url(self.url, decode_responses=True)

    async def disconnect(self):
        if self._redis:
            await self._redis.aclose()

    async def publish_signal(self, signal: Signal):
        stream = STREAM_MAP[signal.source]
        await self._redis.xadd(stream, signal.model_dump(mode="json"))

    async def read_signals(self, stream: str, count: int = 10, last_id: str = "0") -> list[dict]:
        result = await self._redis.xread({stream: last_id}, count=count, block=100)
        if not result:
            return []
        _, messages = result[0]
        return [data for _, data in messages]

    async def publish_heartbeat(self, hb: AgentHeartbeat):
        key = f"heartbeat:{hb.agent_name}"
        await self._redis.set(key, json.dumps(hb.model_dump(mode="json")), ex=60)

    async def get_last_heartbeat(self, agent_name: str) -> dict | None:
        key = f"heartbeat:{agent_name}"
        data = await self._redis.get(key)
        return json.loads(data) if data else None

    async def publish_order(self, order_dict: dict):
        await self._redis.xadd(STREAM_MAP["orders"], order_dict)

    async def publish_incident(self, incident: dict):
        await self._redis.xadd(STREAM_MAP["incidents"], incident)
```

**Step 5: Vérifier que les tests passent**

```bash
pytest tests/test_redis_client.py -v
```
Expected: 2 PASSED

**Step 6: Commit**

```bash
git add shared/redis_client.py tests/test_redis_client.py
git commit -m "feat: add async Redis client with signal/heartbeat pub-sub"
```

---

### Task 4: Calculateur 1/2 Kelly

**Files:**
- Create: `shared/kelly.py`
- Create: `tests/test_kelly.py`

**Step 1: Écrire les tests**

```python
# tests/test_kelly.py
from shared.kelly import half_kelly_fraction, compute_bet_size
from decimal import Decimal
import pytest

def test_kelly_positive_edge():
    # win_prob=0.60, odds=1.0 (bet $1 to win $1) => Kelly = 0.20, half=0.10
    fraction = half_kelly_fraction(win_prob=0.60, odds=1.0)
    assert abs(fraction - 0.10) < 0.001

def test_kelly_no_edge():
    # win_prob=0.50, odds=1.0 => Kelly = 0, no bet
    fraction = half_kelly_fraction(win_prob=0.50, odds=1.0)
    assert fraction == 0.0

def test_kelly_negative_edge_returns_zero():
    fraction = half_kelly_fraction(win_prob=0.40, odds=1.0)
    assert fraction == 0.0

def test_bet_size_capped_at_5_percent():
    # Even with huge edge, max bet is 5% of bankroll
    size = compute_bet_size(bankroll=Decimal("1000"), win_prob=0.99, odds=1.0)
    assert size <= Decimal("50.00")

def test_bet_size_normal():
    size = compute_bet_size(bankroll=Decimal("1000"), win_prob=0.62, odds=1.0)
    assert Decimal("0") < size <= Decimal("50")

def test_bet_size_rounds_to_cents():
    size = compute_bet_size(bankroll=Decimal("1000"), win_prob=0.62, odds=1.0)
    assert size == round(size, 2)
```

**Step 2: Vérifier que les tests échouent**

```bash
pytest tests/test_kelly.py -v
```
Expected: FAIL

**Step 3: Implémenter Kelly**

```python
# shared/kelly.py
from decimal import Decimal, ROUND_DOWN

MAX_BET_FRACTION = Decimal("0.05")  # 5% max du bankroll

def half_kelly_fraction(win_prob: float, odds: float) -> float:
    """
    Kelly Criterion fractionné à 1/2.
    odds = profit net par mise unitaire (ex: odds=1.0 = "doubler sa mise")
    Retourne la fraction du bankroll à miser (0.0 si pas d'edge).
    """
    if win_prob <= 0 or odds <= 0:
        return 0.0
    lose_prob = 1.0 - win_prob
    kelly = (win_prob * odds - lose_prob) / odds
    if kelly <= 0:
        return 0.0
    return kelly / 2  # 1/2 Kelly

def compute_bet_size(bankroll: Decimal, win_prob: float, odds: float) -> Decimal:
    """
    Retourne la mise en USDC, plafonnée à MAX_BET_FRACTION du bankroll.
    """
    fraction = half_kelly_fraction(win_prob, odds)
    if fraction <= 0:
        return Decimal("0.00")
    raw = bankroll * Decimal(str(fraction))
    capped = min(raw, bankroll * MAX_BET_FRACTION)
    return capped.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
```

**Step 4: Vérifier que les tests passent**

```bash
pytest tests/test_kelly.py -v
```
Expected: 6 PASSED

**Step 5: Commit**

```bash
git add shared/kelly.py tests/test_kelly.py
git commit -m "feat: add 1/2 Kelly bet sizing with 5% bankroll cap"
```

---

### Task 5: Infrastructure Docker Compose

**Files:**
- Create: `infra/docker-compose.yml`
- Create: `infra/postgres/init.sql`
- Create: `infra/prometheus/prometheus.yml`

**Step 1: Créer docker-compose.yml**

```yaml
# infra/docker-compose.yml
version: "3.9"

services:
  redis:
    image: redis:7-alpine
    restart: always
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3

  postgres:
    image: postgres:16-alpine
    restart: always
    environment:
      POSTGRES_USER: trader
      POSTGRES_PASSWORD: trader
      POSTGRES_DB: trade
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U trader"]
      interval: 10s
      timeout: 5s
      retries: 5

  prometheus:
    image: prom/prometheus:v2.51.0
    restart: always
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:10.4.0
    restart: always
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
    ports:
      - "3000:3000"
    depends_on:
      - prometheus

volumes:
  postgres_data:
```

**Step 2: Créer le schéma PostgreSQL**

```sql
-- infra/postgres/init.sql

CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY,
    source VARCHAR(20) NOT NULL,
    score INTEGER NOT NULL,
    direction VARCHAR(10) NOT NULL,
    confidence FLOAT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY,
    market_id VARCHAR(200) NOT NULL,
    side VARCHAR(5) NOT NULL,
    amount_usdc NUMERIC(12, 2) NOT NULL,
    score INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trade_results (
    id SERIAL PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES orders(id),
    filled BOOLEAN NOT NULL,
    fill_price NUMERIC(12, 6),
    error TEXT,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS incidents (
    id SERIAL PRIMARY KEY,
    severity VARCHAR(10) NOT NULL,  -- WARNING / CRITICAL
    agent VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    resolved BOOLEAN NOT NULL DEFAULT FALSE,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS strategy_reports (
    id SERIAL PRIMARY KEY,
    report JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_signals_timestamp ON signals(timestamp DESC);
CREATE INDEX idx_trade_results_order_id ON trade_results(order_id);
CREATE INDEX idx_incidents_resolved ON incidents(resolved, timestamp DESC);
```

**Step 3: Créer prometheus.yml**

```yaml
# infra/prometheus/prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: "agents"
    static_configs:
      - targets:
          - "agent-market:8000"
          - "agent-sentiment:8001"
          - "agent-onchain:8002"
          - "agent-polymarket:8003"
          - "agent-chief-trader:8004"
          - "agent-execution:8005"
```

**Step 4: Démarrer l'infrastructure**

```bash
cd infra && docker compose up -d redis postgres
docker compose ps
```
Expected: redis et postgres à l'état "healthy"

**Step 5: Commit**

```bash
git add infra/
git commit -m "feat: add Docker Compose infra (Redis, Postgres, Prometheus, Grafana)"
```

---

## Phase 2 — Agents Data

---

### Task 6: Agent Market (Binance WebSocket + indicateurs)

**Files:**
- Create: `agents/market/__init__.py`
- Create: `agents/market/indicators.py`
- Create: `agents/market/agent.py`
- Create: `tests/agents/test_market_indicators.py`

**Step 1: Écrire les tests des indicateurs**

```python
# tests/agents/test_market_indicators.py
import pandas as pd
import numpy as np
from agents.market.indicators import compute_indicators, score_from_indicators

def make_ohlcv(n=50, trend="up"):
    """Génère des données OHLCV synthétiques."""
    base = 50000.0
    closes = []
    for i in range(n):
        if trend == "up":
            closes.append(base + i * 100 + np.random.normal(0, 50))
        else:
            closes.append(base - i * 100 + np.random.normal(0, 50))
    df = pd.DataFrame({
        "open": closes,
        "high": [c + 100 for c in closes],
        "low": [c - 100 for c in closes],
        "close": closes,
        "volume": [1000.0 + np.random.normal(0, 100) for _ in closes],
    })
    return df

def test_compute_indicators_returns_expected_columns():
    df = make_ohlcv()
    result = compute_indicators(df)
    for col in ["rsi", "macd", "macd_signal", "bb_upper", "bb_lower", "vwap"]:
        assert col in result.columns, f"Missing column: {col}"

def test_score_uptrend_is_above_50():
    df = make_ohlcv(trend="up")
    df = compute_indicators(df)
    score = score_from_indicators(df.iloc[-1])
    assert score > 50

def test_score_downtrend_is_below_50():
    df = make_ohlcv(trend="down")
    df = compute_indicators(df)
    score = score_from_indicators(df.iloc[-1])
    assert score < 50

def test_score_is_in_range():
    df = make_ohlcv()
    df = compute_indicators(df)
    score = score_from_indicators(df.iloc[-1])
    assert 0 <= score <= 100
```

**Step 2: Vérifier que les tests échouent**

```bash
pytest tests/agents/test_market_indicators.py -v
```
Expected: FAIL

**Step 3: Implémenter les indicateurs**

```python
# agents/market/__init__.py
# (vide)

# agents/market/indicators.py
import pandas as pd
import numpy as np
import ta

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calcule RSI, MACD, Bollinger Bands, VWAP sur un DataFrame OHLCV."""
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # RSI
    df["rsi"] = ta.momentum.RSIIndicator(close, window=14).rsi()

    # MACD
    macd = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    # Bollinger Bands
    bb = ta.volatility.BollingerBands(close, window=20)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"] = bb.bollinger_mavg()

    # VWAP (approximation sur la fenêtre disponible)
    typical_price = (high + low + close) / 3
    df["vwap"] = (typical_price * volume).cumsum() / volume.cumsum()

    return df.dropna()

def score_from_indicators(row: pd.Series) -> int:
    """
    Convertit les indicateurs de la dernière bougie en score 0-100.
    Score > 50 = signal haussier, < 50 = signal baissier.
    """
    points = 0.0
    total_weight = 0.0

    # RSI (poids 0.35)
    if pd.notna(row.get("rsi")):
        rsi = row["rsi"]
        if rsi > 70:
            rsi_score = 20  # overbought = bearish
        elif rsi > 55:
            rsi_score = 65
        elif rsi > 45:
            rsi_score = 50
        elif rsi > 30:
            rsi_score = 35
        else:
            rsi_score = 80  # oversold = bullish reversal
        points += 0.35 * rsi_score
        total_weight += 0.35

    # MACD vs signal (poids 0.35)
    if pd.notna(row.get("macd")) and pd.notna(row.get("macd_signal")):
        macd_score = 70 if row["macd"] > row["macd_signal"] else 30
        points += 0.35 * macd_score
        total_weight += 0.35

    # Prix vs Bollinger (poids 0.30)
    if pd.notna(row.get("bb_upper")) and pd.notna(row.get("bb_lower")):
        close = row["close"]
        bb_range = row["bb_upper"] - row["bb_lower"]
        if bb_range > 0:
            position = (close - row["bb_lower"]) / bb_range
            bb_score = int(position * 100)
            bb_score = max(0, min(100, bb_score))
        else:
            bb_score = 50
        points += 0.30 * bb_score
        total_weight += 0.30

    if total_weight == 0:
        return 50
    return int(points / total_weight)
```

**Step 4: Vérifier que les tests passent**

```bash
pytest tests/agents/test_market_indicators.py -v
```
Expected: 4 PASSED

**Step 5: Implémenter l'agent principal**

```python
# agents/market/agent.py
import asyncio
import json
import os
import websockets
import pandas as pd
from collections import deque
from datetime import datetime
from shared.models import Signal, AgentHeartbeat
from shared.redis_client import RedisClient
from agents.market.indicators import compute_indicators, score_from_indicators
from prometheus_client import start_http_server, Gauge

SCORE_GAUGE = Gauge("market_signal_score", "Current market signal score (0-100)")

class MarketAgent:
    BINANCE_WS = "wss://stream.binance.com:9443/ws/btcusdt@kline_5m"
    BUFFER_SIZE = 60  # 60 bougies en mémoire

    def __init__(self, redis_url: str):
        self.redis = RedisClient(redis_url)
        self.candles: deque = deque(maxlen=self.BUFFER_SIZE)

    async def run(self):
        await self.redis.connect()
        start_http_server(8000)
        print("[MarketAgent] Connecting to Binance WS...")
        async for ws in websockets.connect(self.BINANCE_WS, ping_interval=20):
            try:
                async for message in ws:
                    await self._process_message(json.loads(message))
            except websockets.ConnectionClosed:
                print("[MarketAgent] WS disconnected, reconnecting...")
                await asyncio.sleep(2)

    async def _process_message(self, msg: dict):
        kline = msg.get("k", {})
        if not kline.get("x"):  # x=True seulement quand la bougie est fermée
            return

        candle = {
            "open": float(kline["o"]),
            "high": float(kline["h"]),
            "low": float(kline["l"]),
            "close": float(kline["c"]),
            "volume": float(kline["v"]),
        }
        self.candles.append(candle)

        if len(self.candles) < 30:
            return  # Pas assez de données

        df = pd.DataFrame(list(self.candles))
        df = compute_indicators(df)
        if df.empty:
            return

        score = score_from_indicators(df.iloc[-1])
        SCORE_GAUGE.set(score)

        direction = "up" if score > 55 else "down" if score < 45 else "neutral"
        signal = Signal(
            source="market",
            score=score,
            direction=direction,
            confidence=abs(score - 50) / 50,
        )
        await self.redis.publish_signal(signal)

        hb = AgentHeartbeat(agent_name="market", status="ok", message=f"score={score}")
        await self.redis.publish_heartbeat(hb)

if __name__ == "__main__":
    agent = MarketAgent(os.getenv("REDIS_URL", "redis://localhost:6379"))
    asyncio.run(agent.run())
```

**Step 6: Commit**

```bash
git add agents/market/ tests/agents/
git commit -m "feat: add Market agent with Binance WS and technical indicators"
```

---

### Task 7: Agent Polymarket Monitor

**Files:**
- Create: `agents/polymarket/__init__.py`
- Create: `agents/polymarket/client.py`
- Create: `agents/polymarket/agent.py`
- Create: `tests/agents/test_polymarket_client.py`

**Step 1: Écrire les tests**

```python
# tests/agents/test_polymarket_client.py
import pytest
from unittest.mock import AsyncMock, patch
from agents.polymarket.client import PolymarketClient

@pytest.mark.asyncio
async def test_get_btc_markets_returns_list():
    client = PolymarketClient(api_key="fake")
    mock_response = {
        "data": [
            {"id": "m1", "question": "Will BTC be above $100k on March 6?",
             "outcomePrices": ["0.62", "0.38"], "volume": "50000"},
            {"id": "m2", "question": "Will ETH be above $5k?",
             "outcomePrices": ["0.40", "0.60"], "volume": "10000"},
        ]
    }
    with patch.object(client, "_get", new=AsyncMock(return_value=mock_response)):
        markets = await client.get_btc_markets()
    assert len(markets) == 1  # Seulement le marché BTC
    assert markets[0]["id"] == "m1"

@pytest.mark.asyncio
async def test_score_market_high_volume_move():
    client = PolymarketClient(api_key="fake")
    # Prix passe de 0.50 à 0.65 = gros mouvement
    score = client.score_market(
        current_yes_price=0.65,
        prev_yes_price=0.50,
        volume=80000,
        liquidity=20000
    )
    assert score > 65

@pytest.mark.asyncio
async def test_score_market_no_move():
    client = PolymarketClient(api_key="fake")
    score = client.score_market(
        current_yes_price=0.50,
        prev_yes_price=0.50,
        volume=5000,
        liquidity=1000
    )
    assert score < 50
```

**Step 2: Vérifier que les tests échouent**

```bash
pytest tests/agents/test_polymarket_client.py -v
```
Expected: FAIL

**Step 3: Implémenter le client Polymarket**

```python
# agents/polymarket/__init__.py
# (vide)

# agents/polymarket/client.py
import httpx
import asyncio
from typing import Any

POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_API = "https://clob.polymarket.com"

class PolymarketClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._http = httpx.AsyncClient(timeout=10.0)

    async def _get(self, url: str, params: dict = None) -> dict:
        resp = await self._http.get(url, params=params or {})
        resp.raise_for_status()
        return resp.json()

    async def get_btc_markets(self) -> list[dict]:
        """Retourne les marchés actifs liés au BTC."""
        data = await self._get(f"{POLYMARKET_GAMMA_API}/markets", params={
            "active": "true",
            "closed": "false",
            "limit": 100,
        })
        markets = data.get("data", []) if isinstance(data, dict) else data
        return [
            m for m in markets
            if "btc" in m.get("question", "").lower()
            or "bitcoin" in m.get("question", "").lower()
        ]

    def score_market(
        self,
        current_yes_price: float,
        prev_yes_price: float,
        volume: float,
        liquidity: float,
    ) -> int:
        """
        Score 0-100 basé sur :
        - Amplitude du mouvement de prix (signale information)
        - Volume relatif
        - Liquidité (marchés trop peu liquides = spread dangereux)
        """
        score = 50

        # Mouvement de prix (poids 0.50)
        price_move = abs(current_yes_price - prev_yes_price)
        move_score = min(int(price_move * 500), 50)  # max 50 points
        score += move_score if current_yes_price > prev_yes_price else -move_score

        # Volume (poids 0.30) — normalise autour de 50k USDC
        vol_score = min(int((volume / 50000) * 30), 30)
        score += vol_score - 15  # -15 si volume nul, +15 si volume moyen

        # Liquidité (poids 0.20) — pénalise si < 5000 USDC
        if liquidity < 5000:
            score -= 20
        elif liquidity > 20000:
            score += 10

        return max(0, min(100, score))

    async def close(self):
        await self._http.aclose()
```

**Step 4: Implémenter l'agent**

```python
# agents/polymarket/agent.py
import asyncio
import os
from shared.models import Signal, AgentHeartbeat
from shared.redis_client import RedisClient
from agents.polymarket.client import PolymarketClient
from prometheus_client import start_http_server, Gauge

SCORE_GAUGE = Gauge("polymarket_signal_score", "Current Polymarket signal score (0-100)")

class PolymarketAgent:
    POLL_INTERVAL = 30  # secondes

    def __init__(self, redis_url: str, api_key: str):
        self.redis = RedisClient(redis_url)
        self.client = PolymarketClient(api_key)
        self._prev_prices: dict[str, float] = {}

    async def run(self):
        await self.redis.connect()
        start_http_server(8003)
        print("[PolymarketAgent] Starting market monitor...")
        while True:
            try:
                await self._poll()
            except Exception as e:
                print(f"[PolymarketAgent] Error: {e}")
                hb = AgentHeartbeat(agent_name="polymarket", status="error", message=str(e))
                await self.redis.publish_heartbeat(hb)
            await asyncio.sleep(self.POLL_INTERVAL)

    async def _poll(self):
        markets = await self.client.get_btc_markets()
        if not markets:
            return

        scores = []
        for market in markets:
            market_id = market["id"]
            prices = market.get("outcomePrices", ["0.5", "0.5"])
            yes_price = float(prices[0])
            prev_price = self._prev_prices.get(market_id, yes_price)
            volume = float(market.get("volume", 0))
            liquidity = float(market.get("liquidity", 0))

            score = self.client.score_market(yes_price, prev_price, volume, liquidity)
            scores.append(score)
            self._prev_prices[market_id] = yes_price

        avg_score = int(sum(scores) / len(scores))
        SCORE_GAUGE.set(avg_score)

        direction = "up" if avg_score > 55 else "down" if avg_score < 45 else "neutral"
        signal = Signal(
            source="polymarket",
            score=avg_score,
            direction=direction,
            confidence=abs(avg_score - 50) / 50,
        )
        await self.redis.publish_signal(signal)

        hb = AgentHeartbeat(agent_name="polymarket", status="ok", message=f"markets={len(markets)}")
        await self.redis.publish_heartbeat(hb)

if __name__ == "__main__":
    agent = PolymarketAgent(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        api_key=os.getenv("POLYMARKET_API_KEY", ""),
    )
    asyncio.run(agent.run())
```

**Step 5: Vérifier que les tests passent**

```bash
pytest tests/agents/test_polymarket_client.py -v
```
Expected: 3 PASSED

**Step 6: Commit**

```bash
git add agents/polymarket/ tests/agents/test_polymarket_client.py
git commit -m "feat: add Polymarket monitor agent with odds scoring"
```

---

## Phase 3 — Décision

---

### Task 8: Chief Trader Agent

**Files:**
- Create: `agents/chief_trader/__init__.py`
- Create: `agents/chief_trader/aggregator.py`
- Create: `agents/chief_trader/agent.py`
- Create: `tests/agents/test_aggregator.py`

**Step 1: Écrire les tests**

```python
# tests/agents/test_aggregator.py
from agents.chief_trader.aggregator import aggregate_signals, should_trade
from shared.models import Signal
from decimal import Decimal

def make_signal(source, score, direction="up"):
    return Signal(source=source, score=score, direction=direction, confidence=abs(score-50)/50)

def test_aggregate_all_bullish():
    signals = [
        make_signal("market", 80),
        make_signal("sentiment", 70),
        make_signal("onchain", 75),
        make_signal("polymarket", 78),
    ]
    result = aggregate_signals(signals)
    assert result["score"] > 65
    assert result["direction"] == "up"

def test_aggregate_mixed_no_trade():
    signals = [
        make_signal("market", 70, "up"),
        make_signal("sentiment", 30, "down"),
        make_signal("onchain", 50, "neutral"),
        make_signal("polymarket", 40, "down"),
    ]
    result = aggregate_signals(signals)
    assert not should_trade(result)

def test_aggregate_missing_source_uses_neutral():
    # Seulement 2 sources disponibles
    signals = [
        make_signal("market", 80),
        make_signal("polymarket", 75),
    ]
    result = aggregate_signals(signals)
    assert result["score"] > 50  # Les neutres (50) drainent le score

def test_should_trade_above_threshold():
    result = {"score": 70, "direction": "up", "win_prob": 0.70}
    assert should_trade(result) is True

def test_should_not_trade_below_threshold():
    result = {"score": 60, "direction": "up", "win_prob": 0.60}
    assert should_trade(result) is False
```

**Step 2: Vérifier que les tests échouent**

```bash
pytest tests/agents/test_aggregator.py -v
```
Expected: FAIL

**Step 3: Implémenter l'agrégateur**

```python
# agents/chief_trader/__init__.py
# (vide)

# agents/chief_trader/aggregator.py
from shared.models import Signal

WEIGHTS = {
    "market": 0.30,
    "sentiment": 0.20,
    "onchain": 0.20,
    "polymarket": 0.30,
}
MIN_SCORE_TO_TRADE = 65

def aggregate_signals(signals: list[Signal]) -> dict:
    """
    Agrège les signaux de toutes les sources avec pondération fixe.
    Les sources manquantes contribuent avec un score neutre (50).
    """
    scores_by_source = {s.source: s.score for s in signals}
    weighted_sum = 0.0
    for source, weight in WEIGHTS.items():
        weighted_sum += weight * scores_by_source.get(source, 50)

    final_score = int(weighted_sum)
    direction = "up" if final_score > 55 else "down" if final_score < 45 else "neutral"
    win_prob = 0.50 + (final_score - 50) / 200  # linéaire : score=70 → win_prob=0.60

    return {
        "score": final_score,
        "direction": direction,
        "win_prob": float(win_prob),
        "sources_used": list(scores_by_source.keys()),
    }

def should_trade(aggregated: dict) -> bool:
    return (
        aggregated["score"] >= MIN_SCORE_TO_TRADE
        and aggregated["direction"] != "neutral"
    )
```

**Step 4: Implémenter l'agent**

```python
# agents/chief_trader/agent.py
import asyncio
import os
from decimal import Decimal
from shared.models import Signal, Order, AgentHeartbeat
from shared.redis_client import RedisClient, STREAM_MAP
from shared.kelly import compute_bet_size
from agents.chief_trader.aggregator import aggregate_signals, should_trade
from prometheus_client import start_http_server, Gauge, Counter

SCORE_GAUGE = Gauge("chief_trader_score", "Aggregated signal score")
TRADES_COUNTER = Counter("chief_trader_trades_total", "Total trade orders emitted")

class ChiefTraderAgent:
    SIGNAL_WINDOW = 60  # secondes — fenêtre pour collecter les signaux

    def __init__(self, redis_url: str, bankroll: Decimal):
        self.redis = RedisClient(redis_url)
        self.bankroll = bankroll
        self._last_signals: dict[str, Signal] = {}
        self._stream_offsets: dict[str, str] = {
            "signals:market": "0",
            "signals:sentiment": "0",
            "signals:onchain": "0",
            "signals:polymarket": "0",
        }

    async def run(self):
        await self.redis.connect()
        start_http_server(8004)
        print("[ChiefTrader] Starting aggregation loop...")
        while True:
            await self._collect_signals()
            await self._evaluate_and_order()
            await asyncio.sleep(30)  # Décision toutes les 30s

    async def _collect_signals(self):
        for stream in self._stream_offsets:
            messages = await self.redis.read_signals(
                stream=stream,
                count=5,
                last_id=self._stream_offsets[stream],
            )
            for msg in messages:
                try:
                    signal = Signal(**msg)
                    self._last_signals[signal.source] = signal
                except Exception as e:
                    print(f"[ChiefTrader] Invalid signal from {stream}: {e}")

    async def _evaluate_and_order(self):
        if not self._last_signals:
            return

        signals = list(self._last_signals.values())
        result = aggregate_signals(signals)
        SCORE_GAUGE.set(result["score"])

        hb = AgentHeartbeat(
            agent_name="chief_trader",
            status="ok",
            message=f"score={result['score']} sources={result['sources_used']}"
        )
        await self.redis.publish_heartbeat(hb)

        if not should_trade(result):
            return

        bet = compute_bet_size(
            bankroll=self.bankroll,
            win_prob=result["win_prob"],
            odds=1.0,
        )
        if bet <= 0:
            return

        # Sélectionne le premier marché disponible (l'Execution agent choisit le meilleur)
        order = Order(
            market_id="__best_available__",
            side="yes" if result["direction"] == "up" else "no",
            amount_usdc=bet,
            score=result["score"],
        )
        await self.redis.publish_order(order.model_dump(mode="json"))
        TRADES_COUNTER.inc()
        print(f"[ChiefTrader] Order emitted: {order.side} {bet} USDC (score={result['score']})")

if __name__ == "__main__":
    bankroll = Decimal(os.getenv("BANKROLL_USDC", "1000"))
    agent = ChiefTraderAgent(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        bankroll=bankroll,
    )
    asyncio.run(agent.run())
```

**Step 5: Vérifier que les tests passent**

```bash
pytest tests/agents/test_aggregator.py -v
```
Expected: 5 PASSED

**Step 6: Commit**

```bash
git add agents/chief_trader/ tests/agents/test_aggregator.py
git commit -m "feat: add Chief Trader agent with weighted signal aggregation and Kelly sizing"
```

---

## Phase 4 — Exécution

---

### Task 9: Execution Agent avec Circuit Breakers

**Files:**
- Create: `agents/execution/__init__.py`
- Create: `agents/execution/circuit_breaker.py`
- Create: `agents/execution/agent.py`
- Create: `tests/agents/test_circuit_breaker.py`

**Step 1: Écrire les tests**

```python
# tests/agents/test_circuit_breaker.py
from agents.execution.circuit_breaker import CircuitBreaker
from decimal import Decimal
import pytest

def test_breaker_opens_on_daily_drawdown():
    cb = CircuitBreaker(initial_bankroll=Decimal("1000"))
    cb.update_bankroll(Decimal("840"))  # -16% > seuil de -15%
    assert cb.is_open() is True

def test_breaker_opens_on_consecutive_losses():
    cb = CircuitBreaker(initial_bankroll=Decimal("1000"))
    for _ in range(5):
        cb.record_loss()
    assert cb.is_open() is True

def test_breaker_closed_normally():
    cb = CircuitBreaker(initial_bankroll=Decimal("1000"))
    cb.update_bankroll(Decimal("950"))  # -5%, OK
    cb.record_loss()
    assert cb.is_open() is False

def test_breaker_caps_bet_at_5_percent():
    cb = CircuitBreaker(initial_bankroll=Decimal("1000"))
    oversized = Decimal("100")  # 10% du bankroll
    capped = cb.cap_bet(oversized)
    assert capped == Decimal("50.00")

def test_breaker_resets_consecutive_on_win():
    cb = CircuitBreaker(initial_bankroll=Decimal("1000"))
    for _ in range(4):
        cb.record_loss()
    cb.record_win()
    assert cb._consecutive_losses == 0
```

**Step 2: Vérifier que les tests échouent**

```bash
pytest tests/agents/test_circuit_breaker.py -v
```
Expected: FAIL

**Step 3: Implémenter le circuit breaker**

```python
# agents/execution/__init__.py
# (vide)

# agents/execution/circuit_breaker.py
from decimal import Decimal, ROUND_DOWN
import time

MAX_DAILY_DRAWDOWN = Decimal("0.15")   # 15%
MAX_CONSECUTIVE_LOSSES = 5
MAX_BET_FRACTION = Decimal("0.05")     # 5%
PAUSE_AFTER_LOSSES_SECONDS = 3600      # 1h

class CircuitBreaker:
    def __init__(self, initial_bankroll: Decimal):
        self.initial_bankroll = initial_bankroll
        self.current_bankroll = initial_bankroll
        self._consecutive_losses = 0
        self._pause_until: float = 0.0

    def update_bankroll(self, new_bankroll: Decimal):
        self.current_bankroll = new_bankroll

    def record_loss(self):
        self._consecutive_losses += 1
        if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            self._pause_until = time.time() + PAUSE_AFTER_LOSSES_SECONDS
            print(f"[CircuitBreaker] {MAX_CONSECUTIVE_LOSSES} losses in a row — pausing 1h")

    def record_win(self):
        self._consecutive_losses = 0

    def is_open(self) -> bool:
        """True = circuit ouvert = STOP trading."""
        # Drawdown journalier
        drawdown = (self.initial_bankroll - self.current_bankroll) / self.initial_bankroll
        if drawdown >= MAX_DAILY_DRAWDOWN:
            print(f"[CircuitBreaker] Daily drawdown {drawdown:.1%} >= 15% — STOP")
            return True

        # Pause après pertes consécutives
        if time.time() < self._pause_until:
            remaining = self._pause_until - time.time()
            print(f"[CircuitBreaker] In loss pause, {remaining:.0f}s remaining")
            return True

        return False

    def cap_bet(self, amount: Decimal) -> Decimal:
        """Plafonne la mise à MAX_BET_FRACTION du bankroll actuel."""
        max_bet = self.current_bankroll * MAX_BET_FRACTION
        capped = min(amount, max_bet)
        return capped.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
```

**Step 4: Implémenter l'agent d'exécution (paper trading)**

```python
# agents/execution/agent.py
import asyncio
import os
import json
from decimal import Decimal
from shared.models import Order, TradeResult, AgentHeartbeat
from shared.redis_client import RedisClient
from agents.execution.circuit_breaker import CircuitBreaker
from prometheus_client import start_http_server, Counter, Gauge

TRADES_COUNTER = Counter("execution_trades_total", "Total trades executed", ["result"])
BANKROLL_GAUGE = Gauge("execution_bankroll_usdc", "Current bankroll in USDC")

class ExecutionAgent:
    def __init__(self, redis_url: str, bankroll: Decimal, paper_trading: bool = True):
        self.redis = RedisClient(redis_url)
        self.bankroll = bankroll
        self.paper_trading = paper_trading
        self.circuit_breaker = CircuitBreaker(bankroll)
        self._order_offset = "0"

    async def run(self):
        await self.redis.connect()
        start_http_server(8005)
        BANKROLL_GAUGE.set(float(self.bankroll))
        print(f"[ExecutionAgent] Starting (paper={self.paper_trading})...")
        while True:
            await self._process_orders()
            await asyncio.sleep(5)

    async def _process_orders(self):
        messages = await self.redis.read_signals(
            stream="orders",
            count=10,
            last_id=self._order_offset,
        )
        for msg in messages:
            try:
                order = Order(**msg)
                await self._execute(order)
            except Exception as e:
                print(f"[ExecutionAgent] Error processing order: {e}")

    async def _execute(self, order: Order):
        if self.circuit_breaker.is_open():
            print(f"[ExecutionAgent] Circuit OPEN — skipping order {order.id}")
            return

        final_amount = self.circuit_breaker.cap_bet(order.amount_usdc)
        if final_amount <= 0:
            return

        if self.paper_trading:
            result = await self._paper_trade(order, final_amount)
        else:
            result = await self._live_trade(order, final_amount)

        if result.filled:
            TRADES_COUNTER.labels(result="filled").inc()
            # Simule impact sur bankroll (paper trading)
            self.circuit_breaker.record_win()
        else:
            TRADES_COUNTER.labels(result="failed").inc()
            self.circuit_breaker.record_loss()

        hb = AgentHeartbeat(
            agent_name="execution",
            status="ok",
            message=f"filled={result.filled} amount={final_amount}"
        )
        await self.redis.publish_heartbeat(hb)

    async def _paper_trade(self, order: Order, amount: Decimal) -> TradeResult:
        """Simule l'exécution sans appel API réel."""
        print(f"[PAPER] {order.side.upper()} {amount} USDC on {order.market_id}")
        return TradeResult(
            order_id=order.id,
            filled=True,
            fill_price=Decimal("0.60"),
        )

    async def _live_trade(self, order: Order, amount: Decimal) -> TradeResult:
        """Exécution réelle via Polymarket CLOB API — à implémenter en Phase 5."""
        raise NotImplementedError("Live trading not yet implemented")

if __name__ == "__main__":
    bankroll = Decimal(os.getenv("BANKROLL_USDC", "1000"))
    paper = os.getenv("PAPER_TRADING", "true").lower() == "true"
    agent = ExecutionAgent(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        bankroll=bankroll,
        paper_trading=paper,
    )
    asyncio.run(agent.run())
```

**Step 5: Vérifier que les tests passent**

```bash
pytest tests/agents/test_circuit_breaker.py -v
```
Expected: 5 PASSED

**Step 6: Commit**

```bash
git add agents/execution/ tests/agents/test_circuit_breaker.py
git commit -m "feat: add Execution agent with hard-coded circuit breakers (paper trading mode)"
```

---

## Phase 5 — Supervision Claude

---

### Task 10: Health Checker Agent (Claude)

**Files:**
- Create: `agents/health/__init__.py`
- Create: `agents/health/checks.py`
- Create: `agents/health/agent.py`
- Create: `tests/agents/test_health_checks.py`

**Step 1: Écrire les tests**

```python
# tests/agents/test_health_checks.py
import pytest
from unittest.mock import AsyncMock
from agents.health.checks import HealthReport, check_heartbeats

@pytest.mark.asyncio
async def test_all_agents_healthy():
    mock_redis = AsyncMock()
    mock_redis.get_last_heartbeat.return_value = {"status": "ok", "timestamp": "2026-03-06T12:00:00"}
    report = await check_heartbeats(mock_redis, agents=["market", "polymarket", "chief_trader", "execution"])
    assert report.healthy is True
    assert len(report.issues) == 0

@pytest.mark.asyncio
async def test_missing_heartbeat_flags_issue():
    mock_redis = AsyncMock()
    mock_redis.get_last_heartbeat.return_value = None  # Agent mort
    report = await check_heartbeats(mock_redis, agents=["market"])
    assert report.healthy is False
    assert "market" in report.issues[0]

@pytest.mark.asyncio
async def test_error_status_flags_issue():
    mock_redis = AsyncMock()
    mock_redis.get_last_heartbeat.return_value = {"status": "error", "message": "WS down"}
    report = await check_heartbeats(mock_redis, agents=["market"])
    assert report.healthy is False
```

**Step 2: Vérifier que les tests échouent**

```bash
pytest tests/agents/test_health_checks.py -v
```
Expected: FAIL

**Step 3: Implémenter les checks**

```python
# agents/health/__init__.py
# (vide)

# agents/health/checks.py
from dataclasses import dataclass, field

@dataclass
class HealthReport:
    healthy: bool
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

EXPECTED_AGENTS = ["market", "sentiment", "onchain", "polymarket", "chief_trader", "execution"]

async def check_heartbeats(redis_client, agents: list[str] = None) -> HealthReport:
    agents = agents or EXPECTED_AGENTS
    issues = []
    for agent in agents:
        hb = await redis_client.get_last_heartbeat(agent)
        if hb is None:
            issues.append(f"Agent '{agent}' has no heartbeat — possibly down")
        elif hb.get("status") in ("error", "degraded"):
            issues.append(f"Agent '{agent}' status={hb['status']}: {hb.get('message', '')}")
    return HealthReport(healthy=len(issues) == 0, issues=issues)
```

**Step 4: Implémenter l'agent Health Checker avec Claude**

```python
# agents/health/agent.py
import asyncio
import os
import json
from anthropic import AsyncAnthropic
from shared.redis_client import RedisClient
from shared.models import AgentHeartbeat
from agents.health.checks import check_heartbeats, EXPECTED_AGENTS

SYSTEM_PROMPT = """Tu es un agent de supervision d'un système de trading automatisé 24/7.
Tu reçois un rapport de santé du système toutes les 15 minutes.
Ton rôle :
1. Identifier les problèmes critiques (agents down, drawdown élevé, erreurs API)
2. Proposer des actions correctives concrètes
3. Évaluer la sévérité : OK / WARNING / CRITICAL
Réponds UNIQUEMENT en JSON avec ce format :
{"severity": "OK|WARNING|CRITICAL", "summary": "...", "actions": ["...", "..."]}
"""

class HealthCheckerAgent:
    CHECK_INTERVAL = 900  # 15 minutes

    def __init__(self, redis_url: str, anthropic_api_key: str):
        self.redis = RedisClient(redis_url)
        self.claude = AsyncAnthropic(api_key=anthropic_api_key)

    async def run(self):
        await self.redis.connect()
        print("[HealthChecker] Starting supervision loop (15min interval)...")
        while True:
            await self._run_check()
            await asyncio.sleep(self.CHECK_INTERVAL)

    async def _run_check(self):
        report = await check_heartbeats(self.redis)
        report_text = json.dumps({
            "healthy": report.healthy,
            "issues": report.issues,
            "recommendations": report.recommendations,
        }, indent=2)

        response = await self.claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Rapport de santé :\n{report_text}"}],
        )

        try:
            analysis = json.loads(response.content[0].text)
        except Exception:
            analysis = {"severity": "WARNING", "summary": "Parse error", "actions": []}

        print(f"[HealthChecker] {analysis['severity']}: {analysis['summary']}")

        severity = analysis.get("severity", "OK")
        if severity == "CRITICAL":
            await self.redis.publish_incident({
                "severity": "CRITICAL",
                "agent": "health_checker",
                "message": analysis["summary"],
                "actions": json.dumps(analysis.get("actions", [])),
            })
            print("[HealthChecker] CRITICAL incident published — Execution agent should pause")

        hb = AgentHeartbeat(agent_name="health_checker", status="ok", message=severity)
        await self.redis.publish_heartbeat(hb)

if __name__ == "__main__":
    agent = HealthCheckerAgent(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
    )
    asyncio.run(agent.run())
```

**Step 5: Vérifier que les tests passent**

```bash
pytest tests/agents/test_health_checks.py -v
```
Expected: 3 PASSED

**Step 6: Commit**

```bash
git add agents/health/ tests/agents/test_health_checks.py
git commit -m "feat: add Health Checker supervisor agent with Claude analysis (15min cron)"
```

---

### Task 11: Strategy Reviewer Agent (Claude)

**Files:**
- Create: `agents/reviewer/__init__.py`
- Create: `agents/reviewer/agent.py`

**Step 1: Implémenter l'agent**

```python
# agents/reviewer/__init__.py
# (vide)

# agents/reviewer/agent.py
import asyncio
import os
import json
import asyncpg
from anthropic import AsyncAnthropic
from shared.redis_client import RedisClient
from shared.models import AgentHeartbeat

SYSTEM_PROMPT = """Tu es un analyste quantitatif senior supervisant un système de trading sur Polymarket.
Toutes les 2 heures, tu reçois les statistiques de performance récentes.
Ton analyse doit couvrir :
1. Win rate (cible > 52%) — est-il dans les bornes ?
2. Les pertes sont-elles concentrées sur un type de signal ?
3. Les poids du Chief Trader sont-ils encore optimaux ?
4. Recommandation sur le Kelly sizing
Réponds en JSON :
{
  "win_rate_ok": true/false,
  "weight_adjustments": {"market": 0.30, "sentiment": 0.20, "onchain": 0.20, "polymarket": 0.30},
  "confidence": 0.0-1.0,
  "summary": "...",
  "alerts": ["..."]
}
"""

class StrategyReviewerAgent:
    REVIEW_INTERVAL = 7200  # 2 heures

    def __init__(self, redis_url: str, db_url: str, anthropic_api_key: str):
        self.redis = RedisClient(redis_url)
        self.db_url = db_url
        self.claude = AsyncAnthropic(api_key=anthropic_api_key)

    async def run(self):
        await self.redis.connect()
        print("[StrategyReviewer] Starting review loop (2h interval)...")
        while True:
            await self._run_review()
            await asyncio.sleep(self.REVIEW_INTERVAL)

    async def _run_review(self):
        stats = await self._fetch_stats()
        if not stats:
            return

        response = await self.claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Statistiques des 2 dernières heures :\n{json.dumps(stats, indent=2)}"}],
        )

        try:
            analysis = json.loads(response.content[0].text)
        except Exception as e:
            print(f"[StrategyReviewer] Parse error: {e}")
            return

        print(f"[StrategyReviewer] Review complete: {analysis['summary']}")

        # Auto-apply weight adjustments si confiance > 80%
        if analysis.get("confidence", 0) > 0.80:
            await self._apply_weight_adjustments(analysis["weight_adjustments"])

        # Sauvegarder le rapport en base
        await self._save_report(analysis)

        hb = AgentHeartbeat(agent_name="reviewer", status="ok", message=analysis["summary"][:100])
        await self.redis.publish_heartbeat(hb)

    async def _fetch_stats(self) -> dict | None:
        try:
            conn = await asyncpg.connect(self.db_url)
            rows = await conn.fetch("""
                SELECT filled, fill_price, timestamp
                FROM trade_results
                WHERE timestamp > NOW() - INTERVAL '2 hours'
            """)
            await conn.close()
            if not rows:
                return None
            total = len(rows)
            wins = sum(1 for r in rows if r["filled"])
            return {
                "total_trades": total,
                "win_rate": round(wins / total, 3) if total > 0 else 0,
                "losses": total - wins,
            }
        except Exception as e:
            print(f"[StrategyReviewer] DB error: {e}")
            return None

    async def _apply_weight_adjustments(self, weights: dict):
        """Publie les nouveaux poids sur Redis pour que Chief Trader les lise."""
        await self.redis._redis.set("config:weights", json.dumps(weights))
        print(f"[StrategyReviewer] Applied new weights: {weights}")

    async def _save_report(self, analysis: dict):
        try:
            conn = await asyncpg.connect(self.db_url)
            await conn.execute(
                "INSERT INTO strategy_reports (report) VALUES ($1)",
                json.dumps(analysis)
            )
            await conn.close()
        except Exception as e:
            print(f"[StrategyReviewer] Failed to save report: {e}")

if __name__ == "__main__":
    agent = StrategyReviewerAgent(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        db_url=os.getenv("DATABASE_URL"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
    )
    asyncio.run(agent.run())
```

**Step 2: Commit**

```bash
git add agents/reviewer/
git commit -m "feat: add Strategy Reviewer agent with Claude analysis and auto weight adjustment"
```

---

### Task 12: Docker Compose complet — tous les agents

**Files:**
- Modify: `infra/docker-compose.yml`

**Step 1: Ajouter tous les services agents**

Remplacer le contenu de `infra/docker-compose.yml` par :

```yaml
version: "3.9"

x-agent-defaults: &agent-defaults
  restart: always
  env_file: ../.env
  depends_on:
    redis:
      condition: service_healthy
    postgres:
      condition: service_healthy
  volumes:
    - ..:/app
  working_dir: /app

services:
  redis:
    image: redis:7-alpine
    restart: always
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3

  postgres:
    image: postgres:16-alpine
    restart: always
    environment:
      POSTGRES_USER: trader
      POSTGRES_PASSWORD: trader
      POSTGRES_DB: trade
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U trader"]
      interval: 10s
      timeout: 5s
      retries: 5

  agent-market:
    <<: *agent-defaults
    image: python:3.12-slim
    command: python -m agents.market.agent
    ports:
      - "8000:8000"

  agent-sentiment:
    <<: *agent-defaults
    image: python:3.12-slim
    command: python -m agents.sentiment.agent
    ports:
      - "8001:8001"

  agent-onchain:
    <<: *agent-defaults
    image: python:3.12-slim
    command: python -m agents.onchain.agent
    ports:
      - "8002:8002"

  agent-polymarket:
    <<: *agent-defaults
    image: python:3.12-slim
    command: python -m agents.polymarket.agent
    ports:
      - "8003:8003"

  agent-chief-trader:
    <<: *agent-defaults
    image: python:3.12-slim
    command: python -m agents.chief_trader.agent
    ports:
      - "8004:8004"

  agent-execution:
    <<: *agent-defaults
    image: python:3.12-slim
    command: python -m agents.execution.agent
    ports:
      - "8005:8005"

  agent-health:
    <<: *agent-defaults
    image: python:3.12-slim
    command: python -m agents.health.agent

  agent-reviewer:
    <<: *agent-defaults
    image: python:3.12-slim
    command: python -m agents.reviewer.agent

  prometheus:
    image: prom/prometheus:v2.51.0
    restart: always
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:10.4.0
    restart: always
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
    ports:
      - "3000:3000"
    depends_on:
      - prometheus

volumes:
  postgres_data:
```

**Step 2: Démarrer le système complet en paper trading**

```bash
cp .env.example .env
# Remplir les clés API dans .env (au minimum ANTHROPIC_API_KEY et BINANCE_API_KEY)
cd infra && docker compose up -d
docker compose ps
```

**Step 3: Vérifier que les agents démarrent**

```bash
docker compose logs agent-market --tail=20
docker compose logs agent-chief-trader --tail=20
docker compose logs agent-health --tail=20
```
Expected: logs sans erreur, agents connectés à Redis

**Step 4: Commit**

```bash
git add infra/docker-compose.yml
git commit -m "feat: complete Docker Compose with all 8 agents + monitoring stack"
```

---

## Phase 6 — Tests d'intégration & run final

---

### Task 13: Test d'intégration bout-en-bout (paper trading)

**Files:**
- Create: `tests/integration/test_full_pipeline.py`

**Step 1: Écrire le test**

```python
# tests/integration/test_full_pipeline.py
"""
Test d'intégration : vérifie que le pipeline complet fonctionne en paper trading.
Nécessite Redis running sur localhost:6379.
"""
import pytest
import asyncio
from decimal import Decimal
from shared.redis_client import RedisClient
from shared.models import Signal
from agents.chief_trader.aggregator import aggregate_signals, should_trade
from agents.execution.circuit_breaker import CircuitBreaker
from shared.kelly import compute_bet_size

@pytest.mark.asyncio
async def test_full_signal_to_order_pipeline():
    """Simule le chemin complet : signaux → agrégation → Kelly → circuit breaker → ordre."""
    # 1. Signaux bullish de toutes les sources
    signals = [
        Signal(source="market", score=78, direction="up", confidence=0.56),
        Signal(source="sentiment", score=68, direction="up", confidence=0.36),
        Signal(source="onchain", score=72, direction="up", confidence=0.44),
        Signal(source="polymarket", score=80, direction="up", confidence=0.60),
    ]

    # 2. Agrégation Chief Trader
    result = aggregate_signals(signals)
    assert result["score"] > 65
    assert should_trade(result) is True

    # 3. Sizing Kelly
    bankroll = Decimal("1000")
    bet = compute_bet_size(bankroll, result["win_prob"], 1.0)
    assert bet > 0
    assert bet <= Decimal("50")  # max 5% du bankroll

    # 4. Circuit breaker (doit être fermé)
    cb = CircuitBreaker(bankroll)
    assert cb.is_open() is False
    final_bet = cb.cap_bet(bet)
    assert final_bet == bet  # Pas de cap nécessaire

    print(f"Pipeline OK: score={result['score']} bet={final_bet} USDC")

@pytest.mark.asyncio
async def test_circuit_breaker_stops_trade_after_losses():
    signals = [
        Signal(source="market", score=80, direction="up", confidence=0.60),
        Signal(source="polymarket", score=75, direction="up", confidence=0.50),
    ]
    result = aggregate_signals(signals)
    cb = CircuitBreaker(Decimal("1000"))
    for _ in range(5):
        cb.record_loss()
    assert cb.is_open() is True  # Circuit ouvert = pas de trade
```

**Step 2: Lancer les tests d'intégration**

```bash
pytest tests/integration/ -v
```
Expected: 2 PASSED

**Step 3: Lancer tous les tests**

```bash
pytest tests/ -v --ignore=tests/integration  # Tests unitaires
pytest tests/ -v  # Tous les tests
```
Expected: Tous verts

**Step 4: Commit final**

```bash
git add tests/integration/
git commit -m "test: add end-to-end integration test for full signal→order pipeline"
```

---

## Checklist de mise en production (Phase 5 → live)

Avant de passer de `PAPER_TRADING=true` à `PAPER_TRADING=false` :

- [ ] Win rate paper trading > 52% sur 200+ trades simulés
- [ ] Tous les circuit breakers testés manuellement
- [ ] Clés API Polymarket CLOB configurées et testées
- [ ] Implémentation de `_live_trade()` dans `agents/execution/agent.py`
- [ ] Alertes Telegram configurées et testées
- [ ] Dashboard Grafana opérationnel avec toutes les métriques
- [ ] Agent sentiment et on-chain implémentés
- [ ] Backup PostgreSQL configuré
- [ ] Bankroll initiale limitée (ex: $100 USDC pour les premiers jours)
