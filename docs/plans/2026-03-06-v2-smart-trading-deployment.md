# V2 Smart Trading + Deployment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform the trading system from blind 60s-interval trading into a smart, adaptive, learning system — deployed on Render with PostgreSQL persistence, multi-asset support, and variable leverage.

**Architecture:** Replace fixed-interval trading with a setup-quality scoring system that only enters when conditions are strong. Add a trade journal (PostgreSQL) that tracks every decision + outcome, feeding a parameter optimizer that adjusts thresholds over time. Deploy backend on Render with Redis + PostgreSQL, frontend as static files served by FastAPI.

**Tech Stack:** Python/FastAPI, PostgreSQL (asyncpg), Redis, Binance WebSocket, Anthropic Claude API, Render PaaS

---

## Task 1: PostgreSQL Trade Journal — Schema + Repository

**Files:**
- Create: `shared/db.py`
- Create: `migrations/001_initial.sql`
- Test: `tests/test_db.py`

**Step 1: Write the failing test**

```python
# tests/test_db.py
import pytest
import asyncio
from shared.db import TradeRepository

@pytest.mark.asyncio
async def test_record_and_fetch_trade():
    repo = TradeRepository(fake=True)
    await repo.connect()
    trade_id = await repo.record_open(
        symbol="BTCUSDT", direction="LONG", entry_price=95000.0,
        stake=10.0, score=72, win_prob=0.68, confidence=0.78,
        atr=150.0, leverage=50, reasoning="Test trade"
    )
    assert trade_id >= 1
    trade = await repo.get_trade(trade_id)
    assert trade["symbol"] == "BTCUSDT"
    assert trade["direction"] == "LONG"
    assert trade["status"] == "OPEN"
    await repo.close()

@pytest.mark.asyncio
async def test_record_close():
    repo = TradeRepository(fake=True)
    await repo.connect()
    trade_id = await repo.record_open(
        symbol="BTCUSDT", direction="LONG", entry_price=95000.0,
        stake=10.0, score=72, win_prob=0.68, confidence=0.78,
        atr=150.0, leverage=50, reasoning="Test"
    )
    await repo.record_close(
        trade_id=trade_id, exit_price=95500.0, pnl=2.63,
        close_reason="trailing_stop", duration=120.5
    )
    trade = await repo.get_trade(trade_id)
    assert trade["status"] == "CLOSED"
    assert trade["pnl"] == 2.63
    assert trade["won"] is True
    await repo.close()

@pytest.mark.asyncio
async def test_get_recent_trades():
    repo = TradeRepository(fake=True)
    await repo.connect()
    for i in range(5):
        tid = await repo.record_open(
            symbol="BTCUSDT", direction="LONG", entry_price=95000.0 + i,
            stake=10.0, score=60, win_prob=0.6, confidence=0.65,
            atr=100.0, leverage=50, reasoning=f"Trade {i}"
        )
        await repo.record_close(
            trade_id=tid, exit_price=95100.0 + i, pnl=0.5,
            close_reason="test", duration=60.0
        )
    trades = await repo.get_recent_trades(limit=3)
    assert len(trades) == 3
    await repo.close()

@pytest.mark.asyncio
async def test_get_performance_stats():
    repo = TradeRepository(fake=True)
    await repo.connect()
    # 3 wins, 2 losses
    for i, (pnl, won) in enumerate([(1.0, True), (2.0, True), (-0.5, False), (1.5, True), (-1.0, False)]):
        tid = await repo.record_open(
            symbol="BTCUSDT", direction="LONG", entry_price=95000.0,
            stake=10.0, score=60, win_prob=0.6, confidence=0.65,
            atr=100.0, leverage=50, reasoning="Test"
        )
        await repo.record_close(
            trade_id=tid, exit_price=95100.0, pnl=pnl,
            close_reason="test", duration=60.0
        )
    stats = await repo.get_performance_stats()
    assert stats["total_trades"] == 5
    assert stats["win_rate"] == 0.6
    assert stats["total_pnl"] == 3.0
    await repo.close()
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/eden/Desktop/trade && python -m pytest tests/test_db.py -v`
Expected: FAIL (module not found)

