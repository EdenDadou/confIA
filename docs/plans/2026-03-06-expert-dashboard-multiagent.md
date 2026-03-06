# Expert Dashboard Multi-Agent with Trailing Stop — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform the monolithic `dashboard.py` into a multi-agent system with expert-level indicators, Redis Streams communication, Playwright on-chain scraping, and a Position Manager with trailing stop + signal-based exit.

**Architecture:** 6 async agents (Technical, Sentiment, On-Chain, Order Flow, Chief Trader, Position Manager) publish/consume signals via Redis Streams. The dashboard reads aggregated state from Redis and serves the existing frontend. Positions stay open beyond 30s if profitable and confirmed by signals; trailing stop (ATR-based) + signal reversal triggers close.

**Tech Stack:** Python 3.13, Redis Streams, FastAPI, Playwright (headless Chromium), Binance WS, MEXC API, numpy, ta library, Pydantic v2

---

## Phase 1 — Infrastructure

---

### Task 1: Install new dependencies

**Files:**
- Modify: `requirements.txt`

**Step 1: Add playwright and fakeredis to requirements**

```
pydantic>=2.10
redis>=5.0
asyncpg>=0.30
websockets>=13.0
ccxt>=4.4
anthropic>=0.40
prometheus-client>=0.21
python-dotenv>=1.0
httpx>=0.27
pandas>=2.2
numpy>=2.0
ta>=0.11
pytest>=8.2
pytest-asyncio>=0.23
playwright>=1.40
fakeredis>=2.21
```

**Step 2: Install**

```bash
source .venv/bin/activate && pip install -r requirements.txt
```

**Step 3: Install Playwright browsers**

```bash
source .venv/bin/activate && playwright install chromium
```

**Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add playwright and fakeredis dependencies"
```

---

### Task 2: Redis client shared module

**Files:**
- Create: `shared/redis_client.py`
- Create: `tests/test_redis_client.py`

**Step 1: Write the failing test**

```python
# tests/test_redis_client.py
import pytest
import asyncio
import json
from shared.redis_client import RedisClient


@pytest.fixture
async def rclient():
    rc = RedisClient(url="redis://localhost:6379", fake=True)
    await rc.connect()
    yield rc
    await rc.close()


@pytest.mark.asyncio
async def test_publish_and_read(rclient):
    await rclient.publish("test:stream", {"source": "market", "score": 75})
    messages = await rclient.read("test:stream", last_id="0")
    assert len(messages) >= 1
    data = messages[-1]
    assert data["source"] == "market"
    assert data["score"] == "75"  # Redis stores as string


@pytest.mark.asyncio
async def test_set_get_state(rclient):
    await rclient.set_state("position:current", {"direction": "LONG", "entry": 95000})
    state = await rclient.get_state("position:current")
    assert state["direction"] == "LONG"


@pytest.mark.asyncio
async def test_read_empty_stream(rclient):
    messages = await rclient.read("empty:stream", last_id="0")
    assert messages == []
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_redis_client.py -v`
Expected: FAIL — `shared.redis_client` has no `RedisClient`

**Step 3: Implement RedisClient**

```python
# shared/redis_client.py
import json
from typing import Optional
import redis.asyncio as aioredis


class RedisClient:
    def __init__(self, url: str = "redis://localhost:6379", fake: bool = False):
        self._url = url
        self._fake = fake
        self._r: Optional[aioredis.Redis] = None

    async def connect(self):
        if self._fake:
            import fakeredis.aioredis
            self._r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        else:
            self._r = aioredis.from_url(self._url, decode_responses=True)

    async def close(self):
        if self._r:
            await self._r.aclose()

    async def publish(self, stream: str, data: dict):
        flat = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in data.items()}
        await self._r.xadd(stream, flat, maxlen=1000)

    async def read(self, stream: str, last_id: str = "$", count: int = 10) -> list[dict]:
        try:
            exists = await self._r.exists(stream)
            if not exists:
                return []
            results = await self._r.xrange(stream, min=last_id, count=count) if last_id != "$" else []
            return [entry for _, entry in results]
        except Exception:
            return []

    async def read_latest(self, stream: str) -> Optional[dict]:
        try:
            results = await self._r.xrevrange(stream, count=1)
            if results:
                return results[0][1]
            return None
        except Exception:
            return None

    async def set_state(self, key: str, data: dict):
        await self._r.set(key, json.dumps(data))

    async def get_state(self, key: str) -> Optional[dict]:
        raw = await self._r.get(key)
        if raw:
            return json.loads(raw)
        return None
```

**Step 4: Run tests**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_redis_client.py -v`
Expected: 3 PASSED

**Step 5: Commit**

```bash
git add shared/redis_client.py tests/test_redis_client.py
git commit -m "feat: add async Redis client with streams support"
```

---

### Task 3: Shared indicators library

**Files:**
- Create: `shared/indicators.py`
- Create: `tests/test_indicators.py`

**Step 1: Write the failing tests**

```python
# tests/test_indicators.py
import pytest
import numpy as np
from shared.indicators import (
    compute_rsi, compute_macd, compute_bollinger, compute_atr,
    compute_vwap, compute_ema, compute_stoch_rsi, compute_obv,
    compute_momentum, detect_divergence,
)


def make_closes(n=50, base=95000, volatility=50):
    np.random.seed(42)
    return list(base + np.cumsum(np.random.randn(n) * volatility))


def make_ohlcv(n=50, base=95000):
    np.random.seed(42)
    candles = []
    price = base
    for _ in range(n):
        o = price
        c = o + np.random.randn() * 50
        h = max(o, c) + abs(np.random.randn() * 20)
        l = min(o, c) - abs(np.random.randn() * 20)
        v = abs(np.random.randn() * 1000) + 100
        candles.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
        price = c
    return candles


def test_rsi_range():
    closes = make_closes(50)
    rsi = compute_rsi(closes, 14)
    assert 0 <= rsi <= 100


def test_rsi_short_input():
    assert compute_rsi([100, 101], 14) == 50.0


def test_macd_returns_three_values():
    closes = make_closes(50)
    macd_line, signal_line, histogram = compute_macd(closes)
    assert isinstance(histogram, float)


def test_bollinger_bands():
    closes = make_closes(50)
    upper, middle, lower = compute_bollinger(closes, 20, 2.0)
    assert upper > middle > lower


def test_atr_positive():
    candles = make_ohlcv(50)
    atr = compute_atr(candles, 14)
    assert atr > 0


def test_vwap():
    candles = make_ohlcv(50)
    vwap = compute_vwap(candles)
    assert vwap > 0


def test_ema():
    closes = make_closes(50)
    ema = compute_ema(closes, 12)
    assert len(ema) == len(closes)


def test_stoch_rsi():
    closes = make_closes(50)
    k, d = compute_stoch_rsi(closes, 14)
    assert 0 <= k <= 100
    assert 0 <= d <= 100


def test_obv():
    candles = make_ohlcv(50)
    obv = compute_obv(candles)
    assert isinstance(obv, float)


def test_momentum():
    closes = make_closes(50)
    mom = compute_momentum(closes, 3)
    assert mom in ("up", "down", "neutral")


def test_divergence():
    closes = make_closes(50)
    div = detect_divergence(closes)
    assert div["type"] in ("bullish", "bearish", "none")
    assert 0 <= div["score"] <= 100
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_indicators.py -v`
Expected: FAIL — cannot import from `shared.indicators`

