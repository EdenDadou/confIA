#!/usr/bin/env python3
"""
Expert Multi-Agent BTC Trading Dashboard
Agents: Kenji (Technical), Sarah (Sentiment), Viktor (On-Chain), Diego (Order Flow)
Chief Trader: Amir (Sonnet) — Position Manager: Yuki (Trailing Stop)
COOLDOWN_SECONDS env var to change trade interval (default: 60)
Run:  python3 dashboard.py  →  http://localhost:8080
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import numpy as np
import uvicorn
import websockets
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from shared.indicators import compute_rsi, compute_macd, compute_bollinger, compute_atr, compute_vwap
from shared.indicators import compute_ema, compute_stoch_rsi, compute_obv, compute_momentum, detect_divergence
from shared.kelly import bet_size
from shared.setup_scorer import SetupScorer
from shared.optimizer import ParameterOptimizer
from shared.leverage import compute_leverage
from shared.db import TradeRepository
from agents.technical.agent import TechnicalAgent
from agents.sentiment.agent import SentimentAgent
from agents.onchain.agent import OnChainAgent
from agents.orderflow.agent import OrderFlowAgent
from agents.chief_trader.agent import ChiefTrader
from agents.position.manager import PositionManager

app = FastAPI()

# ─────────────────────────────────────────────────────────
# Config (modifiable via env vars)
# ─────────────────────────────────────────────────────────

COOLDOWN_SECONDS = int(os.environ.get("COOLDOWN_SECONDS", "60"))
INITIAL_BANKROLL = float(os.environ.get("INITIAL_BANKROLL", "100"))
MIN_WAIT_SECONDS = int(os.environ.get("MIN_WAIT_SECONDS", "60"))
MIN_QUALITY = float(os.environ.get("MIN_QUALITY", "0.40"))

# ─────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────


class TradingState:
    def __init__(self, initial: float = INITIAL_BANKROLL):
        self.initial = initial
        self.bankroll = initial
        self.trades: list[dict] = []
        self.consecutive_losses = 0
        self.last_trade_ts: float = time.time()  # enforce initial cooldown
        self.bankroll_history: list[dict] = [{"time": int(time.time()), "value": initial}]
        self.analysis_log: list[dict] = []
        self.candles_5s: list[dict] = []
        self._5s_bucket: Optional[dict] = None
        self.latest_price: float = 0.0
        # Agent signals (latest from each)
        self.agent_signals: dict = {
            "technical": {"score": 50, "direction": "neutral", "reasoning": "Waiting for data...", "confidence": 0.5, "atr": 100},
            "sentiment": {"score": 50, "direction": "neutral", "reasoning": "Waiting for data...", "confidence": 0.5, "fear_greed": 50},
            "onchain": {"score": 50, "direction": "neutral", "reasoning": "Waiting for data...", "confidence": 0.5},
            "orderflow": {"score": 50, "direction": "neutral", "reasoning": "Waiting for data...", "confidence": 0.5, "ob_ratio": 0.5, "funding_rate": 0.0001},
        }
        self.chief_decision: dict = {"score": 50, "direction": "LONG", "reasoning": "Waiting...", "confidence": 0.5}
        self.setups_skipped: int = 0
        self.setups_taken: int = 0
        self.start_time: float = time.time()
        self.current_trade_db_id: Optional[int] = None
        self.win_streak: int = 0


state = TradingState()
tech_agent = TechnicalAgent(fake_redis=True)
sent_agent = SentimentAgent(fake_redis=True)
chain_agent = OnChainAgent(fake_redis=True)
flow_agent = OrderFlowAgent(fake_redis=True)
chief = ChiefTrader(fake_redis=True, bankroll=INITIAL_BANKROLL, cooldown=COOLDOWN_SECONDS)
pos_mgr = PositionManager(fake_redis=True)
setup_scorer = SetupScorer(min_quality=MIN_QUALITY, min_wait_seconds=MIN_WAIT_SECONDS)
optimizer = ParameterOptimizer()
trade_repo = TradeRepository(fake=not os.environ.get("DATABASE_URL", ""))


# ─────────────────────────────────────────────────────────
# 10-Second Candle WebSocket (Binance kline_1s → 10s buckets)
# ─────────────────────────────────────────────────────────

CANDLE_INTERVAL = 10  # seconds per candle

async def ws_10s_candles():
    uri = "wss://stream.binance.com:9443/ws/btcusdt@kline_1s"
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, close_timeout=5) as ws:
                async for raw in ws:
                    data = json.loads(raw)
                    k = data.get("k", {})
                    ts_1s = int(k["t"]) // 1000
                    ts_bucket = (ts_1s // CANDLE_INTERVAL) * CANDLE_INTERVAL
                    o = float(k["o"]); h = float(k["h"])
                    l = float(k["l"]); c = float(k["c"])
                    v = float(k["v"])

                    bucket = state._5s_bucket
                    if bucket is None or bucket["time"] != ts_bucket:
                        if bucket is not None:
                            state.candles_5s.append(bucket)
                            if len(state.candles_5s) > 2000:
                                state.candles_5s.pop(0)
                        state._5s_bucket = {"time": ts_bucket, "open": o, "high": h,
                                            "low": l, "close": c, "volume": v}
                    else:
                        bucket["high"] = max(bucket["high"], h)
                        bucket["low"] = min(bucket["low"], l)
                        bucket["close"] = c
                        bucket["volume"] += v

                    # Update latest price for quick access
                    state.latest_price = c

                    # Tick-by-tick position management
                    if pos_mgr.has_position:
                        tick_result = pos_mgr.update_tick(c)
                        if tick_result.get("action") == "CLOSE":
                            close_result = pos_mgr.close_position(c, reason=tick_result["reason"])
                            await _record_close(close_result)
        except Exception as e:
            print(f"[WS] Error: {e}")
            await asyncio.sleep(3)


# ─────────────────────────────────────────────────────────
# Background Agent Loops
# ─────────────────────────────────────────────────────────

async def run_technical_loop():
    while True:
        try:
            candles = await fetch_klines(60)
            if candles and len(candles) >= 2:
                indicators = tech_agent.compute_indicators(candles)
                interpretation = await tech_agent.interpret(indicators)
                interpretation["atr"] = indicators["atr"]
                interpretation["indicators"] = indicators
                state.agent_signals["technical"] = interpretation
        except Exception as e:
            print(f"[TECH] Error: {e}")
        await asyncio.sleep(5)


async def run_sentiment_loop():
    while True:
        try:
            fg = await sent_agent.fetch_fear_greed()
            interpretation = await sent_agent.interpret(fg)
            interpretation["fear_greed"] = fg
            state.agent_signals["sentiment"] = interpretation
        except Exception as e:
            print(f"[SENT] Error: {e}")
        await asyncio.sleep(300)


async def run_onchain_loop():
    while True:
        try:
            futures = await chain_agent.fetch_binance_futures()
            futures["liquidations"] = {"long": 0, "short": 0}
            interpretation = await chain_agent.interpret(futures)
            interpretation["funding_rate"] = futures["funding_rate"]
            interpretation["long_short_ratio"] = futures["long_short_ratio"]
            state.agent_signals["onchain"] = interpretation
        except Exception as e:
            print(f"[CHAIN] Error: {e}")
        await asyncio.sleep(45)


async def run_orderflow_loop():
    while True:
        try:
            data = await flow_agent.fetch_mexc_data()
            interpretation = await flow_agent.interpret(data)
            interpretation["ob_ratio"] = data["ob_ratio"]
            interpretation["funding_rate"] = data["funding_rate"]
            interpretation["mark_price"] = data["mark_price"]
            interpretation["index_price"] = data["index_price"]
            interpretation["bids"] = data["bids"]
            interpretation["asks"] = data["asks"]
            interpretation["spread_bps"] = data.get("spread_bps", 2.0)
            state.agent_signals["orderflow"] = interpretation
        except Exception as e:
            print(f"[FLOW] Error: {e}")
        await asyncio.sleep(5)


async def run_chief_loop():
    """Smart chief trader loop — evaluates setup quality every 30s, only trades when conditions are strong."""
    while True:
        try:
            now = time.time()
            price = state.latest_price or 0

            # ── If position is open: manage it ──
            if pos_mgr.has_position and price > 0:
                # No max hold time — let winners run, trailing stop handles exit
                if pos_mgr.check_signal_exit(state.chief_decision).get("action") == "CLOSE":
                    close_result = pos_mgr.close_position(price, reason="signal_reversal")
                    await _record_close(close_result)
                elif (now - state.last_trade_ts) >= COOLDOWN_SECONDS:
                    review = pos_mgr.check_cooldown_review(price, state.chief_decision)
                    if review["action"] == "CLOSE":
                        close_result = pos_mgr.close_position(price, reason=review["reason"])
                        await _record_close(close_result)
                await asyncio.sleep(1)
                continue

            # ── No position: evaluate setup quality ──
            tech = state.agent_signals["technical"]
            sent = state.agent_signals["sentiment"]
            chain = state.agent_signals["onchain"]
            flow = state.agent_signals["orderflow"]

            # Use optimizer-tuned min_quality
            params = optimizer.get_params()
            setup_scorer.min_quality = params["min_quality"]
            setup_scorer.min_wait_seconds = MIN_WAIT_SECONDS

            seconds_since = now - state.last_trade_ts
            setup = setup_scorer.evaluate(tech, sent, chain, flow, seconds_since)

            if not setup["should_trade"]:
                reason = setup.get("wait_reason") or f"quality={setup['quality']:.2f}<{params['min_quality']:.2f}"
                if setup.get("wait_reason") != "cooldown":
                    state.setups_skipped += 1
                    print(f"[CHIEF] Setup skipped: {reason} (agreement={setup.get('agreement', 0)})")
                await asyncio.sleep(30)  # check every 30s, not every 1s
                continue

            # Check circuit breakers
            chief.bankroll = state.bankroll
            chief.initial = state.initial
            chief.consecutive_losses = state.consecutive_losses
            cb = chief.check_circuit_breakers()
            if cb:
                print(f"[CHIEF] Circuit breaker: {cb['reason']}")
                state.analysis_log.insert(0, {
                    "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    "reason": cb["reason"], "score": 0, "win_prob": 0,
                })
                if len(state.analysis_log) > 30:
                    state.analysis_log.pop()
                await asyncio.sleep(30)
                continue

            # Good setup — synthesize and trade
            decision = await chief.synthesize(tech, sent, chain, flow)
            state.chief_decision = decision
            atr = float(tech.get("atr", 100))
            trade_plan = chief.compute_trade(decision, atr)

            # Variable leverage
            atr_pct = atr / price * 100 if price > 0 else 1.0
            leverage = compute_leverage(
                confidence=trade_plan["confidence"],
                atr_pct=atr_pct,
                win_streak=state.win_streak,
                max_leverage=50,
            )

            entry_price = price if price > 0 else (await fetch_klines(2) or [{}])[-1].get("close", 0)
            if entry_price > 0:
                pos_mgr.open_position(
                    direction=trade_plan["direction"],
                    price=entry_price,
                    stake=trade_plan["stake"],
                    atr=atr,
                    reasoning=trade_plan.get("reasoning", ""),
                    score=trade_plan["score"],
                    win_prob=trade_plan["win_prob"],
                    stop_multiplier=params.get("stop_multiplier", 0.8),
                )
                state.last_trade_ts = now
                state.setups_taken += 1
                await _record_open(trade_plan, entry_price, setup["quality"], leverage)
                print(f"[CHIEF] Opened {trade_plan['direction']} ${trade_plan['stake']:.2f} @{entry_price:.2f} "
                      f"(quality={setup['quality']:.2f}, score={trade_plan['score']}, lev={leverage}x, wp={trade_plan['win_prob']:.1%})")
        except Exception as e:
            print(f"[CHIEF] Error: {e}")
        await asyncio.sleep(1)


async def _record_open(trade_plan: dict, price: float, quality: float = 0, leverage: int = 50):
    trade = {
        "id": len(state.trades) + 1,
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "ts": int(time.time()),
        "direction": trade_plan["direction"],
        "stake": trade_plan["stake"],
        "score": trade_plan["score"],
        "win_prob": trade_plan["win_prob"],
        "won": None,
        "profit": 0,
        "bankroll": state.bankroll,
        "entry_price": price,
        "reasoning": trade_plan.get("reasoning", ""),
        "divergence": "none",
        "status": "OPEN",
        "quality": round(quality, 3),
        "leverage": leverage,
        "agent_directions": {
            "tech": state.agent_signals["technical"].get("direction", "neutral"),
            "sent": state.agent_signals["sentiment"].get("direction", "neutral"),
            "chain": state.agent_signals["onchain"].get("direction", "neutral"),
            "flow": state.agent_signals["orderflow"].get("direction", "neutral"),
        },
    }
    state.trades.insert(0, trade)
    if len(state.trades) > 200:
        state.trades.pop()
    # Persist to DB
    try:
        db_id = await trade_repo.record_open(
            symbol="BTCUSDT", direction=trade_plan["direction"],
            entry_price=price, stake=trade_plan["stake"],
            score=trade_plan["score"], win_prob=trade_plan["win_prob"],
            confidence=trade_plan.get("confidence", 0.5),
            atr=trade_plan.get("atr", 100), leverage=leverage,
            reasoning=trade_plan.get("reasoning", ""),
        )
        state.current_trade_db_id = db_id
    except Exception as e:
        print(f"[DB] Error recording open: {e}")


async def _record_close(close_result: dict):
    if close_result.get("action") != "CLOSED":
        return
    pnl = close_result["pnl"]
    won = close_result["won"]

    # Update bankroll
    state.bankroll = round(state.bankroll + pnl, 2)
    state.consecutive_losses = 0 if won else state.consecutive_losses + 1
    state.win_streak = state.win_streak + 1 if won else 0
    state.bankroll_history.append({"time": int(time.time()), "value": state.bankroll})
    if len(state.bankroll_history) > 200:
        state.bankroll_history.pop(0)

    # Update the matching OPEN trade entry
    for t in state.trades:
        if t.get("status") == "OPEN":
            t["won"] = won
            t["profit"] = round(pnl, 2)
            t["bankroll"] = state.bankroll
            t["status"] = "CLOSED"
            t["exit_price"] = close_result.get("exit_price", 0)
            t["close_reason"] = close_result.get("reason", "")
            t["close_time"] = datetime.now(timezone.utc).strftime("%H:%M:%S")
            t["duration"] = close_result.get("duration", 0)
            break

    # Persist to DB
    try:
        if state.current_trade_db_id:
            await trade_repo.record_close(
                trade_id=state.current_trade_db_id,
                exit_price=close_result.get("exit_price", 0),
                pnl=pnl, close_reason=close_result.get("reason", ""),
                duration=close_result.get("duration", 0),
            )
            state.current_trade_db_id = None
    except Exception as e:
        print(f"[DB] Error recording close: {e}")

    # Feed optimizer with real data from the trade
    closed_trade = next((t for t in state.trades if t.get("status") == "CLOSED" and t.get("close_time")), {})
    quality = closed_trade.get("quality", 0.5)
    agent_dirs = closed_trade.get("agent_directions", {})
    trade_dir = close_result.get("direction", "LONG")
    # Count how many agents agreed with the trade direction
    dir_map = {"LONG": ("up", "LONG"), "SHORT": ("down", "SHORT")}
    aligned = sum(1 for d in agent_dirs.values() if d in dir_map.get(trade_dir, ()))
    params = optimizer.get_params()
    optimizer.record_outcome(
        quality=quality, direction=trade_dir,
        won=won, pnl=pnl, score=close_result.get("score", 50),
        agent_agreement=aligned, stop_multiplier=params["stop_multiplier"],
        agent_directions=agent_dirs,
    )
    print(f"[CHIEF] Closed {close_result.get('direction','?')} pnl=${pnl:+.2f} reason={close_result.get('reason','?')} "
          f"bankroll=${state.bankroll:.2f} (optimizer: min_q={optimizer.get_params()['min_quality']:.2f})")

    # Persist optimizer state
    try:
        import json as _json
        opt_file = os.path.join(os.path.dirname(__file__), "data", "optimizer.json")
        os.makedirs(os.path.dirname(opt_file), exist_ok=True)
        with open(opt_file, "w") as f:
            _json.dump(optimizer.export_state(), f, indent=2)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────
# External API Fetcher (kept for candles)
# ─────────────────────────────────────────────────────────

async def fetch_klines(limit: int = 60) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "5m", "limit": limit},
            )
            r.raise_for_status()
            return [
                {"time": int(k[0]) // 1000,
                 "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                for k in r.json()
            ]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────

async def seed_historical_candles():
    """Seed chart with historical 1m candles so it's never empty on startup."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "1m", "limit": 500},
            )
            r.raise_for_status()
            seen_times = set()
            for k in r.json():
                t = int(k[0]) // 1000
                if t not in seen_times:
                    seen_times.add(t)
                    state.candles_5s.append({
                        "time": t,
                        "open": float(k[1]), "high": float(k[2]),
                        "low": float(k[3]), "close": float(k[4]),
                        "volume": float(k[5]),
                    })
            print(f"[CHART] Seeded {len(state.candles_5s)} historical candles")
    except Exception as e:
        print(f"[CHART] Failed to seed historical candles: {e}")