**Step 3: Write the SQL migration**

```sql
-- migrations/001_initial.sql
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL DEFAULT 'BTCUSDT',
    direction VARCHAR(5) NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_price DOUBLE PRECISION,
    stake DOUBLE PRECISION NOT NULL,
    score INTEGER NOT NULL,
    win_prob DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    atr DOUBLE PRECISION NOT NULL,
    leverage INTEGER NOT NULL DEFAULT 50,
    reasoning TEXT DEFAULT '',
    pnl DOUBLE PRECISION,
    won BOOLEAN,
    close_reason VARCHAR(50),
    duration DOUBLE PRECISION,
    status VARCHAR(10) NOT NULL DEFAULT 'OPEN',
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    agent_signals JSONB
);

CREATE INDEX idx_trades_status ON trades(status);
CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_opened_at ON trades(opened_at DESC);
```

**Step 4: Write the repository**

```python
# shared/db.py
import os
import time
from typing import Optional

class TradeRepository:
    """Trade journal — PostgreSQL in production, in-memory dict for tests."""

    def __init__(self, database_url: str = None, fake: bool = False):
        self._url = database_url or os.environ.get("DATABASE_URL", "")
        self._fake = fake
        self._pool = None
        # Fake storage for tests
        self._fake_trades: list[dict] = []
        self._fake_id = 0

    async def connect(self):
        if self._fake:
            return
        import asyncpg
        self._pool = await asyncpg.create_pool(self._url, min_size=2, max_size=5)

    async def close(self):
        if self._pool:
            await self._pool.close()

    async def run_migrations(self):
        if self._fake:
            return
        migration_path = os.path.join(os.path.dirname(__file__), "..", "migrations", "001_initial.sql")
        with open(migration_path) as f:
            sql = f.read()
        async with self._pool.acquire() as conn:
            await conn.execute(sql)

    async def record_open(self, symbol: str, direction: str, entry_price: float,
                          stake: float, score: int, win_prob: float, confidence: float,
                          atr: float, leverage: int = 50, reasoning: str = "",
                          agent_signals: dict = None) -> int:
        if self._fake:
            self._fake_id += 1
            self._fake_trades.append({
                "id": self._fake_id, "symbol": symbol, "direction": direction,
                "entry_price": entry_price, "exit_price": None, "stake": stake,
                "score": score, "win_prob": win_prob, "confidence": confidence,
                "atr": atr, "leverage": leverage, "reasoning": reasoning,
                "pnl": None, "won": None, "close_reason": None, "duration": None,
                "status": "OPEN", "opened_at": time.time(), "closed_at": None,
                "agent_signals": agent_signals,
            })
            return self._fake_id
        async with self._pool.acquire() as conn:
            import json
            row = await conn.fetchrow(
                """INSERT INTO trades (symbol, direction, entry_price, stake, score,
                   win_prob, confidence, atr, leverage, reasoning, agent_signals)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING id""",
                symbol, direction, entry_price, stake, score, win_prob, confidence,
                atr, leverage, reasoning, json.dumps(agent_signals) if agent_signals else None
            )
            return row["id"]

    async def record_close(self, trade_id: int, exit_price: float, pnl: float,
                           close_reason: str, duration: float):
        if self._fake:
            for t in self._fake_trades:
                if t["id"] == trade_id:
                    t["exit_price"] = exit_price
                    t["pnl"] = pnl
                    t["won"] = pnl > 0
                    t["close_reason"] = close_reason
                    t["duration"] = duration
                    t["status"] = "CLOSED"
                    t["closed_at"] = time.time()
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE trades SET exit_price=$1, pnl=$2, won=$3, close_reason=$4,
                   duration=$5, status='CLOSED', closed_at=NOW() WHERE id=$6""",
                exit_price, pnl, pnl > 0, close_reason, duration, trade_id
            )

    async def get_trade(self, trade_id: int) -> Optional[dict]:
        if self._fake:
            for t in self._fake_trades:
                if t["id"] == trade_id:
                    return dict(t)
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM trades WHERE id=$1", trade_id)
            return dict(row) if row else None

    async def get_recent_trades(self, limit: int = 50, symbol: str = None) -> list[dict]:
        if self._fake:
            trades = self._fake_trades
            if symbol:
                trades = [t for t in trades if t["symbol"] == symbol]
            return [dict(t) for t in sorted(trades, key=lambda x: x["id"], reverse=True)[:limit]]
        async with self._pool.acquire() as conn:
            if symbol:
                rows = await conn.fetch(
                    "SELECT * FROM trades WHERE symbol=$1 ORDER BY opened_at DESC LIMIT $2",
                    symbol, limit)
            else:
                rows = await conn.fetch(
                    "SELECT * FROM trades ORDER BY opened_at DESC LIMIT $1", limit)
            return [dict(r) for r in rows]

    async def get_performance_stats(self, symbol: str = None, last_n: int = None) -> dict:
        if self._fake:
            trades = [t for t in self._fake_trades if t["status"] == "CLOSED"]
            if symbol:
                trades = [t for t in trades if t["symbol"] == symbol]
            if last_n:
                trades = sorted(trades, key=lambda x: x["id"], reverse=True)[:last_n]
            if not trades:
                return {"total_trades": 0, "win_rate": 0, "total_pnl": 0,
                        "avg_pnl": 0, "avg_winner": 0, "avg_loser": 0,
                        "best_trade": 0, "worst_trade": 0}
            wins = [t for t in trades if t["won"]]
            losses = [t for t in trades if not t["won"]]
            total_pnl = round(sum(t["pnl"] for t in trades), 4)
            return {
                "total_trades": len(trades),
                "win_rate": round(len(wins) / len(trades), 4),
                "total_pnl": total_pnl,
                "avg_pnl": round(total_pnl / len(trades), 4),
                "avg_winner": round(sum(t["pnl"] for t in wins) / len(wins), 4) if wins else 0,
                "avg_loser": round(sum(t["pnl"] for t in losses) / len(losses), 4) if losses else 0,
                "best_trade": max(t["pnl"] for t in trades),
                "worst_trade": min(t["pnl"] for t in trades),
            }
        # PostgreSQL version
        async with self._pool.acquire() as conn:
            query = "SELECT * FROM trades WHERE status='CLOSED'"
            params = []
            if symbol:
                query += " AND symbol=$1"
                params.append(symbol)
            query += " ORDER BY closed_at DESC"
            if last_n:
                query += f" LIMIT ${len(params)+1}"
                params.append(last_n)
            rows = await conn.fetch(query, *params)
            trades = [dict(r) for r in rows]
            if not trades:
                return {"total_trades": 0, "win_rate": 0, "total_pnl": 0,
                        "avg_pnl": 0, "avg_winner": 0, "avg_loser": 0,
                        "best_trade": 0, "worst_trade": 0}
            wins = [t for t in trades if t["won"]]
            losses = [t for t in trades if not t["won"]]
            total_pnl = round(sum(t["pnl"] for t in trades), 4)
            return {
                "total_trades": len(trades),
                "win_rate": round(len(wins) / len(trades), 4),
                "total_pnl": total_pnl,
                "avg_pnl": round(total_pnl / len(trades), 4),
                "avg_winner": round(sum(t["pnl"] for t in wins) / len(wins), 4) if wins else 0,
                "avg_loser": round(sum(t["pnl"] for t in losses) / len(losses), 4) if losses else 0,
                "best_trade": max(t["pnl"] for t in trades),
                "worst_trade": min(t["pnl"] for t in trades),
            }
```