**Step 3: Implement indicators**

```python
# shared/indicators.py
import numpy as np


def compute_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    arr = np.array(closes[-(period + 1):])
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = float(np.mean(gains))
    avg_l = float(np.mean(losses))
    if avg_l < 1e-9:
        return 99.0
    return round(100.0 - 100.0 / (1.0 + avg_g / avg_l), 1)


def compute_ema(data: list[float], period: int) -> list[float]:
    if not data:
        return []
    k = 2 / (period + 1)
    result = [data[0]]
    for val in data[1:]:
        result.append(val * k + result[-1] * (1 - k))
    return result


def compute_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, float]:
    if len(closes) < slow:
        return 0.0, 0.0, 0.0
    fast_ema = compute_ema(closes, fast)
    slow_ema = compute_ema(closes, slow)
    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = compute_ema(macd_line, signal)
    histogram = macd_line[-1] - signal_line[-1]
    return round(macd_line[-1], 4), round(signal_line[-1], 4), round(histogram, 4)


def compute_bollinger(closes: list[float], period: int = 20, std_dev: float = 2.0) -> tuple[float, float, float]:
    if len(closes) < period:
        mid = closes[-1] if closes else 0
        return mid + 100, mid, mid - 100
    window = closes[-period:]
    middle = float(np.mean(window))
    std = float(np.std(window))
    return round(middle + std_dev * std, 2), round(middle, 2), round(middle - std_dev * std, 2)


def compute_atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return float(np.mean(trs)) if trs else 0.0
    return round(float(np.mean(trs[-period:])), 2)


def compute_vwap(candles: list[dict]) -> float:
    if not candles:
        return 0.0
    cum_tp_vol = 0.0
    cum_vol = 0.0
    for c in candles:
        tp = (c["high"] + c["low"] + c["close"]) / 3
        vol = c.get("volume", 1)
        cum_tp_vol += tp * vol
        cum_vol += vol
    return round(cum_tp_vol / cum_vol, 2) if cum_vol > 0 else 0.0


def compute_stoch_rsi(closes: list[float], period: int = 14) -> tuple[float, float]:
    if len(closes) < period + period:
        return 50.0, 50.0
    rsis = []
    for i in range(period, len(closes) + 1):
        rsis.append(compute_rsi(closes[:i], period))
    if len(rsis) < period:
        return 50.0, 50.0
    window = rsis[-period:]
    min_rsi = min(window)
    max_rsi = max(window)
    if max_rsi - min_rsi < 1e-9:
        k = 50.0
    else:
        k = (rsis[-1] - min_rsi) / (max_rsi - min_rsi) * 100
    # %D = 3-period SMA of %K
    k_values = []
    for i in range(max(0, len(rsis) - 3), len(rsis)):
        w = rsis[max(0, i - period + 1):i + 1]
        mn, mx = min(w), max(w)
        k_values.append((rsis[i] - mn) / (mx - mn) * 100 if mx - mn > 1e-9 else 50.0)
    d = float(np.mean(k_values)) if k_values else k
    return round(k, 1), round(d, 1)


def compute_obv(candles: list[dict]) -> float:
    if len(candles) < 2:
        return 0.0
    obv = 0.0
    for i in range(1, len(candles)):
        if candles[i]["close"] > candles[i - 1]["close"]:
            obv += candles[i].get("volume", 0)
        elif candles[i]["close"] < candles[i - 1]["close"]:
            obv -= candles[i].get("volume", 0)
    return round(obv, 2)


def compute_momentum(closes: list[float], n: int = 3) -> str:
    if len(closes) < n + 1:
        return "neutral"
    recent = closes[-(n + 1):]
    ups = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])
    downs = sum(1 for i in range(1, len(recent)) if recent[i] < recent[i - 1])
    if ups >= n:
        return "up"
    if downs >= n:
        return "down"
    if ups > downs:
        return "up"
    if downs > ups:
        return "down"
    return "neutral"


def detect_divergence(closes: list[float]) -> dict:
    if len(closes) < 35:
        return {"type": "none", "label": "NONE", "score": 50}
    mid_idx = len(closes) - 12
    rsi_mid = compute_rsi(closes[:mid_idx])
    rsi_now = compute_rsi(closes)
    price_mid = closes[mid_idx]
    price_now = closes[-1]
    price_delta = (price_now - price_mid) / price_mid
    rsi_delta = rsi_now - rsi_mid

    if price_delta < -0.001 and rsi_delta > 2.0 and rsi_now < 60:
        strength = min(100, int(50 + abs(rsi_delta) * 3 + abs(price_delta) * 1000))
        return {"type": "bullish", "label": "BULL DIV", "score": strength,
                "rsi_delta": round(rsi_delta, 1), "price_delta_pct": round(price_delta * 100, 3)}

    if price_delta > 0.001 and rsi_delta < -2.0 and rsi_now > 40:
        strength = max(0, int(50 - abs(rsi_delta) * 3 - abs(price_delta) * 1000))
        return {"type": "bearish", "label": "BEAR DIV", "score": strength,
                "rsi_delta": round(rsi_delta, 1), "price_delta_pct": round(price_delta * 100, 3)}

    return {"type": "none", "label": "NONE", "score": 50}
```

**Step 4: Run tests**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_indicators.py -v`
Expected: 11 PASSED

**Step 5: Commit**

```bash
git add shared/indicators.py tests/test_indicators.py
git commit -m "feat: add shared indicators library (RSI, MACD, Bollinger, ATR, VWAP, StochRSI, OBV)"
```

---

### Task 4: Kelly sizing module

**Files:**
- Create: `shared/kelly.py`
- Create: `tests/test_kelly.py`

**Step 1: Write the failing test**

```python
# tests/test_kelly.py
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
    assert size >= 15.0  # 1.5% of 1000
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_kelly.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# shared/kelly.py