@app.on_event("startup")
async def startup():
    # Connect trade journal
    try:
        await trade_repo.connect()
        await trade_repo.run_migrations()
        print("[DB] Trade repository connected")
    except Exception as e:
        print(f"[DB] Not connected (using in-memory): {e}")
    # Load optimizer state from previous sessions
    try:
        import json as _json
        opt_file = os.path.join(os.path.dirname(__file__), "data", "optimizer.json")
        if os.path.exists(opt_file):
            with open(opt_file) as f:
                optimizer.load_state(_json.load(f))
            print(f"[OPT] Loaded optimizer state ({len(optimizer._outcomes)} past trades)")
    except Exception:
        pass

    # Restore trade history and bankroll from previous sessions
    try:
        past_trades = await trade_repo.get_recent_trades(limit=500)
        if past_trades:
            closed = [t for t in past_trades if t["status"] == "CLOSED"]
            total_pnl = sum(t.get("pnl", 0) for t in closed)
            state.bankroll = INITIAL_BANKROLL + total_pnl
            state.initial = INITIAL_BANKROLL
            state.wins = sum(1 for t in closed if t.get("won"))
            state.losses = sum(1 for t in closed if not t.get("won"))
            state.total_trades = len(closed)
            state.win_rate = round(state.wins / state.total_trades * 100) if state.total_trades > 0 else 0
            state.pnl = total_pnl
            state.pnl_pct = round(total_pnl / INITIAL_BANKROLL * 100, 2)
            # Rebuild trade log for UI
            for t in reversed(past_trades):
                state.trades.append({
                    "id": t["id"], "direction": t["direction"],
                    "score": t.get("score", 0), "win_prob": t.get("win_prob", 0.5),
                    "stake": t.get("stake", 0), "entry_price": t.get("entry_price", 0),
                    "exit_price": t.get("exit_price"), "won": t.get("won"),
                    "profit": t.get("pnl", 0), "bankroll": state.bankroll,
                    "time": "", "status": t["status"],
                    "close_reason": t.get("close_reason", ""),
                    "reasoning": t.get("reasoning", ""),
                })
            # Mark any OPEN trade as abandoned (server restarted)
            for t in past_trades:
                if t["status"] == "OPEN":
                    await trade_repo.record_close(
                        trade_id=t["id"], exit_price=t["entry_price"],
                        pnl=0, close_reason="server_restart", duration=0)
            print(f"[DB] Restored {len(closed)} closed trades, bankroll=${state.bankroll:.2f}")
    except Exception as e:
        print(f"[DB] Could not restore history: {e}")

    await seed_historical_candles()
    asyncio.create_task(ws_10s_candles())
    asyncio.create_task(run_technical_loop())
    asyncio.create_task(run_sentiment_loop())
    asyncio.create_task(run_onchain_loop())
    asyncio.create_task(run_orderflow_loop())
    asyncio.create_task(run_chief_loop())


# ─────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "uptime": round(time.time() - state.start_time, 1)}


@app.get("/api/stats")
async def get_stats():
    stats = await trade_repo.get_performance_stats()
    return {
        "performance": stats,
        "optimizer": optimizer.get_params(),
        "setups_skipped": state.setups_skipped,
        "setups_taken": state.setups_taken,
        "uptime": round(time.time() - state.start_time, 1),
        "win_streak": state.win_streak,
        "leverage": state.trades[0].get("leverage", 0) if state.trades else 0,
    }