**Step 5: Run test to verify it passes**

Run: `cd /Users/eden/Desktop/trade && python -m pytest tests/test_db.py -v`
Expected: PASS (4 tests)

**Step 6: Commit**

```bash
git add shared/db.py migrations/001_initial.sql tests/test_db.py
git commit -m "feat: add PostgreSQL trade journal with TradeRepository"
```

---

## Task 2: Smart Trading — Setup Quality Scoring + Adaptive Intervals

**Files:**
- Create: `shared/setup_scorer.py`
- Test: `tests/test_setup_scorer.py`

The core idea: don't trade every 60 seconds. Instead, score the current "setup quality" and only trade when conditions are strong. Check every 30s but require a minimum score to enter.

**Step 1: Write the failing test**

```python
# tests/test_setup_scorer.py
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
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/eden/Desktop/trade && python -m pytest tests/test_setup_scorer.py -v`
Expected: FAIL

**Step 3: Write the implementation**

```python
# shared/setup_scorer.py
"""
Setup Quality Scorer — Only trade when conditions are strong.
Replaces blind 60s-interval trading with intelligent entry detection.
"""

class SetupScorer:
    def __init__(self, min_quality: float = 0.55, min_wait_seconds: int = 120):
        self.min_quality = min_quality
        self.min_wait_seconds = min_wait_seconds

    def evaluate(self, tech: dict, sent: dict, chain: dict, flow: dict,
                 seconds_since_last_trade: float = float("inf")) -> dict:
        # Cooldown check
        if seconds_since_last_trade < self.min_wait_seconds:
            return {"should_trade": False, "quality": 0, "direction": None,
                    "wait_reason": "cooldown"}

        # Direction consensus
        dirs = []
        for agent in [tech, sent, chain, flow]:
            d = agent.get("direction", "neutral")
            if d in ("up", "LONG"):
                dirs.append("up")
            elif d in ("down", "SHORT"):
                dirs.append("down")
            else:
                dirs.append("neutral")

        up_count = dirs.count("up")
        down_count = dirs.count("down")
        neutral_count = dirs.count("neutral")

        # Determine direction
        if up_count > down_count:
            direction = "LONG"
            aligned_count = up_count
        elif down_count > up_count:
            direction = "SHORT"
            aligned_count = down_count
        else:
            # Tied — follow technical
            td = tech.get("direction", "neutral")
            direction = "LONG" if td in ("up", "LONG") else "SHORT"
            aligned_count = max(up_count, down_count)

        # Score components (0-1 each)
        # 1. Agreement strength (0-1): how many agents agree
        agreement = aligned_count / 4.0

        # 2. Conviction strength: how far scores are from 50
        scores = [int(tech.get("score", 50)), int(sent.get("score", 50)),
                  int(chain.get("score", 50)), int(flow.get("score", 50))]
        if direction == "LONG":
            conviction = sum(max(0, s - 50) for s in scores) / (50.0 * 4)
        else:
            conviction = sum(max(0, 50 - s) for s in scores) / (50.0 * 4)

        # 3. Average confidence from agents
        confidences = [float(a.get("confidence", 0.5)) for a in [tech, sent, chain, flow]]
        avg_confidence = sum(confidences) / len(confidences)

        # Weighted quality score
        quality = round(
            0.40 * agreement +
            0.35 * conviction +
            0.25 * avg_confidence,
            4
        )

        should_trade = quality >= self.min_quality and aligned_count >= 2

        return {
            "should_trade": should_trade,
            "quality": quality,
            "direction": direction,
            "agreement": aligned_count,
            "conviction": round(conviction, 4),
            "avg_confidence": round(avg_confidence, 4),
            "wait_reason": None,
        }
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/eden/Desktop/trade && python -m pytest tests/test_setup_scorer.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add shared/setup_scorer.py tests/test_setup_scorer.py
git commit -m "feat: add SetupScorer for intelligent trade entry timing"
```