def half_kelly(win_prob: float, odds: float = 1.0) -> float:
    k = (win_prob * odds - (1 - win_prob)) / odds
    return max(k / 2.0, 0.0)


def bet_size(bankroll: float, win_prob: float) -> float:
    kelly_stake = bankroll * half_kelly(win_prob)
    min_stake = round(bankroll * 0.015, 2)
    stake = max(kelly_stake, min_stake)
    stake = min(stake, round(bankroll * 0.05, 2))
    return round(stake, 2)
```

**Step 4: Run tests**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_kelly.py -v`
Expected: 5 PASSED

**Step 5: Commit**

```bash
git add shared/kelly.py tests/test_kelly.py
git commit -m "feat: add 1/2 Kelly bet sizing module"
```

---

## Phase 2 — Agents

---

### Task 5: Technical Agent

**Files:**
- Create: `agents/__init__.py`
- Create: `agents/technical/__init__.py`
- Create: `agents/technical/agent.py`
- Create: `tests/test_technical_agent.py`

**Step 1: Write the failing test**

```python
# tests/test_technical_agent.py
import pytest
from unittest.mock import AsyncMock, patch
from agents.technical.agent import TechnicalAgent


@pytest.fixture
def agent():
    return TechnicalAgent(redis_url="redis://localhost:6379", fake_redis=True)


@pytest.mark.asyncio
async def test_compute_signals(agent):
    candles = []
    price = 95000
    import numpy as np
    np.random.seed(42)
    for i in range(60):
        o = price
        c = o + np.random.randn() * 50
        h = max(o, c) + 20
        l = min(o, c) - 20
        candles.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000, "time": i})
        price = c

    signal = agent.compute(candles)
    assert "rsi" in signal
    assert "macd" in signal
    assert "bollinger" in signal
    assert "atr" in signal
    assert "vwap" in signal
    assert "stoch_rsi" in signal
    assert "ema_5s" in signal
    assert "ema_1m" in signal
    assert "ema_5m" in signal
    assert "momentum" in signal
    assert "divergence" in signal
    assert "score" in signal
    assert 0 <= signal["score"] <= 100


@pytest.mark.asyncio
async def test_score_direction(agent):
    # Bullish candles
    candles = [{"open": 95000 + i * 10, "high": 95000 + i * 10 + 15,
                "low": 95000 + i * 10 - 5, "close": 95000 + (i + 1) * 10,
                "volume": 1000, "time": i} for i in range(60)]
    signal = agent.compute(candles)
    assert signal["direction"] in ("up", "down", "neutral")
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_technical_agent.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# agents/__init__.py
# (empty)

# agents/technical/__init__.py
# (empty)

# agents/technical/agent.py
import asyncio
import json
from shared.redis_client import RedisClient
from shared.indicators import (
    compute_rsi, compute_macd, compute_bollinger, compute_atr,
    compute_vwap, compute_ema, compute_stoch_rsi, compute_obv,
    compute_momentum, detect_divergence,
)

STREAM = "signals:technical"


class TechnicalAgent:
    def __init__(self, redis_url: str = "redis://localhost:6379", fake_redis: bool = False):
        self.rc = RedisClient(url=redis_url, fake=fake_redis)

    def compute(self, candles: list[dict]) -> dict:
        closes = [c["close"] for c in candles]
        rsi = compute_rsi(closes, 14)
        macd_line, sig_line, histogram = compute_macd(closes)
        upper, middle, lower = compute_bollinger(closes, 20, 2.0)
        atr = compute_atr(candles, 14)
        vwap = compute_vwap(candles)
        stoch_k, stoch_d = compute_stoch_rsi(closes, 14)
        obv = compute_obv(candles)
        momentum = compute_momentum(closes, 3)
        divergence = detect_divergence(closes)

        # Multi-timeframe EMAs (simulated from available candles)
        ema_5s = compute_ema(closes, 12)[-1] if len(closes) >= 12 else closes[-1]
        ema_1m = compute_ema(closes, 20)[-1] if len(closes) >= 20 else closes[-1]
        ema_5m = compute_ema(closes, 50)[-1] if len(closes) >= 50 else closes[-1]

        # Composite score: weighted combination
        # RSI: oversold = bullish (high score), overbought = bearish (low score)
        if rsi >= 72:
            rsi_score = 22
        elif rsi >= 58:
            rsi_score = 68
        elif rsi >= 44:
            rsi_score = 50
        elif rsi >= 28:
            rsi_score = 34
        else:
            rsi_score = 76  # oversold = buy signal

        macd_score = 65 if histogram > 0 else 35 if histogram < 0 else 50

        # Bollinger: price near lower = bullish, near upper = bearish
        price = closes[-1]
        bb_range = upper - lower if upper > lower else 1
        bb_position = (price - lower) / bb_range
        bb_score = max(0, min(100, int((1 - bb_position) * 50 + 25)))

        # Stoch RSI
        stoch_score = 70 if stoch_k < 20 else 30 if stoch_k > 80 else 50

        mom_score = 65 if momentum == "up" else 35 if momentum == "down" else 50
        div_score = divergence["score"]

        # Weighted: RSI 18% MACD 14% BB 10% Stoch 10% Mom 15% Div 15% OBV 8% ATR 10%
        obv_trend = 60 if obv > 0 else 40 if obv < 0 else 50
        # ATR: high volatility = uncertain, mid = opportunity
        atr_score = 50  # neutral baseline

        score = int(
            0.18 * rsi_score + 0.14 * macd_score + 0.10 * bb_score +
            0.10 * stoch_score + 0.15 * mom_score + 0.15 * div_score +
            0.08 * obv_trend + 0.10 * atr_score
        )
        score = max(0, min(100, score))

        if score >= 58:
            direction = "up"
        elif score <= 42:
            direction = "down"
        else:
            direction = "neutral"

        return {
            "rsi": rsi,
            "macd": {"line": macd_line, "signal": sig_line, "histogram": histogram},
            "bollinger": {"upper": upper, "middle": middle, "lower": lower},
            "atr": atr,
            "vwap": vwap,
            "stoch_rsi": {"k": stoch_k, "d": stoch_d},
            "obv": obv,
            "ema_5s": round(ema_5s, 2),
            "ema_1m": round(ema_1m, 2),
            "ema_5m": round(ema_5m, 2),
            "momentum": momentum,
            "divergence": divergence,
            "score": score,
            "direction": direction,
        }

    async def run(self, candles_provider):
        """Main loop: compute and publish every 5s."""
        await self.rc.connect()
        try:
            while True:
                candles = await candles_provider()
                if candles and len(candles) >= 2:
                    signal = self.compute(candles)
                    await self.rc.publish(STREAM, signal)
                await asyncio.sleep(5)
        finally:
            await self.rc.close()
```