@app.get("/api/data")
async def get_data():
    tech = state.agent_signals["technical"]
    sent = state.agent_signals["sentiment"]
    chain = state.agent_signals["onchain"]
    flow = state.agent_signals["orderflow"]
    decision = state.chief_decision

    indicators = tech.get("indicators", {})
    rsi = indicators.get("rsi", tech.get("rsi", 50)) if isinstance(indicators, dict) else 50
    macd_data = indicators.get("macd", {}) if isinstance(indicators, dict) else {}
    hist = macd_data.get("histogram", 0) if isinstance(macd_data, dict) else 0
    divergence = indicators.get("divergence", {"type": "none", "label": "NONE", "score": 50}) if isinstance(indicators, dict) else {"type": "none", "label": "NONE", "score": 50}

    score = int(decision.get("score", 50))
    direction = decision.get("direction", "LONG")
    confidence = float(decision.get("confidence", 0.5))
    win_prob = confidence

    # Build signal structure (compatible with old frontend)
    rsi_val = float(rsi) if rsi else 50
    if rsi_val >= 72:   rsi_score, rsi_label = 22, "OVERBOUGHT"
    elif rsi_val >= 58: rsi_score, rsi_label = 68, "BULLISH"
    elif rsi_val >= 44: rsi_score, rsi_label = 50, "NEUTRAL"
    elif rsi_val >= 28: rsi_score, rsi_label = 34, "BEARISH"
    else:               rsi_score, rsi_label = 76, "OVERSOLD"

    macd_label = "BULLISH" if hist > 0 else "BEARISH" if hist < 0 else "NEUTRAL"
    macd_score = 65 if hist > 0 else 35 if hist < 0 else 50

    fg_val = int(sent.get("fear_greed", 50))
    fg_score = int(sent.get("score", 50))

    ob_ratio = float(flow.get("ob_ratio", 0.5))
    mx_score = int(flow.get("score", 50))

    mom = tech.get("momentum", indicators.get("momentum", "neutral") if isinstance(indicators, dict) else "neutral")
    mom_score = 65 if mom == "up" else 35 if mom == "down" else 50

    div_score = int(divergence.get("score", 50)) if isinstance(divergence, dict) else 50

    signal = {
        "score": score,
        "direction": direction,
        "win_prob": round(win_prob, 3),
        "momentum": mom,
        "divergence": divergence.get("type", "none") if isinstance(divergence, dict) else "none",
        "components": {
            "rsi": {"score": rsi_score, "value": rsi_val, "label": rsi_label},
            "macd": {"score": macd_score, "label": macd_label},
            "fear_greed": {"score": fg_score, "value": fg_val},
            "mexc": {"score": mx_score},
            "momentum": {"score": mom_score, "label": str(mom).upper()},
            "divergence": {"score": div_score, "label": divergence.get("label", "NONE") if isinstance(divergence, dict) else "NONE",
                           "type": divergence.get("type", "none") if isinstance(divergence, dict) else "none"},
        },
    }

    # Candles
    candles = await fetch_klines(40)
    closes = [c["close"] for c in candles] if candles else []
    price_now = closes[-1] if closes else 0
    price_prev = closes[-2] if len(closes) >= 2 else price_now
    chg_pct = round((price_now - price_prev) / price_prev * 100, 2) if price_prev else 0

    # MEXC data from orderflow agent
    mexc = {
        "mark_price": float(flow.get("mark_price", 0)),
        "index_price": float(flow.get("index_price", 0)),
        "funding_rate": float(flow.get("funding_rate", 0.0001)),
        "next_funding": "—",
        "open_interest": 0,
        "ob_ratio": ob_ratio,
        "bids": flow.get("bids", []),
        "asks": flow.get("asks", []),
    }

    wins = sum(1 for t in state.trades if t.get("won") is True)
    losses = sum(1 for t in state.trades if t.get("won") is False)
    total = wins + losses
    now = time.time()

    # Deduplicate candles by timestamp (avoid chart jumps)
    raw = state.candles_5s[-1000:]
    if state._5s_bucket:
        raw = raw + [state._5s_bucket]
    seen = set()
    live_5s = []
    for c in raw:
        t = c["time"]
        if t not in seen:
            seen.add(t)
            live_5s.append(c)
    live_5s.sort(key=lambda x: x["time"])

    # Position info
    pos_state = pos_mgr.get_state()
    position_info = None
    if pos_state:
        current_p = price_now or pos_state["entry_price"]
        pnl = pos_mgr.position.unrealized_pnl(current_p) if pos_mgr.position else 0
        position_info = {**pos_state, "current_price": current_p, "unrealized_pnl": round(pnl, 4)}

    # Update live PnL for open trades
    for t in state.trades:
        if t.get("status") == "OPEN" and pos_mgr.position and price_now > 0:
            t["profit"] = round(pos_mgr.position.unrealized_pnl(price_now), 4)
            t["current_price"] = price_now

    # New trade for toast (most recent open)
    new_trade = None
    if state.trades and state.trades[0].get("status") == "OPEN":
        new_trade = state.trades[0]

    # Agent reasoning for analysis panel
    agent_analysis = [
        {"time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
         "reason": f"🇯🇵 Kenji (Technical): {tech.get('reasoning', '...')}",
         "score": int(tech.get('score', 50)), "win_prob": round(float(tech.get('confidence', 0.5)) * 100, 1)},
        {"time": "", "reason": f"🇬🇧 Sarah (Sentiment): {sent.get('reasoning', '...')}",
         "score": int(sent.get('score', 50)), "win_prob": round(float(sent.get('confidence', 0.5)) * 100, 1)},
        {"time": "", "reason": f"🇨🇭 Viktor (On-Chain): {chain.get('reasoning', '...')}",
         "score": int(chain.get('score', 50)), "win_prob": round(float(chain.get('confidence', 0.5)) * 100, 1)},
        {"time": "", "reason": f"🇺🇸 Diego (Order Flow): {flow.get('reasoning', '...')}",
         "score": int(flow.get('score', 50)), "win_prob": round(float(flow.get('confidence', 0.5)) * 100, 1)},
        {"time": "", "reason": f"🇦🇪 Amir (Chief): {decision.get('reasoning', '...')}",
         "score": score, "win_prob": round(confidence * 100, 1)},
    ]

    # Compute technical overlays for chart
    chart_closes = [c["close"] for c in live_5s]
    chart_indicators = {}
    if len(chart_closes) >= 26:
        # EMA 12 & 26
        def _ema(data, period):
            result = [data[0]]
            m = 2 / (period + 1)
            for v in data[1:]:
                result.append(v * m + result[-1] * (1 - m))
            return result
        ema12 = _ema(chart_closes, 12)
        ema26 = _ema(chart_closes, 26)
        chart_indicators["ema12"] = [{"time": live_5s[i]["time"], "value": round(ema12[i], 2)} for i in range(len(ema12))]
        chart_indicators["ema26"] = [{"time": live_5s[i]["time"], "value": round(ema26[i], 2)} for i in range(len(ema26))]
    if len(chart_closes) >= 20:
        # Bollinger Bands (20, 2)
        bb_data = []
        for i in range(19, len(chart_closes)):
            window = chart_closes[i-19:i+1]
            sma = sum(window) / 20
            std = (sum((x - sma) ** 2 for x in window) / 20) ** 0.5
            bb_data.append({"time": live_5s[i]["time"], "upper": round(sma + 2 * std, 2),
                            "middle": round(sma, 2), "lower": round(sma - 2 * std, 2)})
        chart_indicators["bollinger"] = bb_data
    if len(chart_closes) >= 14:
        # RSI
        rsi_data = []
        for i in range(14, len(chart_closes)):
            deltas = [chart_closes[j] - chart_closes[j-1] for j in range(i-13, i+1)]
            gains = [d for d in deltas if d > 0]
            losses_l = [-d for d in deltas if d < 0]
            avg_gain = sum(gains) / 14 if gains else 0.001
            avg_loss = sum(losses_l) / 14 if losses_l else 0.001
            rs = avg_gain / avg_loss if avg_loss > 0 else 100
            rsi_val = 100 - (100 / (1 + rs))
            rsi_data.append({"time": live_5s[i]["time"], "value": round(rsi_val, 1)})
        chart_indicators["rsi"] = rsi_data

    return {
        "candles": candles[-40:] if candles else [],
        "candles_5s": live_5s[-300:],
        "chart_indicators": chart_indicators,
        "signal": signal,
        "macd_hist": hist,
        "divergence": divergence if isinstance(divergence, dict) else {"type": "none", "label": "NONE", "score": 50},
        "price": price_now,
        "price_change_pct": chg_pct,
        "mexc": mexc,
        "bankroll": state.bankroll,
        "pnl": round(state.bankroll - state.initial, 2),
        "pnl_pct": round((state.bankroll - state.initial) / state.initial * 100, 2),
        "bankroll_history": state.bankroll_history[-80:],
        "trades": state.trades[:30],
        "analysis_log": agent_analysis,
        "win_rate": round(wins / total * 100, 1) if total else 0,
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "new_trade": new_trade,
        "next_trade_in": max(0, int(COOLDOWN_SECONDS - (now - state.last_trade_ts))),
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "cb_active": state.consecutive_losses >= 5,
        "threshold": 0.50,
        "position": position_info,
        "cooldown_seconds": COOLDOWN_SECONDS,
        "leverage": state.trades[0].get("leverage", 0) if state.trades else 0,
        "agents": {
            "technical": {"name": "Kenji Tanaka", "role": "Technical Analyst", "score": int(tech.get("score", 50)), "direction": tech.get("direction", "neutral")},
            "sentiment": {"name": "Sarah Mitchell", "role": "Sentiment Analyst", "score": int(sent.get("score", 50)), "direction": sent.get("direction", "neutral")},
            "onchain": {"name": "Viktor Petrov", "role": "On-Chain Analyst", "score": int(chain.get("score", 50)), "direction": chain.get("direction", "neutral")},
            "orderflow": {"name": "Diego Ramirez", "role": "Order Flow", "score": int(flow.get("score", 50)), "direction": flow.get("direction", "neutral")},
            "chief": {"name": "Amir Hassan", "role": "Chief Trader", "score": score, "direction": direction},
        },
    }


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML


# ─────────────────────────────────────────────────────────
# HTML Dashboard
# ─────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>JARVIS // BTC COMMAND CENTER</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;600;700;800;900&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<link href="https://cdn.jsdelivr.net/npm/gridstack@10/dist/gridstack.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/gridstack@10/dist/gridstack-all.js"></script>
<style>
:root {
  --void: #050510;
  --panel: rgba(0, 20, 40, 0.85);
  --panel-border: rgba(0, 240, 255, 0.15);
  --glow: rgba(0, 240, 255, 0.4);
  --cyan: #00f0ff;
  --blue: #0088ff;
  --green: #00ff88;
  --red: #ff2255;
  --yellow: #ffcc00;
  --text: #8cb4c8;
  --text-bright: #d0e8f0;
  --font-display: 'Orbitron', sans-serif;
  --font-mono: 'Share Tech Mono', monospace;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--void);color:var(--text);font-family:var(--font-mono);font-size:12px;overflow:hidden}

/* Scan-line overlay */
body::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:9999;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,240,255,0.015) 2px,rgba(0,240,255,0.015) 4px)}

/* Gridstack overrides */
.grid-stack{height:100vh;max-height:100vh;background:var(--void);overflow:hidden}
.grid-stack-item-content{overflow:hidden!important}

/* Panels */
.panel{background:var(--panel);border:1px solid var(--panel-border);border-radius:4px;backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);box-shadow:0 0 15px rgba(0,240,255,0.08),inset 0 1px 0 rgba(0,240,255,0.1);overflow:hidden;display:flex;flex-direction:column;height:100%}
.panel:hover{border-color:rgba(0,240,255,0.3);box-shadow:0 0 25px rgba(0,240,255,0.12),inset 0 1px 0 rgba(0,240,255,0.15)}
.panel-header{font-family:var(--font-display);font-size:10px;letter-spacing:0.2em;color:var(--cyan);padding:8px 12px;border-bottom:1px solid var(--panel-border);text-transform:uppercase;display:flex;justify-content:space-between;align-items:center;cursor:move;flex-shrink:0;background:rgba(0,240,255,0.03)}
.panel-header .hdr-accent{width:6px;height:6px;background:var(--cyan);clip-path:polygon(50% 0%,100% 50%,50% 100%,0% 50%);animation:spin-hex 8s linear infinite;margin-right:6px;display:inline-block}
@keyframes spin-hex{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
.panel-header .hdr-right{font-family:var(--font-mono);font-size:9px;color:var(--text);letter-spacing:0.05em}
.panel-body{flex:1;overflow:auto;padding:8px 12px;position:relative}
.panel-body.no-pad{padding:0}

/* Pulse animation */
.pulse{width:7px;height:7px;border-radius:50%;background:var(--cyan);box-shadow:0 0 10px var(--cyan),0 0 20px rgba(0,240,255,0.3);animation:pulse 2s ease-in-out infinite;display:inline-block}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.3;transform:scale(.6)}}