---

## Task 3: Learning System — Parameter Optimizer

**Files:**
- Create: `shared/optimizer.py`
- Test: `tests/test_optimizer.py`

Tracks trade outcomes and adjusts parameters (min_quality threshold, agent weights, stop distance multiplier) based on what's actually working.

**Step 1: Write the failing test**

```python
# tests/test_optimizer.py
import pytest
from shared.optimizer import ParameterOptimizer

def test_initial_params():
    opt = ParameterOptimizer()
    params = opt.get_params()
    assert "min_quality" in params
    assert "stop_multiplier" in params
    assert "agent_weights" in params

def test_learn_from_winning_trades():
    opt = ParameterOptimizer()
    # Feed 10 winning trades with high quality setups
    for _ in range(10):
        opt.record_outcome(
            quality=0.75, direction="LONG", won=True, pnl=1.5,
            score=72, agent_agreement=3, stop_multiplier=0.8
        )
    params = opt.get_params()
    # After wins, optimizer should maintain or lower quality threshold
    assert params["min_quality"] <= 0.55

def test_learn_from_losing_trades():
    opt = ParameterOptimizer()
    # Feed 10 losing trades
    for _ in range(10):
        opt.record_outcome(
            quality=0.55, direction="LONG", won=False, pnl=-2.0,
            score=55, agent_agreement=2, stop_multiplier=0.8
        )
    params = opt.get_params()
    # After losses, should raise quality threshold (be more selective)
    assert params["min_quality"] > 0.55

def test_export_and_load():
    opt = ParameterOptimizer()
    for _ in range(5):
        opt.record_outcome(quality=0.7, direction="LONG", won=True, pnl=1.0,
                           score=70, agent_agreement=3, stop_multiplier=0.8)
    data = opt.export_state()
    opt2 = ParameterOptimizer()
    opt2.load_state(data)
    assert opt2.get_params() == opt.get_params()
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/eden/Desktop/trade && python -m pytest tests/test_optimizer.py -v`