**Step 4: Run tests**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_technical_agent.py -v`
Expected: 2 PASSED

**Step 5: Commit**

```bash
git add agents/ tests/test_technical_agent.py
git commit -m "feat: add Technical Agent with expert indicators"
```

---

### Task 6: Sentiment Agent

**Files:**
- Create: `agents/sentiment/__init__.py`
- Create: `agents/sentiment/agent.py`
- Create: `tests/test_sentiment_agent.py`

**Step 1: Write the failing test**

```python
# tests/test_sentiment_agent.py
import pytest
from unittest.mock import AsyncMock, patch
from agents.sentiment.agent import SentimentAgent


@pytest.fixture
def agent():
    return SentimentAgent(redis_url="redis://localhost:6379", fake_redis=True)


@pytest.mark.asyncio
async def test_compute_score():
    agent = SentimentAgent(fake_redis=True)
    score = agent.compute_score(fear_greed=25)
    assert 0 <= score <= 100
    # Low F&G = fear = contrarian bullish
    assert score > 50


@pytest.mark.asyncio
async def test_compute_score_greedy():
    agent = SentimentAgent(fake_redis=True)
    score = agent.compute_score(fear_greed=80)
    assert score < 50  # Greed = contrarian bearish
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_sentiment_agent.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# agents/sentiment/__init__.py
# (empty)

# agents/sentiment/agent.py
import asyncio
import httpx
from shared.redis_client import RedisClient

STREAM = "signals:sentiment"


class SentimentAgent:
    def __init__(self, redis_url: str = "redis://localhost:6379", fake_redis: bool = False):
        self.rc = RedisClient(url=redis_url, fake=fake_redis)

    def compute_score(self, fear_greed: int) -> int:
        # Contrarian: extreme fear = buy signal, extreme greed = sell signal
        # F&G 0-25 = extreme fear → score 65-80 (bullish)
        # F&G 25-45 = fear → score 55-65
        # F&G 45-55 = neutral → score 50
        # F&G 55-75 = greed → score 35-45
        # F&G 75-100 = extreme greed → score 20-35 (bearish)
        if fear_greed <= 25:
            return int(80 - fear_greed * 0.6)
        elif fear_greed <= 45:
            return int(65 - (fear_greed - 25) * 0.5)
        elif fear_greed <= 55:
            return 50
        elif fear_greed <= 75:
            return int(45 - (fear_greed - 55) * 0.5)
        else:
            return int(35 - (fear_greed - 75) * 0.6)

    async def fetch_fear_greed(self) -> int:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get("https://api.alternative.me/fng/?limit=1")
                return int(r.json()["data"][0]["value"])
        except Exception:
            return 50

    async def run(self):
        await self.rc.connect()
        try:
            while True:
                fg = await self.fetch_fear_greed()
                score = self.compute_score(fg)
                direction = "up" if score >= 58 else "down" if score <= 42 else "neutral"
                await self.rc.publish(STREAM, {
                    "fear_greed": fg,
                    "score": score,
                    "direction": direction,
                })
                await asyncio.sleep(3600)  # 1h
        finally:
            await self.rc.close()
```

**Step 4: Run tests**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_sentiment_agent.py -v`
Expected: 2 PASSED

**Step 5: Commit**

```bash
git add agents/sentiment/ tests/test_sentiment_agent.py
git commit -m "feat: add Sentiment Agent (Fear & Greed contrarian)"
```

---

### Task 7: On-Chain Agent (Playwright + Binance Futures API)

**Files:**
- Create: `agents/onchain/__init__.py`
- Create: `agents/onchain/agent.py`
- Create: `tests/test_onchain_agent.py`

**Step 1: Write the failing test**