/* Color helpers */
.g{color:var(--green)!important} .r{color:var(--red)!important} .y{color:var(--yellow)!important}

/* ===== STATS BAR ===== */
.stats-grid{display:flex;gap:16px;align-items:center;height:100%;padding:0 8px;flex-wrap:nowrap;overflow-x:auto}
.stat-item{display:flex;flex-direction:column;gap:2px;min-width:0;flex:1}
.stat-label{font-family:var(--font-display);font-size:8px;letter-spacing:0.15em;color:rgba(0,240,255,0.5);text-transform:uppercase;white-space:nowrap}
.stat-value{font-family:var(--font-mono);font-size:16px;font-weight:700;color:var(--text-bright);font-variant-numeric:tabular-nums;white-space:nowrap;transition:color .4s}
.stat-sub{font-size:9px;color:var(--text);opacity:0.6}
.stat-pills{display:flex;gap:6px;align-items:center;flex-wrap:nowrap}
.pill{font-family:var(--font-mono);font-size:9px;font-weight:600;letter-spacing:.08em;padding:2px 7px;border:1px solid var(--panel-border);color:var(--text);white-space:nowrap;border-radius:2px}
.pill.long{color:var(--green);border-color:var(--green);box-shadow:0 0 6px rgba(0,255,136,0.2)}
.pill.short{color:var(--red);border-color:var(--red);box-shadow:0 0 6px rgba(255,34,85,0.2)}
.pill.div-bull{color:var(--green);border-color:var(--green);animation:glow-g 1.5s ease-in-out infinite}
.pill.div-bear{color:var(--red);border-color:var(--red);animation:glow-r 1.5s ease-in-out infinite}
@keyframes glow-g{0%,100%{box-shadow:0 0 0 transparent}50%{box-shadow:0 0 10px rgba(0,255,136,.4)}}
@keyframes glow-r{0%,100%{box-shadow:0 0 0 transparent}50%{box-shadow:0 0 10px rgba(255,34,85,.4)}}

/* ===== COMMAND CENTER ===== */
.cmd-center{display:flex;flex-direction:column;gap:10px;height:100%}
.cmd-arc-wrap{display:flex;align-items:center;justify-content:center;gap:20px;flex:1}
.cmd-arc{position:relative;width:100px;height:100px}
.cmd-arc svg{width:100%;height:100%}
.cmd-arc-label{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.cmd-score{font-family:var(--font-display);font-size:28px;font-weight:900;color:var(--text-bright)}
.cmd-score-lbl{font-size:8px;color:var(--cyan);letter-spacing:0.15em;font-family:var(--font-display)}
.cmd-info{display:flex;flex-direction:column;gap:6px}
.cmd-dir{font-family:var(--font-display);font-size:18px;font-weight:800;letter-spacing:0.1em}
.cmd-dir.long{color:var(--green);text-shadow:0 0 15px rgba(0,255,136,0.5)}
.cmd-dir.short{color:var(--red);text-shadow:0 0 15px rgba(255,34,85,0.5)}
.cmd-dir.flat{color:var(--text)}
.cmd-row{display:flex;justify-content:space-between;gap:12px}
.cmd-stat{display:flex;flex-direction:column;gap:1px}
.cmd-stat-lbl{font-size:8px;color:var(--cyan);letter-spacing:0.1em;font-family:var(--font-display)}
.cmd-stat-val{font-size:14px;font-weight:700;font-family:var(--font-mono);color:var(--text-bright)}

/* ===== AGENT MATRIX ===== */
.agent-grid{display:flex;flex-direction:column;gap:6px;height:100%}
.agent-card{background:rgba(0,240,255,0.03);border:1px solid rgba(0,240,255,0.08);border-radius:3px;padding:8px 10px;display:flex;flex-direction:column;gap:4px;transition:all 0.3s}
.agent-card:hover{border-color:rgba(0,240,255,0.25);background:rgba(0,240,255,0.06)}
.agent-card.chief{border-color:rgba(0,240,255,0.3);background:rgba(0,240,255,0.06);box-shadow:0 0 12px rgba(0,240,255,0.1)}
.agent-top{display:flex;justify-content:space-between;align-items:center}
.agent-name{font-family:var(--font-display);font-size:9px;letter-spacing:0.12em;color:var(--cyan)}
.agent-role{font-size:8px;color:var(--text);opacity:0.5}
.agent-dir{font-family:var(--font-display);font-size:10px;font-weight:700;letter-spacing:0.1em}
.agent-dir.long{color:var(--green)} .agent-dir.short{color:var(--red)} .agent-dir.neutral{color:var(--text)}
.agent-bar-wrap{display:flex;align-items:center;gap:6px}
.agent-bar{flex:1;height:4px;background:rgba(0,240,255,0.08);border-radius:2px;overflow:hidden}
.agent-bar-fill{height:100%;border-radius:2px;transition:width 1s cubic-bezier(.4,0,.2,1)}
.agent-bar-fill.bull{background:linear-gradient(90deg,var(--blue),var(--green))}
.agent-bar-fill.bear{background:linear-gradient(90deg,var(--red),var(--yellow))}
.agent-bar-fill.neut{background:var(--text)}
.agent-score-num{font-family:var(--font-mono);font-size:11px;font-weight:700;width:26px;text-align:right}
.agent-reason{font-size:9px;color:var(--text);opacity:0.7;line-height:1.4;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}

/* ===== POSITION HUD ===== */
.pos-hud{display:flex;flex-direction:column;gap:8px;height:100%}
.pos-status-row{display:flex;align-items:center;gap:10px}
.pos-indicator{font-family:var(--font-display);font-size:11px;font-weight:700;letter-spacing:0.15em;padding:3px 10px;border:1px solid;border-radius:2px}
.pos-indicator.open{color:var(--green);border-color:var(--green);box-shadow:0 0 10px rgba(0,255,136,0.2);animation:pulse-border 2s ease-in-out infinite}
.pos-indicator.waiting{color:var(--yellow);border-color:var(--yellow);box-shadow:0 0 10px rgba(255,204,0,0.15)}
@keyframes pulse-border{0%,100%{box-shadow:0 0 10px rgba(0,255,136,0.2)}50%{box-shadow:0 0 20px rgba(0,255,136,0.4)}}
.pos-arrow{font-size:32px;font-weight:900;line-height:1;transition:all .4s}
.pos-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;flex:1}
.pos-item{display:flex;flex-direction:column;gap:1px}
.pos-item-lbl{font-size:8px;color:var(--cyan);letter-spacing:0.1em;font-family:var(--font-display)}
.pos-item-val{font-size:13px;font-weight:700;font-family:var(--font-mono);color:var(--text-bright)}

/* ===== CHARTS ===== */
#btc-chart{flex:1;min-height:0}
#br-chart{flex:1;min-height:0}
.br-val{font-family:var(--font-mono);font-size:12px;font-weight:700;color:var(--text-bright)}

/* ===== ORDER BOOK ===== */
.ob-meta{display:grid;grid-template-columns:1fr 1fr;gap:1px;margin-bottom:6px}
.ob-meta-item{padding:4px 8px;background:rgba(0,240,255,0.03);border:1px solid rgba(0,240,255,0.06);border-radius:2px}
.ob-meta-lbl{font-size:7px;color:var(--cyan);letter-spacing:0.1em;font-family:var(--font-display)}
.ob-meta-val{font-size:10px;font-weight:600;font-family:var(--font-mono);color:var(--text-bright)}
.obw{flex:1;overflow:hidden;display:flex;flex-direction:column;gap:2px}
.obh{display:flex;justify-content:space-between;font-size:8px;font-weight:700;color:var(--cyan);letter-spacing:.1em;margin-bottom:2px;font-family:var(--font-display)}
.obr{display:flex;align-items:center;gap:5px;height:14px}
.obp{font-size:9px;font-weight:600;font-variant-numeric:tabular-nums;width:76px;font-family:var(--font-mono)}
.obvol{font-size:9px;color:var(--text);font-variant-numeric:tabular-nums;width:52px;text-align:right;font-family:var(--font-mono)}
.obbw{flex:1;height:5px;position:relative;background:rgba(0,240,255,0.03)}
.obb{position:absolute;top:0;height:100%;border-radius:1px;transition:width .5s}
.obb.bid{left:0;background:rgba(0,255,136,.3)} .obb.ask{left:0;background:rgba(255,34,85,.3)}
.obsp{height:1px;background:var(--panel-border);margin:3px 0;flex-shrink:0}
.obspread{font-size:9px;color:var(--text);text-align:center;padding:2px 0;font-family:var(--font-mono)}

/* ===== TRADE LOG ===== */
.log-header-bar{display:flex;align-items:center;gap:10px;padding:0 12px;margin-bottom:4px;flex-shrink:0}
.log-sub{font-size:9px;color:var(--text);opacity:0.5}
.cdw{font-size:9px;color:var(--text);opacity:0.6;margin-left:auto;font-family:var(--font-mono)}
.cd{font-weight:700;color:var(--yellow)}
.logtable{flex:1;overflow-y:auto}
table{width:100%;border-collapse:collapse}
thead th{font-family:var(--font-display);font-size:7px;font-weight:600;letter-spacing:.12em;color:var(--cyan);padding:4px 8px;text-align:left;border-bottom:1px solid var(--panel-border);background:rgba(0,240,255,0.03);position:sticky;top:0;z-index:2}
tbody tr{border-bottom:1px solid rgba(0,240,255,.04);transition:background .15s;border-left:2px solid transparent}
tbody tr.wr{border-left-color:var(--green)} tbody tr.lr{border-left-color:var(--red)}
tbody tr:hover{background:rgba(0,240,255,.04)}
tbody tr.te{animation:rslide .5s ease-out}
@keyframes rslide{from{opacity:0;transform:translateX(-10px);background:rgba(0,240,255,.08)}to{opacity:1;transform:translateX(0);background:transparent}}
td{padding:4px 8px;font-size:10px;font-variant-numeric:tabular-nums;white-space:nowrap;font-family:var(--font-mono)}
.ti{color:var(--text);opacity:0.4;width:30px} .tt{color:var(--text);opacity:0.5;width:65px}
.tdir{font-weight:700;width:58px;font-size:11px}
.tsc{color:var(--text);opacity:0.5;width:36px} .twp{color:var(--text);opacity:0.5;width:38px}
.tstk{width:56px} .tep{color:var(--text);opacity:0.5;width:84px;font-size:9px}
.tres{font-weight:700;width:48px} .tpnl{font-weight:700;width:58px} .tbr{color:var(--text);opacity:0.5}
.treason{font-size:8px;color:var(--text);opacity:0.5;max-width:180px;overflow:hidden;text-overflow:ellipsis}
.win{color:var(--green)!important} .loss{color:var(--red)!important}
.empty-l{padding:16px;text-align:center;color:var(--text);opacity:0.4;font-size:10px}