**Step 3: Write the implementation**

```python
# shared/optimizer.py
"""
Parameter Optimizer — learns from trade outcomes to adjust trading parameters.
Simple exponential moving average approach — no heavy ML, just adaptive thresholds.
"""

class ParameterOptimizer:
    def __init__(self):
        self._outcomes: list[dict] = []
        self._params = {
            "min_quality": 0.55,
            "stop_multiplier": 0.8,
            "agent_weights": {"tech": 0.35, "sent": 0.20, "chain": 0.20, "flow": 0.25},
            "max_stake_pct": 0.25,
            "min_agreement": 2,
        }
        self._alpha = 0.1  # learning rate

    def record_outcome(self, quality: float, direction: str, won: bool,
                       pnl: float, score: int, agent_agreement: int,
                       stop_multiplier: float):
        self._outcomes.append({
            "quality": quality, "direction": direction, "won": won,
            "pnl": pnl, "score": score, "agreement": agent_agreement,
            "stop_mult": stop_multiplier,
        })
        self._update_params()

    def _update_params(self):
        if len(self._outcomes) < 5:
            return
        recent = self._outcomes[-20:]  # last 20 trades
        wins = [o for o in recent if o["won"]]
        losses = [o for o in recent if not o["won"]]
        win_rate = len(wins) / len(recent)

        # Adjust min_quality: losing too much → be more selective
        if win_rate < 0.45:
            self._params["min_quality"] = min(0.75,
                self._params["min_quality"] + self._alpha * 0.05)
        elif win_rate > 0.60:
            self._params["min_quality"] = max(0.40,
                self._params["min_quality"] - self._alpha * 0.03)

        # Adjust stop multiplier: if stops are too tight (many trailing_stop losses)
        stop_losses = [o for o in losses if o.get("close_reason") == "trailing_stop"]
        if len(losses) > 0 and len(stop_losses) / max(len(losses), 1) > 0.6:
            self._params["stop_multiplier"] = min(1.2,
                self._params["stop_multiplier"] + self._alpha * 0.1)
        elif win_rate > 0.55:
            self._params["stop_multiplier"] = max(0.6,
                self._params["stop_multiplier"] - self._alpha * 0.05)

        # Adjust max stake: losing streak → reduce risk
        if win_rate < 0.40:
            self._params["max_stake_pct"] = max(0.10,
                self._params["max_stake_pct"] - self._alpha * 0.02)
        elif win_rate > 0.55:
            self._params["max_stake_pct"] = min(0.30,
                self._params["max_stake_pct"] + self._alpha * 0.01)

    def get_params(self) -> dict:
        return dict(self._params)

    def export_state(self) -> dict:
        return {"params": dict(self._params), "outcomes": list(self._outcomes)}

    def load_state(self, data: dict):
        self._params = data.get("params", self._params)
        self._outcomes = data.get("outcomes", [])
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/eden/Desktop/trade && python -m pytest tests/test_optimizer.py -v`

**Step 5: Commit**

```bash
git add shared/optimizer.py tests/test_optimizer.py
git commit -m "feat: add ParameterOptimizer for adaptive trade parameters"
```

---