```python
# tests/test_onchain_agent.py
import pytest
from agents.onchain.agent import OnChainAgent


@pytest.fixture
def agent():
    return OnChainAgent(fake_redis=True)


def test_compute_score_bearish():
    agent = OnChainAgent(fake_redis=True)
    # High OI increase + high funding = overheated longs → bearish
    score = agent.compute_score(
        oi_change_pct=5.0,
        funding_rate=0.0005,
        long_short_ratio=2.5,
        liquidations_24h={"long": 10_000_000, "short": 2_000_000},
    )
    assert score < 45


def test_compute_score_bullish():
    agent = OnChainAgent(fake_redis=True)
    # Negative funding + shorts getting liquidated → bullish
    score = agent.compute_score(
        oi_change_pct=-2.0,
        funding_rate=-0.0003,
        long_short_ratio=0.8,
        liquidations_24h={"long": 2_000_000, "short": 10_000_000},
    )
    assert score > 55
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_onchain_agent.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# agents/onchain/__init__.py
# (empty)

# agents/onchain/agent.py
import asyncio
import httpx
from shared.redis_client import RedisClient

STREAM = "signals:onchain"


class OnChainAgent:
    def __init__(self, redis_url: str = "redis://localhost:6379", fake_redis: bool = False):
        self.rc = RedisClient(url=redis_url, fake=fake_redis)

    def compute_score(self, oi_change_pct: float, funding_rate: float,
                      long_short_ratio: float, liquidations_24h: dict) -> int:
        # Funding rate: positive = longs pay shorts (bearish if high)
        if funding_rate > 0.0003:
            fr_score = 25  # very bearish
        elif funding_rate > 0.0001:
            fr_score = 40
        elif funding_rate > -0.0001:
            fr_score = 50  # neutral
        elif funding_rate > -0.0003:
            fr_score = 62
        else:
            fr_score = 75  # negative funding = bullish

        # Long/short ratio: high = too many longs (bearish contrarian)
        if long_short_ratio > 2.0:
            ls_score = 30
        elif long_short_ratio > 1.5:
            ls_score = 40
        elif long_short_ratio > 0.8:
            ls_score = 50
        elif long_short_ratio > 0.5:
            ls_score = 60
        else:
            ls_score = 70  # few longs = bullish

        # OI change: rapid increase with high funding = overheated
        if oi_change_pct > 3.0 and funding_rate > 0.0002:
            oi_score = 30  # overheated longs
        elif oi_change_pct < -3.0 and funding_rate < -0.0001:
            oi_score = 70  # longs flushed out
        else:
            oi_score = 50

        # Liquidations: who's getting rekt
        long_liq = liquidations_24h.get("long", 0)
        short_liq = liquidations_24h.get("short", 0)
        total_liq = long_liq + short_liq
        if total_liq > 0:
            liq_ratio = short_liq / total_liq
            liq_score = int(30 + liq_ratio * 40)  # more short liqs = bullish
        else:
            liq_score = 50

        score = int(0.30 * fr_score + 0.25 * ls_score + 0.25 * oi_score + 0.20 * liq_score)
        return max(0, min(100, score))

    async def fetch_binance_futures(self) -> dict:
        defaults = {"oi_change_pct": 0.0, "funding_rate": 0.0001,
                    "long_short_ratio": 1.0,
                    "liquidations_24h": {"long": 0, "short": 0}}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                fr_r, ls_r, oi_r = await asyncio.gather(
                    client.get("https://fapi.binance.com/fapi/v1/fundingRate",
                               params={"symbol": "BTCUSDT", "limit": 1}),
                    client.get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                               params={"symbol": "BTCUSDT", "period": "5m", "limit": 1}),
                    client.get("https://fapi.binance.com/fapi/v1/openInterest",
                               params={"symbol": "BTCUSDT"}),
                    return_exceptions=True,
                )
            result = dict(defaults)
            if not isinstance(fr_r, Exception) and fr_r.status_code == 200:
                data = fr_r.json()
                if data:
                    result["funding_rate"] = float(data[0].get("fundingRate", 0.0001))

            if not isinstance(ls_r, Exception) and ls_r.status_code == 200:
                data = ls_r.json()
                if data:
                    result["long_short_ratio"] = float(data[0].get("longShortRatio", 1.0))

            if not isinstance(oi_r, Exception) and oi_r.status_code == 200:
                data = oi_r.json()
                result["open_interest"] = float(data.get("openInterest", 0))

            return result
        except Exception:
            return defaults

    async def scrape_coinglass(self) -> dict:
        """Scrape CoinGlass liquidation data using Playwright."""
        defaults = {"long": 0, "short": 0}
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto("https://www.coinglass.com/LiquidationData", timeout=15000)
                await page.wait_for_timeout(5000)

                # Try to extract BTC liquidation values from the page
                rows = await page.query_selector_all("table tbody tr")
                for row in rows:
                    text = await row.inner_text()
                    if "BTC" in text and "Bitcoin" in text:
                        cells = await row.query_selector_all("td")
                        if len(cells) >= 4:
                            try:
                                long_val = await cells[2].inner_text()
                                short_val = await cells[3].inner_text()
                                long_num = float(long_val.replace("$", "").replace(",", "").replace("M", "e6").replace("K", "e3")) if long_val.strip() else 0
                                short_num = float(short_val.replace("$", "").replace(",", "").replace("M", "e6").replace("K", "e3")) if short_val.strip() else 0
                                await browser.close()
                                return {"long": long_num, "short": short_num}
                            except (ValueError, IndexError):
                                pass
                await browser.close()
        except Exception:
            pass
        return defaults

    async def run(self):
        await self.rc.connect()
        try:
            while True:
                futures_data = await self.fetch_binance_futures()
                liqs = await self.scrape_coinglass()
                futures_data["liquidations_24h"] = liqs

                score = self.compute_score(
                    oi_change_pct=futures_data.get("oi_change_pct", 0),
                    funding_rate=futures_data["funding_rate"],
                    long_short_ratio=futures_data["long_short_ratio"],
                    liquidations_24h=liqs,
                )
                direction = "up" if score >= 58 else "down" if score <= 42 else "neutral"

                await self.rc.publish(STREAM, {
                    "score": score,
                    "direction": direction,
                    "funding_rate": futures_data["funding_rate"],
                    "long_short_ratio": futures_data["long_short_ratio"],
                    "liquidations": liqs,
                })
                await asyncio.sleep(45)  # every 45s
        finally:
            await self.rc.close()
```

**Step 4: Run tests**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_onchain_agent.py -v`
Expected: 2 PASSED

**Step 5: Commit**

```bash
git add agents/onchain/ tests/test_onchain_agent.py
git commit -m "feat: add On-Chain Agent (Binance Futures + CoinGlass scraper)"
```

---

### Task 8: Order Flow Agent

**Files:**
- Create: `agents/orderflow/__init__.py`
- Create: `agents/orderflow/agent.py`
- Create: `tests/test_orderflow_agent.py`

**Step 1: Write the failing test**

```python
# tests/test_orderflow_agent.py
import pytest
from agents.orderflow.agent import OrderFlowAgent


def test_compute_score_bid_heavy():
    agent = OrderFlowAgent(fake_redis=True)
    score = agent.compute_score(ob_ratio=0.7, funding_rate=0.0001, spread_bps=1.0)
    assert score > 55  # bids dominate = bullish


def test_compute_score_ask_heavy():
    agent = OrderFlowAgent(fake_redis=True)
    score = agent.compute_score(ob_ratio=0.3, funding_rate=0.0003, spread_bps=2.0)
    assert score < 45  # asks dominate + high funding = bearish
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_orderflow_agent.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# agents/orderflow/__init__.py
# (empty)

# agents/orderflow/agent.py
import asyncio
import httpx
from shared.redis_client import RedisClient

STREAM = "signals:orderflow"