/* ===== SMART TRADING ===== */
.smart-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;height:100%}
.smart-item{display:flex;flex-direction:column;gap:2px;padding:6px 8px;background:rgba(0,240,255,0.03);border:1px solid rgba(0,240,255,0.06);border-radius:3px}
.smart-lbl{font-size:8px;color:var(--cyan);letter-spacing:0.1em;font-family:var(--font-display)}
.smart-val{font-size:16px;font-weight:700;font-family:var(--font-mono);color:var(--text-bright)}
.smart-sub{font-size:8px;color:var(--text);opacity:0.5}

/* ===== TOAST ===== */
.toast{position:fixed;top:14px;right:14px;width:280px;background:var(--panel);border:1px solid rgba(0,240,255,0.3);border-radius:4px;backdrop-filter:blur(20px);z-index:10000;display:none;flex-direction:column;box-shadow:0 0 30px rgba(0,240,255,0.15),0 8px 32px rgba(0,0,0,.55);animation:tin .35s cubic-bezier(.22,1,.36,1)}
@keyframes tin{from{opacity:0;transform:translateX(18px)}to{opacity:1;transform:translateX(0)}}
.toast.show{display:flex}
.tt-top{padding:10px 14px 6px;display:flex;align-items:center;gap:10px}
.tt-ar{font-size:32px;font-weight:900;line-height:1}
.tt-info{flex:1}
.tt-lbl{font-family:var(--font-display);font-size:8px;font-weight:600;letter-spacing:.15em;color:var(--cyan)}
.tt-dir{font-family:var(--font-display);font-size:14px;font-weight:700}
.tt-body{padding:6px 14px 10px;display:grid;grid-template-columns:1fr 1fr;gap:4px}
.ttb-i{display:flex;flex-direction:column;gap:1px}
.ttb-l{font-size:7px;color:var(--cyan);letter-spacing:.12em;font-family:var(--font-display)}
.ttb-v{font-size:12px;font-weight:700;font-variant-numeric:tabular-nums;font-family:var(--font-mono);color:var(--text-bright)}
.tt-bar{height:2px}
.tt-reason{padding:0 14px 10px;font-size:9px;color:var(--text);opacity:0.7;line-height:1.5;border-top:1px solid var(--panel-border);padding-top:6px;margin-top:2px}

/* Scrollbar */
::-webkit-scrollbar{width:3px;height:3px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(0,240,255,.2);border-radius:2px}
::-webkit-scrollbar-thumb:hover{background:rgba(0,240,255,.35)}

/* Corner hex decorations */
.hex-corner{position:absolute;width:8px;height:8px;border:1px solid rgba(0,240,255,0.2)}
.hex-corner.tl{top:0;left:0;border-right:0;border-bottom:0}
.hex-corner.tr{top:0;right:0;border-left:0;border-bottom:0}
.hex-corner.bl{bottom:0;left:0;border-right:0;border-top:0}
.hex-corner.br{bottom:0;right:0;border-left:0;border-top:0}
</style>
</head>
<body>

<!-- GRIDSTACK LAYOUT -->
<div class="grid-stack">

  <!-- ===== STATS BAR (12x1) ===== -->
  <div class="grid-stack-item" gs-x="0" gs-y="0" gs-w="12" gs-h="1" gs-no-resize="true">
    <div class="grid-stack-item-content">
      <div class="panel">
        <div class="stats-grid">
          <div class="stat-item" style="min-width:40px;flex:0">
            <div class="pulse" style="margin-top:4px"></div>
          </div>
          <div class="stat-item">
            <div class="stat-label">BTC/USDT</div>
            <div style="display:flex;align-items:baseline;gap:6px">
              <span class="stat-value" id="hp">---</span>
              <span class="stat-value" id="hc" style="font-size:11px">---</span>
            </div>
          </div>
          <div class="stat-item">
            <div class="stat-label">Bankroll</div>
            <div class="stat-value" id="sbr">$100.00</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">P&L</div>
            <div class="stat-value" id="spnl">$0.00</div>
            <div class="stat-sub" id="spnl-p">+0.00%</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">Win Rate</div>
            <div class="stat-value" id="swr">---</div>
            <div class="stat-sub" id="swrsub">0 W / 0 L</div>
          </div>
          <div class="stat-item">
            <div class="stat-label">Cooldown</div>
            <div class="stat-value" id="cd" style="font-size:14px;color:var(--yellow)">---</div>
          </div>
          <div class="stat-item">
            <div class="stat-pills">
              <span class="pill" id="p-rsi">RSI ---</span>
              <span class="pill" id="p-macd">MACD ---</span>
              <span class="pill" id="p-fg">F&G ---</span>
              <span class="pill" id="p-div">DIV ---</span>
              <span class="pill" id="p-fr">FUND ---</span>
            </div>
          </div>
          <div class="stat-item" style="margin-left:auto;text-align:right;flex:0;min-width:100px">
            <div style="display:flex;align-items:center;gap:6px;justify-content:flex-end">
              <div class="pulse" style="width:5px;height:5px"></div>
              <span style="font-family:var(--font-display);font-size:9px;letter-spacing:0.15em;color:var(--cyan)">LIVE</span>
            </div>
            <div class="stat-sub" id="hts">---</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ===== BTC CHART (8x6) ===== -->
  <div class="grid-stack-item" gs-x="0" gs-y="1" gs-w="8" gs-h="6">
    <div class="grid-stack-item-content">
      <div class="panel">
        <div class="panel-header">
          <div><span class="hdr-accent"></span>BTC/USDT PERPETUAL</div>
          <div class="hdr-right"><span id="ph-mark"></span> 10S LIVE BINANCE</div>
        </div>
        <div class="panel-body no-pad" style="display:flex;flex-direction:column">
          <div id="btc-chart"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- ===== COMMAND CENTER (4x3) ===== -->
  <div class="grid-stack-item" gs-x="8" gs-y="1" gs-w="4" gs-h="3">
    <div class="grid-stack-item-content">
      <div class="panel">
        <div class="panel-header">
          <div><span class="hdr-accent"></span>COMMAND CENTER</div>
          <div class="hdr-right" id="dtag" style="font-family:var(--font-display);font-weight:700;letter-spacing:0.1em">FLAT</div>
        </div>
        <div class="panel-body">
          <div class="cmd-center">
            <div class="cmd-arc-wrap">
              <div class="cmd-arc">
                <svg viewBox="0 0 100 100">
                  <circle cx="50" cy="50" r="42" fill="none" stroke="rgba(0,240,255,0.08)" stroke-width="6"/>
                  <circle id="score-arc" cx="50" cy="50" r="42" fill="none" stroke="var(--cyan)" stroke-width="6"
                    stroke-dasharray="264" stroke-dashoffset="264" stroke-linecap="round"
                    transform="rotate(-90 50 50)" style="transition:stroke-dashoffset 1s ease,stroke 0.5s"/>
                </svg>
                <div class="cmd-arc-label">
                  <div class="cmd-score" id="scn">---</div>
                  <div class="cmd-score-lbl">SCORE</div>
                </div>
              </div>
              <div class="cmd-info">
                <div class="cmd-dir flat" id="cmd-dir">FLAT</div>
                <div class="cmd-row">
                  <div class="cmd-stat">
                    <div class="cmd-stat-lbl">WIN PROB</div>
                    <div class="cmd-stat-val" id="scwp">---</div>
                  </div>
                  <div class="cmd-stat">
                    <div class="cmd-stat-lbl">CONFIDENCE</div>
                    <div class="cmd-stat-val" id="cmd-conf">---</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ===== AGENT MATRIX (4x3) ===== -->
  <div class="grid-stack-item" gs-x="8" gs-y="4" gs-w="4" gs-h="3">
    <div class="grid-stack-item-content">
      <div class="panel">
        <div class="panel-header">
          <div><span class="hdr-accent"></span>AGENT MATRIX</div>
          <div class="hdr-right">5 EXPERTS</div>
        </div>
        <div class="panel-body" style="padding:6px 8px">
          <div class="agent-grid" id="agent-grid">
            <div class="agent-card" id="agent-kenji">
              <div class="agent-top">
                <span class="agent-name">KENJI TANAKA</span>
                <span class="agent-dir neutral" id="ag-dir-kenji">NEUTRAL</span>
              </div>
              <div class="agent-bar-wrap">
                <div class="agent-bar"><div class="agent-bar-fill neut" id="ag-bar-kenji" style="width:0%"></div></div>
                <span class="agent-score-num" id="ag-score-kenji">---</span>
              </div>
              <div class="agent-reason" id="ag-reason-kenji">Technical Analyst - Waiting for data...</div>
            </div>
            <div class="agent-card" id="agent-sarah">
              <div class="agent-top">
                <span class="agent-name">SARAH MITCHELL</span>
                <span class="agent-dir neutral" id="ag-dir-sarah">NEUTRAL</span>
              </div>
              <div class="agent-bar-wrap">
                <div class="agent-bar"><div class="agent-bar-fill neut" id="ag-bar-sarah" style="width:0%"></div></div>
                <span class="agent-score-num" id="ag-score-sarah">---</span>
              </div>
              <div class="agent-reason" id="ag-reason-sarah">Sentiment Analyst - Waiting for data...</div>
            </div>
            <div class="agent-card" id="agent-viktor">
              <div class="agent-top">
                <span class="agent-name">VIKTOR PETROV</span>
                <span class="agent-dir neutral" id="ag-dir-viktor">NEUTRAL</span>
              </div>
              <div class="agent-bar-wrap">
                <div class="agent-bar"><div class="agent-bar-fill neut" id="ag-bar-viktor" style="width:0%"></div></div>
                <span class="agent-score-num" id="ag-score-viktor">---</span>
              </div>
              <div class="agent-reason" id="ag-reason-viktor">On-Chain Analyst - Waiting for data...</div>
            </div>
            <div class="agent-card" id="agent-diego">
              <div class="agent-top">
                <span class="agent-name">DIEGO RAMIREZ</span>
                <span class="agent-dir neutral" id="ag-dir-diego">NEUTRAL</span>
              </div>
              <div class="agent-bar-wrap">
                <div class="agent-bar"><div class="agent-bar-fill neut" id="ag-bar-diego" style="width:0%"></div></div>
                <span class="agent-score-num" id="ag-score-diego">---</span>
              </div>
              <div class="agent-reason" id="ag-reason-diego">Order Flow - Waiting for data...</div>
            </div>
            <div class="agent-card chief" id="agent-amir">
              <div class="agent-top">
                <span class="agent-name" style="color:var(--yellow)">AMIR HASSAN</span>
                <span class="agent-dir neutral" id="ag-dir-amir">NEUTRAL</span>
              </div>
              <div class="agent-bar-wrap">
                <div class="agent-bar"><div class="agent-bar-fill neut" id="ag-bar-amir" style="width:0%"></div></div>
                <span class="agent-score-num" id="ag-score-amir">---</span>
              </div>
              <div class="agent-reason" id="ag-reason-amir">Chief Trader - Waiting for data...</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ===== POSITION HUD (4x2) ===== -->
  <div class="grid-stack-item" gs-x="0" gs-y="7" gs-w="4" gs-h="2">
    <div class="grid-stack-item-content">
      <div class="panel">
        <div class="panel-header">
          <div><span class="hdr-accent"></span>POSITION HUD</div>
          <div class="hdr-right" id="pos-status-label">WAITING</div>
        </div>
        <div class="panel-body">
          <div class="pos-hud">
            <div class="pos-status-row">
              <span class="pos-arrow" id="la-ar" style="color:var(--text);opacity:0.3">.</span>
              <div>
                <div class="pos-item-val" id="la-res" style="font-size:12px;color:var(--text)">Watching...</div>
                <div class="stat-sub" id="la-det"></div>
              </div>
            </div>
            <div class="pos-grid">
              <div class="pos-item">
                <div class="pos-item-lbl">ENTRY</div>
                <div class="pos-item-val" id="pos-entry">---</div>
              </div>
              <div class="pos-item">
                <div class="pos-item-lbl">STOP LOSS</div>
                <div class="pos-item-val" id="pos-sl">---</div>
              </div>
              <div class="pos-item">
                <div class="pos-item-lbl">UNREAL P&L</div>
                <div class="pos-item-val" id="pos-upnl">---</div>
              </div>
              <div class="pos-item">
                <div class="pos-item-lbl">DURATION</div>
                <div class="pos-item-val" id="pos-dur">---</div>
              </div>
              <div class="pos-item">
                <div class="pos-item-lbl">LEVERAGE</div>
                <div class="pos-item-val" id="pos-lev">---</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ===== BANKROLL CHART (4x2) ===== -->
  <div class="grid-stack-item" gs-x="4" gs-y="7" gs-w="4" gs-h="2">
    <div class="grid-stack-item-content">
      <div class="panel">
        <div class="panel-header">
          <div><span class="hdr-accent"></span>BANKROLL EVOLUTION</div>
          <div class="hdr-right br-val" id="brv">$100.00</div>
        </div>
        <div class="panel-body no-pad" style="display:flex;flex-direction:column">
          <div id="br-chart"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- ===== SMART TRADING (4x2) ===== -->
  <div class="grid-stack-item" gs-x="8" gs-y="7" gs-w="4" gs-h="2">
    <div class="grid-stack-item-content">
      <div class="panel">
        <div class="panel-header">
          <div><span class="hdr-accent"></span>SMART TRADING</div>
          <div class="hdr-right">OPTIMIZER</div>
        </div>
        <div class="panel-body">
          <div class="smart-grid">
            <div class="smart-item">
              <div class="smart-lbl">SETUP QUALITY</div>
              <div class="smart-val" id="sq-val">---</div>
              <div class="smart-sub" id="sq-sub">min threshold: 0.40</div>
            </div>
            <div class="smart-item">
              <div class="smart-lbl">SKIPPED</div>
              <div class="smart-val" id="ss-val">0</div>
              <div class="smart-sub" id="ss-sub">Taken: 0</div>
            </div>
            <div class="smart-item">
              <div class="smart-lbl">LEVERAGE</div>
              <div class="smart-val" id="sl-val">---</div>
              <div class="smart-sub" id="sl-sub">Dynamic</div>
            </div>
            <div class="smart-item">
              <div class="smart-lbl">OPTIMIZER</div>
              <div class="smart-val" id="so-val">Learning</div>
              <div class="smart-sub" id="so-sub">min_q=0.40 | stake_max=25%</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ===== TRADE LOG (8x3) ===== -->
  <div class="grid-stack-item" gs-x="0" gs-y="9" gs-w="8" gs-h="3">
    <div class="grid-stack-item-content">
      <div class="panel">
        <div class="panel-header">
          <div><span class="hdr-accent"></span>TRADE LOG</div>
          <div class="hdr-right">
            <span class="cdw">NEXT: <span class="cd" id="cd2">---</span></span>
          </div>
        </div>
        <div class="panel-body no-pad" style="display:flex;flex-direction:column">
          <div class="logtable">
            <table>
              <thead><tr>
                <th>#</th><th>TIME</th><th>POS</th><th>SC</th><th>WP%</th>
                <th>STAKE</th><th>LEV</th><th>ENTRY</th><th>EXIT</th><th>RES</th><th>P&L</th><th>BANKROLL</th><th>REASON</th>
              </tr></thead>
              <tbody id="logbody">
                <tr><td colspan="13" class="empty-l">Agents initializing... scanning for opportunities</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ===== ORDER BOOK (4x3) ===== -->
  <div class="grid-stack-item" gs-x="8" gs-y="9" gs-w="4" gs-h="3">
    <div class="grid-stack-item-content">
      <div class="panel">
        <div class="panel-header">
          <div><span class="hdr-accent"></span>ORDER BOOK</div>
          <div class="hdr-right">MEXC BTC_USDT</div>
        </div>
        <div class="panel-body" style="padding:6px 8px">
          <div class="ob-meta">
            <div class="ob-meta-item"><div class="ob-meta-lbl">MARK</div><div class="ob-meta-val" id="mx-mark">---</div></div>
            <div class="ob-meta-item"><div class="ob-meta-lbl">FUNDING</div><div class="ob-meta-val" id="mx-fr">---</div></div>
            <div class="ob-meta-item"><div class="ob-meta-lbl">INDEX</div><div class="ob-meta-val" id="mx-idx">---</div></div>
            <div class="ob-meta-item"><div class="ob-meta-lbl">BID/ASK</div><div class="ob-meta-val" id="mx-ob">---</div></div>
          </div>
          <div class="obw">
            <div class="obh"><span>ASKS</span><span>QTY</span></div>
            <div id="ob-asks"></div>
            <div class="obsp"></div>
            <div class="obspread" id="ob-spr">---</div>
            <div class="obsp"></div>
            <div class="obh"><span>BIDS</span><span>QTY</span></div>
            <div id="ob-bids"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