## Task 4: Multi-Asset Support

**Files:**
- Create: `shared/symbols.py`
- Test: `tests/test_symbols.py`
- Modify: `dashboard.py` — replace hardcoded "BTCUSDT" with configurable symbols

**Step 1: Write the failing test**

```python
# tests/test_symbols.py
from shared.symbols import SymbolConfig, get_symbols

def test_default_symbols():
    symbols = get_symbols()
    assert "BTCUSDT" in [s.symbol for s in symbols]

def test_symbol_config():
    s = SymbolConfig(symbol="ETHUSDT", leverage=25, ws_stream="ethusdt@kline_1s")
    assert s.symbol == "ETHUSDT"
    assert s.leverage == 25
    assert s.ws_stream == "ethusdt@kline_1s"

def test_symbol_from_env(monkeypatch):
    monkeypatch.setenv("TRADE_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT")
    symbols = get_symbols()
    assert len(symbols) == 3
    assert symbols[1].symbol == "ETHUSDT"
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/eden/Desktop/trade && python -m pytest tests/test_symbols.py -v`

**Step 3: Write the implementation**

```python
# shared/symbols.py
import os
from dataclasses import dataclass

LEVERAGE_DEFAULTS = {
    "BTCUSDT": 50, "ETHUSDT": 30, "SOLUSDT": 20,
    "BNBUSDT": 20, "XRPUSDT": 15, "DOGEUSDT": 10,
}

@dataclass
class SymbolConfig:
    symbol: str
    leverage: int = 50
    ws_stream: str = ""

    def __post_init__(self):
        if not self.ws_stream:
            self.ws_stream = f"{self.symbol.lower()}@kline_1s"
        if self.leverage == 50 and self.symbol in LEVERAGE_DEFAULTS:
            self.leverage = LEVERAGE_DEFAULTS[self.symbol]

def get_symbols() -> list[SymbolConfig]:
    env = os.environ.get("TRADE_SYMBOLS", "BTCUSDT")
    symbols = [s.strip() for s in env.split(",") if s.strip()]
    return [SymbolConfig(symbol=s) for s in symbols]
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/eden/Desktop/trade && python -m pytest tests/test_symbols.py -v`

**Step 5: Commit**

```bash
git add shared/symbols.py tests/test_symbols.py
git commit -m "feat: add multi-asset symbol configuration"
```

---

## Task 5: Variable Leverage

**Files:**
- Create: `shared/leverage.py`
- Test: `tests/test_leverage.py`

Adjust leverage based on confidence, volatility, and win streak — high confidence + low volatility = more leverage.

**Step 1: Write the failing test**

```python
# tests/test_leverage.py
from shared.leverage import compute_leverage

def test_high_confidence_low_vol():
    lev = compute_leverage(confidence=0.85, atr_pct=0.3, win_streak=3, max_leverage=50)
    assert lev >= 30  # high confidence → higher leverage

def test_low_confidence_high_vol():
    lev = compute_leverage(confidence=0.55, atr_pct=1.5, win_streak=0, max_leverage=50)
    assert lev <= 15  # low confidence + high vol → lower leverage

def test_never_exceeds_max():
    lev = compute_leverage(confidence=0.99, atr_pct=0.1, win_streak=10, max_leverage=50)
    assert lev <= 50

def test_minimum_leverage():
    lev = compute_leverage(confidence=0.50, atr_pct=3.0, win_streak=0, max_leverage=50)
    assert lev >= 5  # never go below 5x
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/eden/Desktop/trade && python -m pytest tests/test_leverage.py -v`

**Step 3: Write the implementation**

```python
# shared/leverage.py
def compute_leverage(confidence: float, atr_pct: float, win_streak: int,
                     max_leverage: int = 50) -> int:
    """
    Dynamic leverage based on:
    - confidence (0-1): higher = more leverage
    - atr_pct: ATR as % of price (higher = more volatile = less leverage)
    - win_streak: consecutive wins boost leverage slightly
    """
    # Base leverage from confidence (5-40 range)
    base = 5 + (confidence - 0.5) * 70  # 0.5→5, 0.85→29.5, 1.0→40
    base = max(5, min(40, base))

    # Volatility discount: high ATR% → reduce leverage
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
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/eden/Desktop/trade && python -m pytest tests/test_leverage.py -v`