class OrderFlowAgent:
    def __init__(self, redis_url: str = "redis://localhost:6379", fake_redis: bool = False):
        self.rc = RedisClient(url=redis_url, fake=fake_redis)

    def compute_score(self, ob_ratio: float, funding_rate: float, spread_bps: float) -> int:
        # OB ratio: >0.5 = more bids (bullish), <0.5 = more asks (bearish)
        ob_score = int(ob_ratio * 100)

        # Funding rate
        if funding_rate > 0.0003:
            fr_score = 25
        elif funding_rate > 0.0001:
            fr_score = 40
        elif funding_rate > -0.0001:
            fr_score = 50
        elif funding_rate > -0.0003:
            fr_score = 62
        else:
            fr_score = 75

        # Spread: tight = healthy market, wide = uncertainty
        sp_score = 55 if spread_bps < 1.5 else 45 if spread_bps < 3.0 else 40

        score = int(0.55 * ob_score + 0.30 * fr_score + 0.15 * sp_score)
        return max(0, min(100, score))

    async def fetch_mexc_data(self) -> dict:
        BASE = "https://contract.mexc.com/api/v1/contract"
        default = {"ob_ratio": 0.5, "funding_rate": 0.0001, "spread_bps": 2.0,
                   "bids": [], "asks": [], "mark_price": 0, "index_price": 0}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                ticker_r, depth_r, funding_r = await asyncio.gather(
                    client.get(f"{BASE}/ticker", params={"symbol": "BTC_USDT"}),
                    client.get(f"{BASE}/depth/BTC_USDT", params={"limit": 8}),
                    client.get(f"{BASE}/funding_rate/BTC_USDT"),
                    return_exceptions=True,
                )

            result = dict(default)

            if not isinstance(ticker_r, Exception) and ticker_r.status_code == 200:
                td = ticker_r.json().get("data", {})
                result["mark_price"] = float(td.get("fairPrice", 0) or 0)
                result["index_price"] = float(td.get("indexPrice", 0) or 0)
                result["funding_rate"] = float(td.get("fundingRate", 0.0001) or 0.0001)

            if not isinstance(depth_r, Exception) and depth_r.status_code == 200:
                dd = depth_r.json().get("data", {})
                bids = [(float(r[0]), float(r[1])) for r in (dd.get("bids") or [])]
                asks = [(float(r[0]), float(r[1])) for r in (dd.get("asks") or [])]
                bid_vol = sum(v for _, v in bids)
                ask_vol = sum(v for _, v in asks)
                total = bid_vol + ask_vol
                result["bids"] = bids[:6]
                result["asks"] = asks[:6]
                result["ob_ratio"] = round(bid_vol / total, 3) if total > 0 else 0.5
                if bids and asks:
                    spread = asks[0][0] - bids[0][0]
                    mid = (asks[0][0] + bids[0][0]) / 2
                    result["spread_bps"] = round(spread / mid * 10000, 2) if mid else 2.0

            if not isinstance(funding_r, Exception) and funding_r.status_code == 200:
                fd = funding_r.json().get("data", {})
                if fd.get("fundingRate") is not None:
                    result["funding_rate"] = float(fd["fundingRate"])

            return result
        except Exception:
            return default

    async def run(self):
        await self.rc.connect()
        try:
            while True:
                data = await self.fetch_mexc_data()
                score = self.compute_score(
                    data["ob_ratio"], data["funding_rate"], data["spread_bps"]
                )
                direction = "up" if score >= 58 else "down" if score <= 42 else "neutral"
                await self.rc.publish(STREAM, {
                    "score": score,
                    "direction": direction,
                    **data,
                })
                await asyncio.sleep(5)
        finally:
            await self.rc.close()
```

**Step 4: Run tests**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_orderflow_agent.py -v`
Expected: 2 PASSED

**Step 5: Commit**

```bash
git add agents/orderflow/ tests/test_orderflow_agent.py
git commit -m "feat: add Order Flow Agent (MEXC OB + funding)"
```

---

### Task 9: Chief Trader Agent

**Files:**
- Create: `agents/chief_trader/__init__.py`
- Create: `agents/chief_trader/agent.py`
- Create: `tests/test_chief_trader.py`

**Step 1: Write the failing test**

```python
# tests/test_chief_trader.py
import pytest
from agents.chief_trader.agent import ChiefTrader


def test_aggregate_bullish():
    ct = ChiefTrader(fake_redis=True)
    result = ct.aggregate(
        technical={"score": 72, "direction": "up", "atr": 150},
        sentiment={"score": 65, "direction": "up"},
        onchain={"score": 60, "direction": "up"},
        orderflow={"score": 70, "direction": "up"},
    )
    assert result["score"] > 60
    assert result["direction"] == "LONG"
    assert result["stake"] > 0


def test_aggregate_bearish():
    ct = ChiefTrader(fake_redis=True)
    result = ct.aggregate(
        technical={"score": 30, "direction": "down", "atr": 150},
        sentiment={"score": 35, "direction": "down"},
        onchain={"score": 40, "direction": "down"},
        orderflow={"score": 32, "direction": "down"},
    )
    assert result["score"] < 40
    assert result["direction"] == "SHORT"


def test_aggregate_forces_direction():
    """Even if FLAT, chief trader picks a direction."""
    ct = ChiefTrader(fake_redis=True)
    result = ct.aggregate(
        technical={"score": 50, "direction": "neutral", "atr": 100},
        sentiment={"score": 50, "direction": "neutral"},
        onchain={"score": 50, "direction": "neutral"},
        orderflow={"score": 52, "direction": "neutral"},
    )
    assert result["direction"] in ("LONG", "SHORT")


def test_circuit_breaker_consecutive_losses():
    ct = ChiefTrader(fake_redis=True, bankroll=1000)
    ct.consecutive_losses = 5
    result = ct.aggregate(
        technical={"score": 80, "direction": "up", "atr": 100},
        sentiment={"score": 70, "direction": "up"},
        onchain={"score": 65, "direction": "up"},
        orderflow={"score": 75, "direction": "up"},
    )
    assert result["action"] == "BLOCKED"
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_chief_trader.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# agents/chief_trader/__init__.py
# (empty)

# agents/chief_trader/agent.py
import asyncio
import time
from shared.redis_client import RedisClient
from shared.kelly import bet_size

STREAM = "decisions:chief"
COOLDOWN = 30


class ChiefTrader:
    def __init__(self, redis_url: str = "redis://localhost:6379",
                 fake_redis: bool = False, bankroll: float = 100.0):
        self.rc = RedisClient(url=redis_url, fake=fake_redis)
        self.bankroll = bankroll
        self.initial = bankroll
        self.consecutive_losses = 0
        self.last_trade_ts = 0.0

    def aggregate(self, technical: dict, sentiment: dict,
                  onchain: dict, orderflow: dict) -> dict:
        # Circuit breakers
        if self.consecutive_losses >= 5:
            return {"action": "BLOCKED", "reason": f"{self.consecutive_losses} consecutive losses"}
        drawdown = (self.initial - self.bankroll) / self.initial if self.initial > 0 else 0
        if drawdown >= 0.15:
            return {"action": "BLOCKED", "reason": f"Drawdown {drawdown*100:.1f}%"}

        # Weighted scoring: tech 30%, orderflow 25%, onchain 25%, sentiment 20%
        score = int(
            0.30 * technical.get("score", 50) +
            0.25 * orderflow.get("score", 50) +
            0.25 * onchain.get("score", 50) +
            0.20 * sentiment.get("score", 50)
        )
        score = max(0, min(100, score))

        # Direction — always pick one
        if score >= 55:
            direction = "LONG"
        elif score <= 45:
            direction = "SHORT"
        else:
            # Tiebreak: use technical momentum
            tech_dir = technical.get("direction", "neutral")
            if tech_dir == "up":
                direction = "LONG"
            elif tech_dir == "down":
                direction = "SHORT"
            else:
                direction = "LONG" if score >= 50 else "SHORT"

        # Win probability calibration
        if direction == "LONG":
            win_prob = min(0.88, 0.50 + (score - 50) / 100.0)
        else:
            win_prob = min(0.88, 0.50 + (50 - score) / 100.0)
        win_prob = max(0.50, win_prob)

        stake = bet_size(self.bankroll, win_prob)
        atr = technical.get("atr", 100)

        return {
            "action": "TRADE",
            "score": score,
            "direction": direction,
            "win_prob": round(win_prob, 3),
            "stake": stake,
            "atr": atr,
            "trailing_stop_distance": round(atr * 1.5, 2),
        }

    async def run(self):
        await self.rc.connect()
        try:
            while True:
                now = time.time()
                if now - self.last_trade_ts < COOLDOWN:
                    await asyncio.sleep(1)
                    continue

                # Read latest from each agent
                tech = await self.rc.read_latest("signals:technical") or {"score": 50, "direction": "neutral", "atr": 100}
                sent = await self.rc.read_latest("signals:sentiment") or {"score": 50, "direction": "neutral"}
                chain = await self.rc.read_latest("signals:onchain") or {"score": 50, "direction": "neutral"}
                flow = await self.rc.read_latest("signals:orderflow") or {"score": 50, "direction": "neutral"}

                # Convert string scores back to int (Redis stores as string)
                for d in [tech, sent, chain, flow]:
                    if "score" in d:
                        d["score"] = int(d["score"])
                    if "atr" in d:
                        d["atr"] = float(d["atr"])

                decision = self.aggregate(tech, sent, chain, flow)
                await self.rc.publish(STREAM, decision)

                if decision["action"] == "TRADE":
                    self.last_trade_ts = now

                await asyncio.sleep(1)
        finally:
            await self.rc.close()
```