</div>

<!-- Toast notification -->
<div class="toast" id="toast">
  <div class="tt-top">
    <span class="tt-ar" id="t-ar">&#9650;</span>
    <div class="tt-info">
      <div class="tt-lbl">POSITION OPENED // MEXC PERP</div>
      <div class="tt-dir" id="t-dir">---</div>
    </div>
  </div>
  <div class="tt-body">
    <div class="ttb-i"><span class="ttb-l">SCORE</span><span class="ttb-v" id="t-sc">---</span></div>
    <div class="ttb-i"><span class="ttb-l">WIN PROB</span><span class="ttb-v" id="t-wp">---</span></div>
    <div class="ttb-i"><span class="ttb-l">STAKE</span><span class="ttb-v" id="t-st">---</span></div>
    <div class="ttb-i"><span class="ttb-l">RESULT</span><span class="ttb-v" id="t-res">---</span></div>
  </div>
  <div class="tt-reason" id="t-rsn"></div>
  <div class="tt-bar" id="t-bar"></div>
</div>

<!-- Hidden elements for backward compat -->
<div style="display:none">
  <span id="agn"></span>
  <span id="agwp"></span>
  <span id="ana-cur"></span>
  <div id="analist"></div>
</div>

<script>
let btcChart=null,brChart=null,candleSeries=null,brLine=null;
let ema12Line=null,ema26Line=null,bbUpperLine=null,bbLowerLine=null;
let lastTradeId=0,toastTimer=null,cdInterval=null,cdRemaining=0;
let gsGrid=null;
const totalRows=12;

function initCharts() {
  const btcEl=document.getElementById('btc-chart');
  if(!btcEl)return;
  btcChart=LightweightCharts.createChart(btcEl,{
    width:btcEl.clientWidth,height:btcEl.clientHeight,
    layout:{background:{color:'#050510'},textColor:'#4a6a7a',fontFamily:"'Share Tech Mono',monospace",fontSize:10},
    grid:{vertLines:{color:'rgba(0,240,255,0.04)'},horzLines:{color:'rgba(0,240,255,0.04)'}},
    crosshair:{mode:LightweightCharts.CrosshairMode.Normal,vertLine:{color:'rgba(0,240,255,0.3)',labelBackgroundColor:'#0088ff'},horzLine:{color:'rgba(0,240,255,0.3)',labelBackgroundColor:'#0088ff'}},
    rightPriceScale:{borderColor:'rgba(0,240,255,0.1)',scaleMargins:{top:.08,bottom:.08}},
    timeScale:{borderColor:'rgba(0,240,255,0.1)',timeVisible:true,secondsVisible:true,rightOffset:3},
  });
  candleSeries=btcChart.addCandlestickSeries({
    upColor:'#00ff88',downColor:'#ff2255',
    borderUpColor:'#00ff88',borderDownColor:'#ff2255',
    wickUpColor:'#00cc66',wickDownColor:'#cc1144',
  });
  ema12Line=btcChart.addLineSeries({color:'#00f0ff',lineWidth:1,title:'EMA12',priceLineVisible:false,lastValueVisible:false});
  ema26Line=btcChart.addLineSeries({color:'#0066ff',lineWidth:1,title:'EMA26',priceLineVisible:false,lastValueVisible:false});
  bbUpperLine=btcChart.addLineSeries({color:'rgba(255,170,0,0.5)',lineWidth:1,lineStyle:2,title:'BB+',priceLineVisible:false,lastValueVisible:false});
  bbLowerLine=btcChart.addLineSeries({color:'rgba(255,170,0,0.5)',lineWidth:1,lineStyle:2,title:'BB-',priceLineVisible:false,lastValueVisible:false});

  const brEl=document.getElementById('br-chart');
  if(!brEl)return;
  brChart=LightweightCharts.createChart(brEl,{
    width:brEl.clientWidth,height:brEl.clientHeight,
    layout:{background:{color:'#050510'},textColor:'#4a6a7a',fontFamily:"'Share Tech Mono',monospace",fontSize:9},
    grid:{vertLines:{color:'rgba(0,0,0,0)'},horzLines:{color:'rgba(0,240,255,0.03)'}},
    crosshair:{mode:LightweightCharts.CrosshairMode.Normal},
    rightPriceScale:{borderColor:'rgba(0,240,255,0.1)',scaleMargins:{top:.1,bottom:.1}},
    timeScale:{visible:false},handleScroll:false,handleScale:false,
  });
  brLine=brChart.addAreaSeries({lineColor:'#00ff88',topColor:'rgba(0,255,136,.15)',bottomColor:'rgba(0,0,0,0)',lineWidth:2});

  window.addEventListener('resize',()=>{
    if(gsGrid){const newCH=Math.floor((window.innerHeight-totalRows*8)/totalRows);gsGrid.cellHeight(newCH);}
    if(btcChart){const el=document.getElementById('btc-chart');btcChart.resize(el.clientWidth,el.clientHeight);}
    if(brChart){const el=document.getElementById('br-chart');brChart.resize(el.clientWidth,el.clientHeight);}
  });
}