**Step 5: Commit**

```bash
git add shared/leverage.py tests/test_leverage.py
git commit -m "feat: add dynamic leverage calculator"
```

---

## Task 6: Integrate Smart Trading + Persistence into Dashboard

**Files:**
- Modify: `dashboard.py` — rewire `run_chief_loop()` to use SetupScorer, TradeRepository, ParameterOptimizer, variable leverage

This is the main integration task. The chief loop changes from "trade every 60s" to "evaluate setup every 30s, only trade when quality is high enough."

**Step 1: Write the failing test**

```python
# tests/test_smart_loop.py
import pytest
from shared.setup_scorer import SetupScorer
from shared.optimizer import ParameterOptimizer

def test_smart_loop_integration():
    """Test the decision pipeline: scorer → optimizer → trade/skip."""
    scorer = SetupScorer(min_quality=0.55, min_wait_seconds=120)
    optimizer = ParameterOptimizer()

    # Strong setup — should trade
    result = scorer.evaluate(
        tech={"score": 75, "direction": "up", "atr": 150, "confidence": 0.8},
        sent={"score": 70, "direction": "up", "confidence": 0.7},
        chain={"score": 68, "direction": "up", "confidence": 0.65},
        flow={"score": 72, "direction": "up", "confidence": 0.7},
        seconds_since_last_trade=300,
    )
    assert result["should_trade"] is True

    # Record outcome for optimizer
    optimizer.record_outcome(
        quality=result["quality"], direction=result["direction"],
        won=True, pnl=1.5, score=72, agent_agreement=result["agreement"],
        stop_multiplier=0.8
    )
    params = optimizer.get_params()
    assert params["min_quality"] > 0  # still valid
```

**Step 2: Run test**

Run: `cd /Users/eden/Desktop/trade && python -m pytest tests/test_smart_loop.py -v`

**Step 3: Modify `dashboard.py`**

Key changes to `run_chief_loop()`:
1. Replace fixed cooldown with `SetupScorer.evaluate()`
2. Add `TradeRepository` for persistence (record every open/close)
3. Use `ParameterOptimizer` to adjust thresholds
4. Use `compute_leverage()` for variable leverage
5. Check interval: every 30s (not every 1s)

Changes to `_record_open()` and `_record_close()`:
1. Call `trade_repo.record_open()` / `trade_repo.record_close()`
2. Feed outcomes to optimizer

Changes to startup:
1. Initialize `TradeRepository`, connect, run migrations
2. Load optimizer state from DB if available

**Step 4: Run all tests**

Run: `cd /Users/eden/Desktop/trade && python -m pytest -v`
Expected: All pass

**Step 5: Commit**

```bash
git add dashboard.py tests/test_smart_loop.py
git commit -m "feat: integrate smart trading, persistence, and variable leverage into dashboard"
```

---

## Task 7: Render Deployment Configuration

**Files:**
- Create: `render.yaml`
- Create: `Procfile`
- Create: `.env.example`
- Modify: `dashboard.py` — add `DATABASE_URL` / `REDIS_URL` env var support

**Step 1: Create render.yaml**

```yaml
# render.yaml
services:
  - type: web
    name: trade-dashboard
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn dashboard:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT --timeout 120
    envVars:
      - key: PYTHONUNBUFFERED
        value: "1"
      - key: COOLDOWN_SECONDS
        value: "60"
      - key: INITIAL_BANKROLL
        value: "100"
      - key: TRADE_SYMBOLS
        value: "BTCUSDT"
      - key: DATABASE_URL
        fromDatabase:
          name: trade-db
          property: connectionString
      - key: REDIS_URL
        fromService:
          type: redis
          name: trade-redis
          property: connectionString
      - key: ANTHROPIC_API_KEY
        sync: false
    healthCheckPath: /api/health

databases:
  - name: trade-db
    plan: starter
    databaseName: trade
    postgresMajorVersion: "16"

services:
  - type: redis
    name: trade-redis
    plan: starter
    maxmemoryPolicy: allkeys-lru
```