**Step 4: Run tests**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_chief_trader.py -v`
Expected: 4 PASSED

**Step 5: Commit**

```bash
git add agents/chief_trader/ tests/test_chief_trader.py
git commit -m "feat: add Chief Trader Agent (weighted aggregation + Kelly + circuit breakers)"
```

---

### Task 10: Position Manager (Trailing Stop + Signal Exit)

**Files:**
- Create: `agents/position/__init__.py`
- Create: `agents/position/manager.py`
- Create: `tests/test_position_manager.py`

**Step 1: Write the failing test**

```python
# tests/test_position_manager.py
import pytest
from agents.position.manager import PositionManager, Position


def test_open_long():
    pm = PositionManager(fake_redis=True)
    pos = pm.open_position("LONG", 95000, 5.0, atr=150)
    assert pos.direction == "LONG"
    assert pos.entry_price == 95000
    assert pos.stop_loss == 95000 - 150 * 1.5  # ATR * 1.5


def test_open_short():
    pm = PositionManager(fake_redis=True)
    pos = pm.open_position("SHORT", 95000, 5.0, atr=150)
    assert pos.stop_loss == 95000 + 150 * 1.5


def test_trailing_stop_long_moves_up():
    pm = PositionManager(fake_redis=True)
    pos = pm.open_position("LONG", 95000, 5.0, atr=100)
    initial_stop = pos.stop_loss
    pm.update_tick(95200)  # price goes up
    assert pos.stop_loss > initial_stop  # stop moved up
    assert pos.highest_price == 95200


def test_trailing_stop_long_doesnt_move_down():
    pm = PositionManager(fake_redis=True)
    pos = pm.open_position("LONG", 95000, 5.0, atr=100)
    pm.update_tick(95200)
    stop_after_up = pos.stop_loss
    pm.update_tick(95100)  # price goes down
    assert pos.stop_loss == stop_after_up  # stop stays


def test_stop_loss_triggered_long():
    pm = PositionManager(fake_redis=True)
    pos = pm.open_position("LONG", 95000, 5.0, atr=100)
    result = pm.update_tick(94800)  # below stop
    assert result["action"] == "CLOSE"
    assert result["reason"] == "trailing_stop"


def test_signal_reversal_closes():
    pm = PositionManager(fake_redis=True)
    pm.open_position("LONG", 95000, 5.0, atr=100)
    result = pm.check_signal_exit({"direction": "down", "score": 35})
    assert result["action"] == "CLOSE"
    assert result["reason"] == "signal_reversal"


def test_position_stays_open_if_profitable_and_confirmed():
    pm = PositionManager(fake_redis=True)
    pm.open_position("LONG", 95000, 5.0, atr=100)
    pm.update_tick(95100)  # profitable
    result = pm.check_30s_review(current_price=95100, signal={"direction": "up", "score": 65})
    assert result["action"] == "HOLD"


def test_position_closes_at_30s_if_losing():
    pm = PositionManager(fake_redis=True)
    pm.open_position("LONG", 95000, 5.0, atr=100)
    result = pm.check_30s_review(current_price=94950, signal={"direction": "up", "score": 65})
    assert result["action"] == "CLOSE"
    assert result["reason"] == "30s_losing"


def test_pnl_calculation_long():
    pm = PositionManager(fake_redis=True)
    pos = pm.open_position("LONG", 95000, 5.0, atr=100)
    pnl = pos.unrealized_pnl(95500)
    assert pnl > 0


def test_pnl_calculation_short():
    pm = PositionManager(fake_redis=True)
    pos = pm.open_position("SHORT", 95000, 5.0, atr=100)
    pnl = pos.unrealized_pnl(94500)
    assert pnl > 0
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_position_manager.py -v`
Expected: FAIL

**Step 3: Implement**

```python
# agents/position/__init__.py
# (empty)

# agents/position/manager.py
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
        return round(self.stake * pct * 10, 4)  # leveraged PnL simulation