function fE(v,plus=false){const a=Math.abs(v).toFixed(2);return v<0?'-$'+a:(plus&&v>0?'+':'')+'$'+a}
function fP(v){return '$'+v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}
function sc(s){return s>54?'bull':s<46?'bear':'neut'}
function setG(el,v){el.classList.remove('g','r','y');if(v>0)el.classList.add('g');else if(v<0)el.classList.add('r')}

function showToast(t){
  const isL=t.direction==='LONG';
  document.getElementById('t-ar').textContent=isL?'\\u25B2':'\\u25BC';
  document.getElementById('t-ar').style.color=isL?'var(--green)':'var(--red)';
  document.getElementById('t-dir').textContent=(isL?'LONG':'SHORT')+' #'+t.id;
  document.getElementById('t-dir').style.color=isL?'var(--green)':'var(--red)';
  document.getElementById('t-sc').textContent=t.score+'/100';
  document.getElementById('t-wp').textContent=(t.win_prob*100).toFixed(1)+'%';
  document.getElementById('t-st').textContent=fE(t.stake);
  document.getElementById('t-res').textContent=t.won?'WIN':'LOSS';
  document.getElementById('t-res').style.color=t.won?'var(--green)':'var(--red)';
  document.getElementById('t-bar').style.background=t.won?'var(--green)':'var(--red)';
  document.getElementById('t-rsn').textContent=t.reasoning||'';
  const toast=document.getElementById('toast');
  toast.classList.add('show');
  if(toastTimer)clearTimeout(toastTimer);
  toastTimer=setTimeout(()=>toast.classList.remove('show'),8000);
}

function updateOB(mx){
  const bids=mx.bids||[],asks=mx.asks||[];
  const maxVol=Math.max(...bids.map(b=>b[1]),...asks.map(a=>a[1]),1);
  const asksRev=[...asks].sort((a,b)=>b[0]-a[0]);
  document.getElementById('ob-asks').innerHTML=asksRev.map(([p,v])=>
    `<div class="obr">
      <span class="obp loss">$${p.toLocaleString('en-US',{maximumFractionDigits:1})}</span>
      <span class="obvol">${v.toFixed(0)}</span>
      <div class="obbw"><div class="obb ask" style="width:${(v/maxVol*100).toFixed(0)}%"></div></div>
    </div>`).join('');
  const bestAsk=asks.length?asks[0][0]:0,bestBid=bids.length?bids[0][0]:0;
  const spr=bestAsk&&bestBid?(bestAsk-bestBid).toFixed(1):'---';
  document.getElementById('ob-spr').textContent=`SPREAD $${spr}  |  BID ${(mx.ob_ratio*100).toFixed(1)}%  /  ASK ${((1-mx.ob_ratio)*100).toFixed(1)}%`;
  document.getElementById('ob-bids').innerHTML=bids.map(([p,v])=>
    `<div class="obr">
      <span class="obp win">$${p.toLocaleString('en-US',{maximumFractionDigits:1})}</span>
      <span class="obvol">${v.toFixed(0)}</span>
      <div class="obbw"><div class="obb bid" style="width:${(v/maxVol*100).toFixed(0)}%"></div></div>
    </div>`).join('');
}

function updateAgentCard(key,name,score,direction,reason){
  const dirEl=document.getElementById('ag-dir-'+key);
  if(!dirEl)return;
  const dir=direction?direction.toUpperCase():'NEUTRAL';
  dirEl.textContent=dir;
  dirEl.className='agent-dir '+(dir==='LONG'?'long':dir==='SHORT'?'short':'neutral');
  const barEl=document.getElementById('ag-bar-'+key);
  barEl.style.width=score+'%';
  barEl.className='agent-bar-fill '+sc(score);
  document.getElementById('ag-score-'+key).textContent=score;
  document.getElementById('ag-score-'+key).className='agent-score-num '+(score>54?'g':score<46?'r':'');
  if(reason)document.getElementById('ag-reason-'+key).textContent=reason;
}