**Step 2: Create Procfile**

```
web: gunicorn dashboard:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT --timeout 120
```

**Step 3: Create .env.example**

```
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=postgresql://localhost:5432/trade
REDIS_URL=redis://localhost:6379
COOLDOWN_SECONDS=60
INITIAL_BANKROLL=100
TRADE_SYMBOLS=BTCUSDT
```

**Step 4: Add health check endpoint to dashboard.py**

```python
@app.get("/api/health")
async def health():
    return {"status": "ok", "uptime": time.time() - state.start_time}
```

**Step 5: Add gunicorn to requirements.txt**

Add `gunicorn>=22.0` to requirements.txt.

**Step 6: Commit**

```bash
git add render.yaml Procfile .env.example requirements.txt dashboard.py
git commit -m "feat: add Render deployment configuration"
```

---

## Task 8: Load Persisted State on Startup

**Files:**
- Modify: `dashboard.py` — on startup, load bankroll, trade history, and optimizer state from PostgreSQL

**Step 1: Modify startup function**

In `dashboard.py` startup:
1. Connect `TradeRepository` to `DATABASE_URL`
2. Run migrations
3. Load last known bankroll from most recent trade
4. Load optimizer state
5. Populate `state.trades` from DB (last 200)

**Step 2: Test manually**

Run locally with SQLite or a local PostgreSQL. Verify:
- App starts, connects to DB
- Previous trades show up in dashboard
- Bankroll resumes from last value

**Step 3: Commit**

```bash
git add dashboard.py
git commit -m "feat: load persisted state from PostgreSQL on startup"
```

---

## Task 9: Performance Stats API + Dashboard Display

**Files:**
- Modify: `dashboard.py` — add `/api/stats` endpoint, add stats panel to frontend HTML

**Step 1: Add stats endpoint**

```python
@app.get("/api/stats")
async def get_stats():
    stats = await trade_repo.get_performance_stats()
    optimizer_params = optimizer.get_params()
    return {
        "performance": stats,
        "optimizer": optimizer_params,
        "uptime": time.time() - state.start_time,
    }
```

**Step 2: Add stats panel to frontend**

Add a section showing:
- Win rate, total PnL, avg winner/loser
- Current optimizer parameters (min_quality, leverage mode)
- Trades skipped (setups rejected)

**Step 3: Commit**

```bash
git add dashboard.py
git commit -m "feat: add performance stats API and dashboard panel"
```

---

## Task 10: Final Integration Test + Cleanup

**Files:**
- All test files

**Step 1: Run full test suite**

Run: `cd /Users/eden/Desktop/trade && python -m pytest -v`
Expected: All tests pass

**Step 2: Run the app locally and verify**

Run: `cd /Users/eden/Desktop/trade && PYTHONUNBUFFERED=1 python3 -u dashboard.py`

Verify:
- Dashboard loads at http://localhost:8080
- Agents produce signals
- SetupScorer evaluates quality (check terminal output)
- Trades only open when quality >= threshold (not every 60s)
- Trade journal records opens/closes
- Stats panel shows win rate
- Position manager still does tick-by-tick trailing stops

**Step 3: Commit final state**

```bash
git add -A
git commit -m "feat: v2 complete — smart trading, learning, persistence, multi-asset"
```

---

## Summary

| Task | What | New Files | Tests |
|------|------|-----------|-------|
| 1 | PostgreSQL Trade Journal | `shared/db.py`, `migrations/001_initial.sql` | 4 |
| 2 | Setup Quality Scorer | `shared/setup_scorer.py` | 5 |
| 3 | Parameter Optimizer | `shared/optimizer.py` | 4 |
| 4 | Multi-Asset Config | `shared/symbols.py` | 3 |
| 5 | Variable Leverage | `shared/leverage.py` | 4 |
| 6 | Dashboard Integration | modify `dashboard.py` | 1 |
| 7 | Render Deployment | `render.yaml`, `Procfile`, `.env.example` | 0 |
| 8 | Persist State on Startup | modify `dashboard.py` | 0 |
| 9 | Stats API + Panel | modify `dashboard.py` | 0 |
| 10 | Final Test + Verify | — | all |