class PositionManager:
    def __init__(self, redis_url: str = "redis://localhost:6379", fake_redis: bool = False):
        self.rc = RedisClient(url=redis_url, fake=fake_redis)
        self.position: Optional[Position] = None

    def open_position(self, direction: str, price: float, stake: float, atr: float) -> Position:
        self.position = Position(direction=direction, entry_price=price, stake=stake, atr=atr)
        return self.position

    def close_position(self, current_price: float) -> dict:
        if not self.position:
            return {"action": "NONE"}
        pnl = self.position.unrealized_pnl(current_price)
        result = {
            "action": "CLOSED",
            "direction": self.position.direction,
            "entry_price": self.position.entry_price,
            "exit_price": current_price,
            "stake": self.position.stake,
            "pnl": pnl,
            "won": pnl > 0,
            "duration": round(time.time() - self.position.open_time, 1),
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
        else:  # SHORT
            if price < pos.lowest_price:
                pos.lowest_price = price
                pos.stop_loss = min(pos.stop_loss, price + pos.stop_distance)
            if price >= pos.stop_loss:
                return {"action": "CLOSE", "reason": "trailing_stop",
                        "price": price, "stop": pos.stop_loss}

        return {"action": "HOLD", "pnl": pos.unrealized_pnl(price)}

    def check_signal_exit(self, signal: dict) -> dict:
        if not self.position:
            return {"action": "NONE"}
        pos = self.position
        sig_dir = signal.get("direction", "neutral")
        score = int(signal.get("score", 50))

        # Close if signals strongly reverse
        if pos.direction == "LONG" and sig_dir == "down" and score < 40:
            return {"action": "CLOSE", "reason": "signal_reversal"}
        if pos.direction == "SHORT" and sig_dir == "up" and score > 60:
            return {"action": "CLOSE", "reason": "signal_reversal"}

        return {"action": "HOLD"}

    def check_30s_review(self, current_price: float, signal: dict) -> dict:
        if not self.position:
            return {"action": "NONE"}

        pnl = self.position.unrealized_pnl(current_price)

        # If losing at 30s → close
        if pnl <= 0:
            return {"action": "CLOSE", "reason": "30s_losing", "pnl": pnl}

        # If profitable AND signals confirm → hold
        sig_dir = signal.get("direction", "neutral")
        pos_dir = self.position.direction
        confirms = (
            (pos_dir == "LONG" and sig_dir in ("up", "neutral")) or
            (pos_dir == "SHORT" and sig_dir in ("down", "neutral"))
        )

        if confirms:
            return {"action": "HOLD", "reason": "profitable_confirmed", "pnl": pnl}

        # Profitable but signals don't confirm → still close
        return {"action": "CLOSE", "reason": "30s_no_confirmation", "pnl": pnl}

    @property
    def has_position(self) -> bool:
        return self.position is not None
```

**Step 4: Run tests**

Run: `source .venv/bin/activate && python3 -m pytest tests/test_position_manager.py -v`
Expected: 10 PASSED

**Step 5: Commit**

```bash
git add agents/position/ tests/test_position_manager.py
git commit -m "feat: add Position Manager with trailing stop + signal-based exit"
```

---

## Phase 3 — Integration

---

### Task 11: Rewrite dashboard.py to consume agents via Redis

**Files:**
- Modify: `dashboard.py` (full rewrite of backend, keep HTML/JS frontend)

This task replaces the monolithic logic in `dashboard.py` with:
1. Startup launches all agents as background async tasks
2. `/api/data` reads aggregated state from Redis + Position Manager state
3. Frontend remains the same HTML/JS but receives richer data

**Step 1: Rewrite the backend section of dashboard.py**

Replace everything from line 1 to line 508 (before `HTML = """`) with a new backend that:
- Imports and starts all 5 agents + Position Manager at startup
- The `/api/data` route reads latest signals from Redis streams
- Position Manager runs tick-by-tick from the 5s candle WS
- Trade decision flows: Chief Trader publishes → dashboard picks up → Position Manager opens/manages/closes

The HTML/JS section (line 510 onwards) stays intact but needs minor updates to show new indicators (Bollinger, Stoch RSI, ATR, trailing stop info, position duration).

**This is a large task. Implementation code will be written inline during execution — the structure is:**

```python
#!/usr/bin/env python3
"""
Expert Multi-Agent BTC Trading Dashboard
Agents: Technical, Sentiment, On-Chain, Order Flow, Chief Trader
Position Manager: Trailing stop (ATR×1.5) + signal reversal exit
Run: python3 dashboard.py → http://localhost:8080
"""
import asyncio, json, time
from datetime import datetime, timezone
from typing import Optional

import httpx, numpy as np, uvicorn, websockets
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from shared.redis_client import RedisClient
from shared.indicators import *
from shared.kelly import bet_size
from agents.technical.agent import TechnicalAgent
from agents.sentiment.agent import SentimentAgent
from agents.onchain.agent import OnChainAgent
from agents.orderflow.agent import OrderFlowAgent
from agents.chief_trader.agent import ChiefTrader
from agents.position.manager import PositionManager

app = FastAPI()
USE_REDIS = False  # Set True when Redis is available, False for in-process mode

# State: candles + position manager + trade history
# ... (in-process mode: agents compute directly without Redis)
# ... (Redis mode: agents publish/consume via streams)
```

**Step 2: Update HTML to show new indicators**

Add to the signal breakdown panel:
- Bollinger Band position
- Stoch RSI K/D
- ATR value
- Position status (OPEN/CLOSED, duration, trailing stop level, unrealized PnL)

**Step 3: Test manually**

```bash
source .venv/bin/activate && python3 dashboard.py
# Open http://localhost:8080
# Verify: candles display, trades every 30s, position stays open if profitable
```

**Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat: rewrite dashboard with multi-agent system + trailing stop"
```

---

### Task 12: Update models and CLAUDE.md

**Files:**
- Modify: `shared/models.py` — add `PositionState` model
- Modify: `CLAUDE.md` — update architecture section

**Step 1: Add PositionState model**

```python
# Add to shared/models.py
class PositionState(BaseModel):
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    current_price: float = 0.0
    stop_loss: float
    highest_price: float = 0.0
    lowest_price: float = float("inf")
    stake: float
    unrealized_pnl: float = 0.0
    duration_s: float = 0.0
    status: Literal["open", "closed"] = "open"
```

**Step 2: Update CLAUDE.md**

Update to reflect multi-agent architecture is now implemented.

**Step 3: Run all tests**

```bash
source .venv/bin/activate && python3 -m pytest tests/ -v
```
Expected: ALL PASSED

**Step 4: Commit**

```bash
git add shared/models.py CLAUDE.md
git commit -m "feat: add PositionState model, update CLAUDE.md"
```

---

### Task 13: Run all tests + final verification

**Step 1: Run full test suite**

```bash
source .venv/bin/activate && python3 -m pytest tests/ -v --tb=short
```
Expected: All tests pass

**Step 2: Start dashboard and verify**

```bash
source .venv/bin/activate && python3 dashboard.py
```

Verify:
- [ ] 5s candles display correctly
- [ ] Trade opens every 30s
- [ ] New indicators visible (Bollinger, Stoch RSI, ATR)
- [ ] Position stays open if profitable + signals confirm
- [ ] Trailing stop visible and moves up (LONG) / down (SHORT)
- [ ] Position closes on stop hit or signal reversal
- [ ] Circuit breakers still work (5 consecutive losses → pause)

**Step 3: Final commit**

```bash
git add -A
git commit -m "feat: expert multi-agent trading dashboard v1.0"
```