function updateUI(d){
  const sig=d.signal,c=sig.components,mx=d.mexc,div=d.divergence;

  // Stats bar
  document.getElementById('hts').textContent=d.timestamp+' UTC';
  const pe=document.getElementById('hp');
  pe.textContent=fP(d.price);
  pe.style.color=d.price_change_pct>0?'var(--green)':d.price_change_pct<0?'var(--red)':'var(--text-bright)';
  const ce=document.getElementById('hc');
  ce.textContent=(d.price_change_pct>=0?'\\u25B2':'\\u25BC')+Math.abs(d.price_change_pct)+'%';
  ce.style.color=d.price_change_pct>=0?'var(--green)':'var(--red)';

  // Pill indicators
  document.getElementById('p-rsi').textContent='RSI '+c.rsi.value+' '+c.rsi.label;
  document.getElementById('p-macd').textContent='MACD '+(d.macd_hist>=0?'+':'')+d.macd_hist;
  document.getElementById('p-fg').textContent='F&G '+c.fear_greed.value;
  const divEl=document.getElementById('p-div');
  if(div.type==='bullish'){divEl.textContent='BULL DIV';divEl.className='pill div-bull';}
  else if(div.type==='bearish'){divEl.textContent='BEAR DIV';divEl.className='pill div-bear';}
  else{divEl.textContent='DIV ---';divEl.className='pill';}
  const fr=mx.funding_rate;
  const frEl=document.getElementById('p-fr');
  frEl.textContent='FUND '+(fr>=0?'+':'')+(fr*100).toFixed(4)+'%';
  frEl.className='pill '+(fr>0.0002?'short':fr<-0.0001?'long':'');
  if(mx.mark_price)document.getElementById('ph-mark').textContent='MARK '+fP(mx.mark_price)+' | ';

  // Stats
  const brel=document.getElementById('sbr');brel.textContent=fE(d.bankroll);setG(brel,d.pnl);
  document.getElementById('brv').textContent=fE(d.bankroll);
  document.getElementById('brv').style.color=d.pnl>=0?'var(--green)':'var(--red)';
  const pe2=document.getElementById('spnl');pe2.textContent=fE(d.pnl,true);setG(pe2,d.pnl);
  const pp=document.getElementById('spnl-p');pp.textContent=(d.pnl_pct>=0?'+':'')+d.pnl_pct.toFixed(2)+'%';setG(pp,d.pnl);
  const wrel=document.getElementById('swr');wrel.textContent=d.total_trades>0?d.win_rate+'%':'---';setG(wrel,d.pnl);
  document.getElementById('swrsub').textContent=d.wins+' W / '+d.losses+' L / '+d.total_trades+' total';

  // Command Center - score arc
  const arcEl=document.getElementById('score-arc');
  if(arcEl){
    const circumference=2*Math.PI*42;
    const offset=circumference-(sig.score/100)*circumference;
    arcEl.style.strokeDasharray=circumference;
    arcEl.style.strokeDashoffset=offset;
    arcEl.style.stroke=sig.direction==='LONG'?'var(--green)':sig.direction==='SHORT'?'var(--red)':'var(--cyan)';
  }
  document.getElementById('scn').textContent=sig.score;
  document.getElementById('scn').style.color=sig.direction==='LONG'?'var(--green)':sig.direction==='SHORT'?'var(--red)':'var(--text-bright)';
  const wpPct=(sig.win_prob*100).toFixed(1);
  document.getElementById('scwp').textContent=wpPct+'%';
  document.getElementById('scwp').style.color=sig.win_prob>=0.60?'var(--green)':'var(--yellow)';
  document.getElementById('cmd-conf').textContent=sig.win_prob.toFixed(2);
  const cmdDir=document.getElementById('cmd-dir');
  cmdDir.textContent=sig.direction;
  cmdDir.className='cmd-dir '+(sig.direction==='LONG'?'long':sig.direction==='SHORT'?'short':'flat');
  const dt=document.getElementById('dtag');
  dt.textContent=sig.direction;
  dt.style.color=sig.direction==='LONG'?'var(--green)':sig.direction==='SHORT'?'var(--red)':'var(--text)';

  // Agent Matrix
  if(d.agents){
    const ag=d.agents;
    updateAgentCard('kenji',ag.technical.name,ag.technical.score,ag.technical.direction,null);
    updateAgentCard('sarah',ag.sentiment.name,ag.sentiment.score,ag.sentiment.direction,null);
    updateAgentCard('viktor',ag.onchain.name,ag.onchain.score,ag.onchain.direction,null);
    updateAgentCard('diego',ag.orderflow.name,ag.orderflow.score,ag.orderflow.direction,null);
    updateAgentCard('amir',ag.chief.name,ag.chief.score,ag.chief.direction,null);
  }
  if(d.analysis_log&&d.analysis_log.length>0){
    const reasons=d.analysis_log;
    const keys=['kenji','sarah','viktor','diego','amir'];
    reasons.forEach((a,i)=>{
      if(keys[i]){
        const reasonEl=document.getElementById('ag-reason-'+keys[i]);
        if(reasonEl)reasonEl.textContent=a.reason||'';
      }
    });
  }

  // Position HUD
  const posLabel=document.getElementById('pos-status-label');
  if(d.position){
    const p=d.position,isL=p.direction==='LONG';
    posLabel.textContent='OPEN';posLabel.style.color='var(--green)';
    document.getElementById('la-ar').textContent=isL?'\\u25B2':'\\u25BC';
    document.getElementById('la-ar').style.color=isL?'var(--green)':'var(--red)';
    document.getElementById('la-ar').style.opacity='1';
    const upnl=p.unrealized_pnl||0;
    const re=document.getElementById('la-res');
    re.textContent=(isL?'LONG':'SHORT')+' OPEN '+fE(upnl,true);
    re.style.color=upnl>=0?'var(--green)':'var(--red)';
    document.getElementById('la-det').textContent='Trailing stop active';
    document.getElementById('pos-entry').textContent='$'+p.entry_price.toLocaleString();
    document.getElementById('pos-sl').textContent='$'+p.stop_loss.toLocaleString();
    document.getElementById('pos-upnl').textContent=fE(upnl,true);
    document.getElementById('pos-upnl').style.color=upnl>=0?'var(--green)':'var(--red)';
    document.getElementById('pos-dur').textContent=Math.round(p.duration)+'s';
    document.getElementById('pos-lev').textContent=d.leverage?d.leverage+'x':'---';
  } else {
    posLabel.textContent='WAITING';posLabel.style.color='var(--yellow)';
    document.getElementById('pos-entry').textContent='---';
    document.getElementById('pos-sl').textContent='---';
    document.getElementById('pos-upnl').textContent='---';
    document.getElementById('pos-upnl').style.color='var(--text)';
    document.getElementById('pos-dur').textContent='---';
    document.getElementById('pos-lev').textContent='---';
    if(d.trades&&d.trades.length>0){
      const lt=d.trades[0],isL=lt.direction==='LONG';
      document.getElementById('la-ar').textContent=isL?'\\u25B2':'\\u25BC';
      document.getElementById('la-ar').style.color=isL?'var(--green)':'var(--red)';
      document.getElementById('la-ar').style.opacity='1';
      const re=document.getElementById('la-res');
      re.textContent=(lt.won?'WIN ':'LOSS ')+fE(lt.profit,true);
      re.style.color=lt.won?'var(--green)':'var(--red)';
      document.getElementById('la-det').textContent=lt.direction+' '+fE(lt.stake)+' @'+lt.time;
    }
  }

  // MEXC OB meta
  if(mx.mark_price){
    document.getElementById('mx-mark').textContent=fP(mx.mark_price);
    document.getElementById('mx-idx').textContent=fP(mx.index_price);
  }
  const frv=document.getElementById('mx-fr');
  frv.textContent=(fr>=0?'+':'')+(fr*100).toFixed(4)+'%';
  frv.style.color=fr>0.0002?'var(--red)':fr<-0.0001?'var(--green)':'var(--text-bright)';
  const obv=document.getElementById('mx-ob');
  obv.textContent=(mx.ob_ratio*100).toFixed(1)+'% BID / '+((1-mx.ob_ratio)*100).toFixed(1)+'% ASK';
  obv.style.color=mx.ob_ratio>0.55?'var(--green)':mx.ob_ratio<0.45?'var(--red)':'var(--text-bright)';
  if(mx.bids&&mx.bids.length)updateOB(mx);

  // Trade log (chronological order — oldest first)
  if(d.trades&&d.trades.length>0){
    const newId=d.new_trade?d.new_trade.id:-1;
    const sortedTrades=[...d.trades].sort((a,b)=>a.id-b.id);
    document.getElementById('logbody').innerHTML=sortedTrades.map(t=>{
      const isNew=t.id===newId&&t.id>lastTradeId;
      const isL=t.direction==='LONG';
      const exitP=t.exit_price?'$'+(t.exit_price).toLocaleString('en-US',{maximumFractionDigits:0}):t.current_price?'$'+(t.current_price).toLocaleString('en-US',{maximumFractionDigits:0}):'---';
      const pnlVal=t.profit||0;
      const pnlClass=t.status==='OPEN'?(pnlVal>=0?'win':'loss'):(t.won?'win':'loss');
      const pnlText=t.status==='OPEN'?fE(pnlVal,true)+' ~':fE(pnlVal,true);
      const closeInfo=t.close_reason?t.close_reason+(t.close_time?' @'+t.close_time:''):(t.reasoning||'-');
      return `<tr class="${t.status==='OPEN'?'te':t.won?'wr':'lr'}${isNew?' te':''}">
        <td class="ti">#${t.id}</td>
        <td class="tt">${t.time}</td>
        <td class="tdir ${isL?'win':'loss'}">${isL?'\\u25B2 LONG':'\\u25BC SHORT'}</td>
        <td class="tsc">${t.score}</td>
        <td class="twp">${((t.win_prob||0)*100).toFixed(0)}%</td>
        <td class="tstk">${fE(t.stake)}</td>
        <td class="tep">${t.leverage?t.leverage+'x':'---'}</td>
        <td class="tep">$${(t.entry_price||0).toLocaleString('en-US',{maximumFractionDigits:0})}</td>
        <td class="tep">${exitP}</td>
        <td class="tres ${t.status==='OPEN'?'y':t.won?'win':'loss'}">${t.status==='OPEN'?'~ LIVE':t.won?'WIN':'LOSS'}</td>
        <td class="tpnl ${pnlClass}">${pnlText}</td>
        <td class="tbr">${fE(t.bankroll)}</td>
        <td class="treason">${closeInfo}</td>
      </tr>`;
    }).join('');
    if(d.new_trade&&d.new_trade.id>lastTradeId){showToast(d.new_trade);lastTradeId=d.new_trade.id;}
    // Auto-scroll to newest trade
    const logEl=document.querySelector('.logtable');
    if(logEl)logEl.scrollTop=logEl.scrollHeight;
  }

  // Hidden compat elements
  document.getElementById('agn').textContent=sig.score;
  document.getElementById('agwp').textContent='win prob: '+wpPct+'% | direction: '+sig.direction;
  let curText;
  if(d.position){
    const p=d.position,upnl=p.unrealized_pnl||0;
    curText=`POSITION ${p.direction} OPEN | Entry: $${p.entry_price.toLocaleString()} | Stop: $${p.stop_loss.toLocaleString()} | PnL: ${upnl>=0?'+':''}${upnl.toFixed(2)} | ${Math.round(p.duration)}s`;
  } else {
    curText=`Score: ${sig.score}/100 | Direction: ${sig.direction} | Win prob: ${wpPct}% | Next trade in ${d.next_trade_in}s`;
  }
  document.getElementById('ana-cur').textContent=curText;

  // Bankroll chart
  if(d.bankroll_history&&d.bankroll_history.length>1&&brLine){
    brLine.setData(d.bankroll_history.map(h=>({time:h.time,value:h.value})));
    const up=d.bankroll>=d.initial;
    brLine.applyOptions({lineColor:up?'#00ff88':'#ff2255',topColor:up?'rgba(0,255,136,.15)':'rgba(255,34,85,.1)'});
    if(d.trades&&d.trades.length){
      const markers=[...d.trades]
        .filter(t=>t.ts)
        .sort((a,b)=>a.ts-b.ts)
        .map(t=>({
          time:t.ts,
          position:t.won?'belowBar':'aboveBar',
          color:t.won?'#00ff88':'#ff2255',
          shape:t.won?'arrowUp':'arrowDown',
          text:t.direction==='LONG'?'L':'S',
        }));
      brLine.setMarkers(markers);
    }
    brChart.timeScale().fitContent();
  }

  // Live 10s candles + indicators
  if(d.candles_5s&&d.candles_5s.length&&candleSeries){
    candleSeries.setData(d.candles_5s.map(c=>({time:c.time,open:c.open,high:c.high,low:c.low,close:c.close})));
    const ci=d.chart_indicators||{};
    if(ci.ema12&&ci.ema12.length&&ema12Line)ema12Line.setData(ci.ema12);
    if(ci.ema26&&ci.ema26.length&&ema26Line)ema26Line.setData(ci.ema26);
    if(ci.bollinger&&ci.bollinger.length){
      if(bbUpperLine)bbUpperLine.setData(ci.bollinger.map(b=>({time:b.time,value:b.upper})));
      if(bbLowerLine)bbLowerLine.setData(ci.bollinger.map(b=>({time:b.time,value:b.lower})));
    }
    btcChart.timeScale().scrollToRealTime();
  }

  // Leverage display
  if(d.leverage){
    const slEl=document.getElementById('sl-val');
    if(slEl)slEl.textContent=d.leverage+'x';
  }

  cdRemaining=d.next_trade_in;
}

function startCd(){
  if(cdInterval)clearInterval(cdInterval);
  cdInterval=setInterval(()=>{
    cdRemaining=Math.max(0,cdRemaining-1);
    const el=document.getElementById('cd');
    if(el){
      el.textContent=cdRemaining>0?cdRemaining+'s':'READY';
      el.style.color=cdRemaining===0?'var(--green)':'var(--yellow)';
    }
    const el2=document.getElementById('cd2');
    if(el2){
      el2.textContent=cdRemaining>0?cdRemaining+'s':'READY';
      el2.style.color=cdRemaining===0?'var(--green)':'var(--yellow)';
    }
  },1000);
}

async function refresh(){
  try{
    const r=await fetch('/api/data');
    if(!r.ok)throw new Error(r.status);
    updateUI(await r.json());
  }catch(e){const h=document.getElementById('hts');if(h)h.textContent='ERROR -- retrying...';console.error(e);}
  try{
    const sr=await fetch('/api/stats');
    if(sr.ok){
      const s=await sr.json();
      const p=s.performance||{};
      const o=s.optimizer||{};
      const sqEl=document.getElementById('sq-val');if(sqEl)sqEl.textContent=p.win_rate!=null?(p.win_rate*100).toFixed(0)+'%':'---';
      const sqSub=document.getElementById('sq-sub');if(sqSub)sqSub.textContent='min threshold: '+(o.min_quality||0.40).toFixed(2);
      const ssEl=document.getElementById('ss-val');if(ssEl)ssEl.textContent=s.setups_skipped||0;
      const ssSub=document.getElementById('ss-sub');if(ssSub)ssSub.textContent='Taken: '+(s.setups_taken||0);
      const slEl=document.getElementById('sl-val');if(slEl&&s.leverage)slEl.textContent=s.leverage+'x';
      const slSub=document.getElementById('sl-sub');if(slSub)slSub.textContent='Win streak: '+(s.win_streak||0);
      const mq=(o.min_quality||0.40).toFixed(2);
      const ms=((o.max_stake_pct||0.25)*100).toFixed(0);
      const soEl=document.getElementById('so-val');if(soEl)soEl.textContent=p.total_trades>0?'Active':'Learning';
      const soSub=document.getElementById('so-sub');if(soSub)soSub.textContent='min_q='+mq+' | stake_max='+ms+'%';
    }
  }catch(e){}
}

document.addEventListener('DOMContentLoaded',async()=>{
  // Init gridstack
  const cellH=Math.floor((window.innerHeight-totalRows*8)/totalRows);
  gsGrid=GridStack.init({
    cellHeight:cellH,
    margin:4,
    column:12,
    animate:true,
    float:true,
    draggable:{handle:'.panel-header'},
    resizable:{handles:'e,se,s,sw,w'},
    disableOneColumnMode:true,
  });

  // Resize charts on panel resize
  gsGrid.on('resizestop',function(event,el){
    setTimeout(()=>{
      if(el.querySelector('#btc-chart')&&btcChart){
        const c=el.querySelector('#btc-chart');
        btcChart.resize(c.clientWidth,c.clientHeight);
      }
      if(el.querySelector('#br-chart')&&brChart){
        const c=el.querySelector('#br-chart');
        brChart.resize(c.clientWidth,c.clientHeight);
      }
    },50);
  });

  initCharts();
  await refresh();
  startCd();
  setInterval(refresh,2000);
});
</script>
</body>
</html>"""


if __name__ == "__main__":
    print(f"\n  Expert Multi-Agent BTC Trading Dashboard")
    print("  ─────────────────────────────────────────")
    print("  → http://localhost:8080")
    print(f"  Cooldown:  {COOLDOWN_SECONDS}s  (COOLDOWN_SECONDS env)")
    print(f"  Bankroll:  ${INITIAL_BANKROLL}  (INITIAL_BANKROLL env)")
    print("  Agents:    Kenji·Sarah·Viktor·Diego → Amir (Chief) → Yuki (Trailing Stop)")
    print("  Ctrl+C to stop\n")
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
