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
MIN_WAIT_SECONDS = int(os.environ.get("MIN_WAIT_SECONDS", "120"))
MIN_QUALITY = float(os.environ.get("MIN_QUALITY", "0.55"))

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
                            if len(state.candles_5s) > 600:
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
                hold_time = now - state.last_trade_ts
                max_hold = COOLDOWN_SECONDS * 5

                if hold_time >= max_hold:
                    close_result = pos_mgr.close_position(price, reason="max_hold_time")
                    await _record_close(close_result)
                elif pos_mgr.check_signal_exit(state.chief_decision).get("action") == "CLOSE":
                    close_result = pos_mgr.close_position(price, reason="signal_reversal")
                    await _record_close(close_result)
                elif hold_time >= COOLDOWN_SECONDS:
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

    # Feed optimizer
    quality = next((t.get("quality", 0.5) for t in state.trades if t.get("status") == "CLOSED"), 0.5)
    optimizer.record_outcome(
        quality=quality, direction=close_result.get("direction", "LONG"),
        won=won, pnl=pnl, score=close_result.get("score", 50),
        agent_agreement=3, stop_multiplier=0.8,
    )
    print(f"[CHIEF] Closed {close_result.get('direction','?')} pnl=${pnl:+.2f} reason={close_result.get('reason','?')} "
          f"bankroll=${state.bankroll:.2f} (optimizer: min_q={optimizer.get_params()['min_quality']:.2f})")


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

@app.on_event("startup")
async def startup():
    # Connect trade journal
    try:
        await trade_repo.connect()
        await trade_repo.run_migrations()
        print("[DB] Trade repository connected")
    except Exception as e:
        print(f"[DB] Not connected (using in-memory): {e}")
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

    live_5s = list(state.candles_5s[-300:])
    if state._5s_bucket:
        live_5s = live_5s + [state._5s_bucket]

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

    return {
        "candles": candles[-40:] if candles else [],
        "candles_5s": live_5s[-300:],
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
<title>MEXC BTC PERP // SIM</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
:root {
  --bg:#060a10; --bg2:#0a1018; --bg3:#0f1825; --bg4:#141f2e;
  --border:rgba(0,210,120,.10); --border-b:rgba(0,210,120,.28);
  --green:#00e87a; --gd:#00a854; --red:#ff2d55; --rd:#c0223f;
  --blue:#00aaff; --yellow:#ffd600; --orange:#ff8c00; --purple:#b57bee;
  --text:#9ab8cc; --tb:#ddeef8; --td:#3a5060; --tm:#506878;
  --font:'JetBrains Mono',monospace;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:var(--font);font-size:12px;overflow:hidden}
body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:9998;background:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,.03) 3px,rgba(0,0,0,.03) 4px)}

/* Shell */
.shell{display:grid;grid-template-rows:42px 72px 1fr 220px;height:100vh;gap:1px;background:var(--border)}

/* Header */
.header{background:var(--bg2);display:flex;align-items:center;padding:0 14px;gap:18px}
.logo{font-size:13px;font-weight:700;color:var(--green);letter-spacing:.14em;display:flex;align-items:center;gap:7px}
.pulse{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 10px var(--green);animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.3;transform:scale(.6)}}
.hpb{display:flex;align-items:baseline;gap:7px}
.hl{font-size:9px;color:var(--td);letter-spacing:.1em}
.hv{font-size:20px;font-weight:600;color:var(--tb);font-variant-numeric:tabular-nums;transition:color .3s}
.hc{font-size:11px;font-weight:600}
.hpills{display:flex;align-items:center;gap:8px;margin-left:6px}
.pill{font-size:9px;font-weight:600;letter-spacing:.1em;padding:2px 7px;border:1px solid var(--border-b);color:var(--tm)}
.pill.long{color:var(--green);border-color:var(--green)}
.pill.short{color:var(--red);border-color:var(--red)}
.pill.div-bull{color:var(--green);border-color:var(--green);animation:glow-g 1.5s ease-in-out infinite}
.pill.div-bear{color:var(--red);border-color:var(--red);animation:glow-r 1.5s ease-in-out infinite}
@keyframes glow-g{0%,100%{box-shadow:0 0 0 transparent}50%{box-shadow:0 0 8px rgba(0,232,122,.4)}}
@keyframes glow-r{0%,100%{box-shadow:0 0 0 transparent}50%{box-shadow:0 0 8px rgba(255,45,85,.4)}}
.hr{margin-left:auto;display:flex;align-items:center;gap:14px}
.live-b{display:flex;align-items:center;gap:5px;font-size:9px;font-weight:700;color:var(--green);letter-spacing:.14em}
.hts{font-size:10px;color:var(--td)}

/* Stats */
.stats{display:grid;grid-template-columns:repeat(5,1fr);background:var(--bg2)}
.stat{padding:8px 13px;border-right:1px solid var(--border);display:flex;flex-direction:column;gap:3px}
.stat:last-child{border-right:none}
.slbl{font-size:9px;font-weight:700;color:var(--td);letter-spacing:.14em;text-transform:uppercase}
.sval{font-size:20px;font-weight:700;color:var(--tb);font-variant-numeric:tabular-nums;line-height:1.1;transition:color .4s}
.ssub{font-size:9px;color:var(--tm)}
.g{color:var(--green)!important} .r{color:var(--red)!important} .y{color:var(--yellow)!important}

/* Score bar */
.sw{display:flex;align-items:center;gap:6px;margin-top:2px}
.st{flex:1;height:5px;background:var(--bg3);border:1px solid var(--border);position:relative;overflow:hidden}
.sf{position:absolute;left:0;top:0;height:100%;transition:width .9s cubic-bezier(.4,0,.2,1);background:linear-gradient(90deg,var(--gd),var(--green))}
.sf.sh{background:linear-gradient(90deg,var(--rd),var(--red))}
.sn{font-size:13px;font-weight:700;width:26px;text-align:right;font-variant-numeric:tabular-nums}
.dtag{font-size:9px;font-weight:700;letter-spacing:.1em;padding:1px 7px;border:1px solid}
.dtag.dl{color:var(--green);border-color:var(--green)}
.dtag.ds{color:var(--red);border-color:var(--red)}
.dtag.df{color:var(--tm);border-color:var(--td)}

/* Last action */
.la-row{display:flex;align-items:center;gap:8px;margin-top:4px}
.la-ar{font-size:28px;font-weight:900;line-height:1;transition:all .4s}
.la-info{display:flex;flex-direction:column;gap:1px}
.la-res{font-size:11px;font-weight:700}
.la-det{font-size:9px;color:var(--tm)}

/* Main */
.main{display:grid;grid-template-columns:1fr 290px;background:var(--bg2);overflow:hidden}

/* Charts left col */
.lc{display:grid;grid-template-rows:1fr 110px;border-right:1px solid var(--border);overflow:hidden}
.cp{overflow:hidden;display:flex;flex-direction:column}
.ph{height:28px;background:var(--bg3);flex-shrink:0;display:flex;align-items:center;padding:0 12px;gap:10px;border-bottom:1px solid var(--border);font-size:9px;font-weight:700;letter-spacing:.14em;color:var(--td)}
.ph-t{color:var(--tb);font-size:10px}
#btc-chart{flex:1}
.brp{border-top:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.br-v{font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}
#br-chart{flex:1}

/* Right col */
.rc{display:grid;grid-template-rows:1fr 1fr;overflow:hidden}

/* Signals */
.sigp{border-bottom:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.sigl{padding:7px 12px;display:flex;flex-direction:column;gap:6px;flex:1;overflow:hidden}
.sigr{display:flex;flex-direction:column;gap:3px}
.sigh{display:flex;justify-content:space-between}
.sign{font-size:9px;font-weight:700;color:var(--td);letter-spacing:.12em}
.sigv{font-size:9px;color:var(--tm)}
.sigt{width:100%;height:4px;background:var(--bg3);border:1px solid var(--border);position:relative;overflow:hidden}
.sigf{position:absolute;left:0;top:0;height:100%;transition:width 1s cubic-bezier(.4,0,.2,1)}
.sigf.bull{background:var(--gd)} .sigf.bear{background:var(--rd)} .sigf.neut{background:var(--td)}
.sigf.div-bull{background:var(--green);box-shadow:0 0 6px rgba(0,232,122,.4)}
.sigf.div-bear{background:var(--red);box-shadow:0 0 6px rgba(255,45,85,.4)}
.sigagg{padding:8px 12px;border-top:1px solid var(--border);background:var(--bg3);display:flex;align-items:center;justify-content:space-between}
.aggl{font-size:9px;font-weight:700;color:var(--td);letter-spacing:.12em}
.aggwp{font-size:9px;color:var(--tm);margin-top:2px}
.aggn{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}

/* MEXC OB */
.mxp{display:flex;flex-direction:column;overflow:hidden}
.mxm{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border);flex-shrink:0}
.mxc{padding:4px 10px;background:var(--bg3)}
.mxl{font-size:8px;color:var(--td);letter-spacing:.1em}
.mxv{font-size:11px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--text)}
.obw{flex:1;overflow:hidden;display:flex;flex-direction:column;padding:5px 10px;gap:2px}
.obh{display:flex;justify-content:space-between;font-size:8px;font-weight:700;color:var(--td);letter-spacing:.1em;margin-bottom:2px}
.obr{display:flex;align-items:center;gap:5px;height:13px}
.obp{font-size:9px;font-weight:600;font-variant-numeric:tabular-nums;width:76px}
.obvol{font-size:9px;color:var(--tm);font-variant-numeric:tabular-nums;width:52px;text-align:right}
.obbw{flex:1;height:5px;position:relative}
.obb{position:absolute;top:0;height:100%;border-radius:1px;transition:width .5s}
.obb.bid{left:0;background:rgba(0,232,122,.25)} .obb.ask{left:0;background:rgba(255,45,85,.25)}
.obsp{height:1px;background:var(--border);margin:2px 0;flex-shrink:0}
.obspread{font-size:9px;color:var(--tm);text-align:center;padding:1px 0}

/* Bottom: log + analysis */
.bottom{background:var(--bg2);overflow:hidden;display:grid;grid-template-columns:1fr 380px}

/* Trade log */
.logp{display:flex;flex-direction:column;overflow:hidden;border-right:1px solid var(--border)}
.logh{height:28px;background:var(--bg3);flex-shrink:0;display:flex;align-items:center;padding:0 12px;gap:14px;border-bottom:1px solid var(--border);font-size:9px;font-weight:700;letter-spacing:.14em;color:var(--td)}
.logt{color:var(--tb);font-size:10px}
.logst{font-size:9px;color:var(--tm)}
.cdw{margin-left:auto;font-size:9px;color:var(--td)}
.cd{font-weight:700}
.logtable{flex:1;overflow-y:auto}

table{width:100%;border-collapse:collapse}
thead th{font-size:8px;font-weight:700;letter-spacing:.1em;color:var(--td);padding:3px 8px;text-align:left;border-bottom:1px solid var(--border);background:var(--bg3);position:sticky;top:0;z-index:2}
tbody tr{border-bottom:1px solid rgba(0,210,120,.04);transition:background .1s;border-left:2px solid transparent}
tbody tr.wr{border-left-color:var(--gd)} tbody tr.lr{border-left-color:var(--rd)}
tbody tr:hover{background:var(--bg3)}
tbody tr.te{animation:rslide .5s ease-out}
@keyframes rslide{from{opacity:0;transform:translateX(-10px);background:rgba(0,232,122,.12)}to{opacity:1;transform:translateX(0);background:transparent}}
td{padding:3px 8px;font-size:10px;font-variant-numeric:tabular-nums;white-space:nowrap}
.ti{color:var(--td);width:30px} .tt{color:var(--tm);width:65px}
.tdir{font-weight:700;width:58px;font-size:11px}
.tsc{color:var(--tm);width:36px} .twp{color:var(--tm);width:38px}
.tstk{width:56px} .tep{color:var(--tm);width:84px;font-size:9px}
.tres{font-weight:700;width:48px} .tpnl{font-weight:700;width:58px} .tbr{color:var(--tm)}
.treason{font-size:8px;color:var(--tm);max-width:180px;overflow:hidden;text-overflow:ellipsis}
.win{color:var(--green)} .loss{color:var(--red)}
.div-mark{color:var(--purple);font-size:9px;font-weight:700}
.empty-l{padding:12px;text-align:center;color:var(--td);font-size:10px}

/* Analysis panel */
.anap{display:flex;flex-direction:column;overflow:hidden}
.anah{height:28px;background:var(--bg3);flex-shrink:0;display:flex;align-items:center;padding:0 12px;gap:10px;border-bottom:1px solid var(--border);font-size:9px;font-weight:700;letter-spacing:.14em;color:var(--td)}
.ana-t{color:var(--tb);font-size:10px}
.analist{flex:1;overflow-y:auto;padding:8px 12px;display:flex;flex-direction:column;gap:8px}
.ana-entry{display:flex;flex-direction:column;gap:3px;padding:7px 10px;background:var(--bg3);border-left:2px solid var(--border-b)}
.ana-hdr{display:flex;justify-content:space-between;align-items:center}
.ana-time{font-size:8px;color:var(--td);letter-spacing:.1em}
.ana-score{font-size:9px;font-weight:700}
.ana-reason{font-size:9px;color:var(--text);line-height:1.5}
.ana-empty{padding:16px 12px;text-align:center;color:var(--td);font-size:10px}
.ana-current{padding:8px 12px;background:var(--bg4);border-top:1px solid var(--border);flex-shrink:0}
.ana-cur-lbl{font-size:8px;color:var(--td);letter-spacing:.1em;margin-bottom:4px}
.ana-cur-body{font-size:9px;color:var(--text);line-height:1.6}

/* Toast */
.toast{position:fixed;top:52px;right:14px;width:260px;background:var(--bg3);border:1px solid var(--border-b);z-index:1000;display:none;flex-direction:column;box-shadow:0 8px 32px rgba(0,0,0,.55);animation:tin .35s cubic-bezier(.22,1,.36,1)}
@keyframes tin{from{opacity:0;transform:translateX(18px)}to{opacity:1;transform:translateX(0)}}
.toast.show{display:flex}
.tt-top{padding:9px 13px 5px;display:flex;align-items:center;gap:9px}
.tt-ar{font-size:32px;font-weight:900;line-height:1}
.tt-info{flex:1}
.tt-lbl{font-size:9px;font-weight:700;letter-spacing:.13em;color:var(--td)}
.tt-dir{font-size:14px;font-weight:700}
.tt-body{padding:5px 13px 9px;display:grid;grid-template-columns:1fr 1fr;gap:4px}
.ttb-i{display:flex;flex-direction:column;gap:1px}
.ttb-l{font-size:8px;color:var(--td);letter-spacing:.1em}
.ttb-v{font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}
.tt-bar{height:2px}
.tt-reason{padding:0 13px 8px;font-size:8px;color:var(--tm);line-height:1.5;border-top:1px solid var(--border);padding-top:6px;margin-top:2px}

::-webkit-scrollbar{width:3px;height:3px}
::-webkit-scrollbar-thumb{background:rgba(0,210,120,.2)}
</style>
</head>
<body>
<div class="shell">

  <!-- Header -->
  <header class="header">
    <div class="logo"><div class="pulse"></div>MULTI-AGENT BTC</div>
    <div class="hpb">
      <span class="hl">BTC/USDT</span>
      <span class="hv" id="hp">—</span>
      <span class="hc" id="hc">—</span>
    </div>
    <div class="hpills">
      <span class="pill" id="p-rsi">RSI —</span>
      <span class="pill" id="p-macd">MACD —</span>
      <span class="pill" id="p-fg">F&G —</span>
      <span class="pill" id="p-div">DIV —</span>
      <span class="pill" id="p-fr">FUND —</span>
    </div>
    <div class="hr">
      <div class="live-b"><div class="pulse" style="width:5px;height:5px"></div>LIVE</div>
      <span class="hts" id="hts">—</span>
    </div>
  </header>

  <!-- Stats -->
  <div class="stats">
    <div class="stat">
      <div class="slbl">Bankroll</div>
      <div class="sval" id="sbr">€100.00</div>
      <div class="ssub">Initial: €100.00</div>
    </div>
    <div class="stat">
      <div class="slbl">P &amp; L</div>
      <div class="sval" id="spnl">€0.00</div>
      <div class="ssub" id="spnl-p">+0.00%</div>
    </div>
    <div class="stat">
      <div class="slbl">Win Rate</div>
      <div class="sval" id="swr">—</div>
      <div class="ssub" id="swrsub">0 W · 0 L</div>
    </div>
    <div class="stat">
      <div class="slbl">Last Position</div>
      <div class="la-row" style="margin-top:3px">
        <span class="la-ar" id="la-ar" style="color:var(--td)">·</span>
        <div class="la-info">
          <span class="la-res" id="la-res" style="color:var(--tm)">Watching…</span>
          <span class="la-det" id="la-det"></span>
        </div>
      </div>
    </div>
    <div class="stat">
      <div class="slbl" style="display:flex;justify-content:space-between;align-items:center">
        <span>Signal Score</span>
        <span class="dtag df" id="dtag">FLAT</span>
      </div>
      <div class="sw">
        <div class="st"><div class="sf" id="scf" style="width:50%"></div></div>
        <span class="sn" id="scn">50</span>
      </div>
      <div class="ssub" id="scwp">win prob: 50.0% (need ≥70%)</div>
    </div>
  </div>

  <!-- Smart Trading Stats -->
  <div class="stats" style="grid-template-columns:repeat(4,1fr);margin-top:2px;border-top:1px solid var(--bd)">
    <div class="stat">
      <div class="slbl">Setup Quality</div>
      <div class="sval" id="sq-val">—</div>
      <div class="ssub" id="sq-sub">min threshold: 0.55</div>
    </div>
    <div class="stat">
      <div class="slbl">Setups Skipped</div>
      <div class="sval" id="ss-val">0</div>
      <div class="ssub" id="ss-sub">Taken: 0</div>
    </div>
    <div class="stat">
      <div class="slbl">Leverage</div>
      <div class="sval" id="sl-val">—</div>
      <div class="ssub" id="sl-sub">Dynamic</div>
    </div>
    <div class="stat">
      <div class="slbl">Optimizer</div>
      <div class="sval" id="so-val">Learning</div>
      <div class="ssub" id="so-sub">min_q=0.55 | stake_max=25%</div>
    </div>
  </div>

  <!-- Main -->
  <div class="main">

    <!-- Left: charts -->
    <div class="lc">
      <div class="cp">
        <div class="ph">
          <span class="ph-t">BTC/USDT</span>
          <span>10S LIVE · BINANCE</span>
          <span id="ph-mark" style="margin-left:auto;color:var(--tm)"></span>
        </div>
        <div id="btc-chart"></div>
      </div>
      <div class="brp">
        <div class="ph" style="justify-content:space-between">
          <span class="ph-t">BANKROLL EVOLUTION</span>
          <span class="br-v" id="brv">€100.00</span>
        </div>
        <div id="br-chart"></div>
      </div>
    </div>

    <!-- Right: signals + OB -->
    <div class="rc">
      <div class="sigp">
        <div class="ph"><span class="ph-t">SIGNAL BREAKDOWN</span></div>
        <div class="sigl">
          <div class="sigr">
            <div class="sigh"><span class="sign">RSI(14)</span><span class="sigv" id="sv-rsi">—</span></div>
            <div class="sigt"><div class="sigf" id="sb-rsi" style="width:50%"></div></div>
          </div>
          <div class="sigr">
            <div class="sigh"><span class="sign">MACD HIST</span><span class="sigv" id="sv-macd">—</span></div>
            <div class="sigt"><div class="sigf" id="sb-macd" style="width:50%"></div></div>
          </div>
          <div class="sigr">
            <div class="sigh"><span class="sign">DIVERGENCE RSI/PRIX</span><span class="sigv" id="sv-div">—</span></div>
            <div class="sigt"><div class="sigf" id="sb-div" style="width:50%"></div></div>
          </div>
          <div class="sigr">
            <div class="sigh"><span class="sign">FEAR &amp; GREED</span><span class="sigv" id="sv-fg">—</span></div>
            <div class="sigt"><div class="sigf" id="sb-fg" style="width:50%"></div></div>
          </div>
          <div class="sigr">
            <div class="sigh"><span class="sign">MEXC OB + FUNDING</span><span class="sigv" id="sv-mx">—</span></div>
            <div class="sigt"><div class="sigf" id="sb-mx" style="width:50%"></div></div>
          </div>
          <div class="sigr">
            <div class="sigh"><span class="sign">MOMENTUM (3C)</span><span class="sigv" id="sv-mom">—</span></div>
            <div class="sigt"><div class="sigf" id="sb-mom" style="width:50%"></div></div>
          </div>
        </div>
        <div class="sigagg">
          <div>
            <div class="aggl">CHIEF TRADER · AMIR HASSAN</div>
            <div class="aggwp" id="agwp">win prob: —</div>
          </div>
          <div class="aggn" id="agn">—</div>
        </div>
      </div>

      <!-- MEXC OB -->
      <div class="mxp">
        <div class="ph"><span class="ph-t">MEXC BTC_USDT PERP — ORDER BOOK</span></div>
        <div class="mxm">
          <div class="mxc"><div class="mxl">MARK PRICE</div><div class="mxv" id="mx-mark">—</div></div>
          <div class="mxc"><div class="mxl">FUNDING RATE</div><div class="mxv" id="mx-fr">—</div></div>
          <div class="mxc"><div class="mxl">INDEX PRICE</div><div class="mxv" id="mx-idx">—</div></div>
          <div class="mxc"><div class="mxl">OB RATIO BID/ASK</div><div class="mxv" id="mx-ob">—</div></div>
        </div>
        <div class="obw">
          <div class="obh"><span>ASKS (sellers)</span><span>QTY</span></div>
          <div id="ob-asks"></div>
          <div class="obsp"></div>
          <div class="obspread" id="ob-spr">—</div>
          <div class="obsp"></div>
          <div class="obh"><span>BIDS (buyers)</span><span>QTY</span></div>
          <div id="ob-bids"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- Bottom: trade log + analysis -->
  <div class="bottom">

    <!-- Trade log -->
    <div class="logp">
      <div class="logh">
        <span class="logt">POSITION LOG</span>
        <span class="logst" id="logst">50x leverage · 1/2 Kelly · 5 agents · trailing stop</span>
        <span class="cdw">Next: <span class="cd" id="cd">—</span></span>
      </div>
      <div class="logtable">
        <table>
          <thead><tr>
            <th>#</th><th>TIME</th><th>POS</th><th>SC</th><th>WP%</th>
            <th>STAKE</th><th>ENTRY</th><th>EXIT</th><th>RES</th><th>P&L</th><th>BANKROLL</th><th>RAISON</th>
          </tr></thead>
          <tbody id="logbody">
            <tr><td colspan="12" class="empty-l">Agents initializing — first trade in ${COOLDOWN_SECONDS}s...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Analysis -->
    <div class="anap">
      <div class="anah">
        <span class="ana-t">ANALYSE DU MARCHÉ</span>
        <span style="font-size:9px;color:var(--tm)">expert agents reasoning</span>
      </div>
      <div class="analist" id="analist">
        <div class="ana-empty">En attente de données…</div>
      </div>
      <div class="ana-current">
        <div class="ana-cur-lbl">ÉTAT ACTUEL</div>
        <div class="ana-cur-body" id="ana-cur">—</div>
      </div>
    </div>

  </div>
</div>

<!-- Toast -->
<div class="toast" id="toast">
  <div class="tt-top">
    <span class="tt-ar" id="t-ar">▲</span>
    <div class="tt-info">
      <div class="tt-lbl">POSITION OUVERTE — MEXC PERP</div>
      <div class="tt-dir" id="t-dir">—</div>
    </div>
  </div>
  <div class="tt-body">
    <div class="ttb-i"><span class="ttb-l">SCORE</span><span class="ttb-v" id="t-sc">—</span></div>
    <div class="ttb-i"><span class="ttb-l">WIN PROB</span><span class="ttb-v" id="t-wp">—</span></div>
    <div class="ttb-i"><span class="ttb-l">STAKE</span><span class="ttb-v" id="t-st">—</span></div>
    <div class="ttb-i"><span class="ttb-l">RÉSULTAT</span><span class="ttb-v" id="t-res">—</span></div>
  </div>
  <div class="tt-reason" id="t-rsn"></div>
  <div class="tt-bar" id="t-bar"></div>
</div>

<script>
let btcChart=null,brChart=null,candleSeries=null,brLine=null;
let lastTradeId=0,toastTimer=null,cdInterval=null,cdRemaining=0;

function initCharts() {
  const btcEl=document.getElementById('btc-chart');
  btcChart=LightweightCharts.createChart(btcEl,{
    width:btcEl.clientWidth,height:btcEl.clientHeight,
    layout:{background:{color:'#0a1018'},textColor:'#506878',fontFamily:"'JetBrains Mono',monospace",fontSize:10},
    grid:{vertLines:{color:'rgba(0,210,120,0.06)'},horzLines:{color:'rgba(0,210,120,0.06)'}},
    crosshair:{mode:LightweightCharts.CrosshairMode.Normal},
    rightPriceScale:{borderColor:'rgba(0,210,120,.10)',scaleMargins:{top:.08,bottom:.08}},
    timeScale:{borderColor:'rgba(0,210,120,.10)',timeVisible:true,secondsVisible:true,rightOffset:3},
  });
  candleSeries=btcChart.addCandlestickSeries({
    upColor:'#00e87a',downColor:'#ff2d55',
    borderUpColor:'#00e87a',borderDownColor:'#ff2d55',
    wickUpColor:'#00cc66',wickDownColor:'#cc2244',
  });

  const brEl=document.getElementById('br-chart');
  brChart=LightweightCharts.createChart(brEl,{
    width:brEl.clientWidth,height:brEl.clientHeight,
    layout:{background:{color:'#0a1018'},textColor:'#506878',fontFamily:"'JetBrains Mono',monospace",fontSize:9},
    grid:{vertLines:{color:'rgba(0,0,0,0)'},horzLines:{color:'rgba(0,210,120,0.04)'}},
    crosshair:{mode:LightweightCharts.CrosshairMode.Normal},
    rightPriceScale:{borderColor:'rgba(0,210,120,.10)',scaleMargins:{top:.1,bottom:.1}},
    timeScale:{visible:false},handleScroll:false,handleScale:false,
  });
  brLine=brChart.addAreaSeries({lineColor:'#00e87a',topColor:'rgba(0,232,122,.18)',bottomColor:'rgba(0,0,0,0)',lineWidth:2});

  window.addEventListener('resize',()=>{
    btcChart.resize(btcEl.clientWidth,btcEl.clientHeight);
    brChart.resize(brEl.clientWidth,brEl.clientHeight);
  });
}

function fE(v,plus=false){const a=Math.abs(v).toFixed(2);return v<0?'-€'+a:(plus&&v>0?'+':'')+'€'+a}
function fP(v){return '$'+v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}
function sc(s){return s>54?'bull':s<46?'bear':'neut'}
function setG(el,v){el.classList.remove('g','r','y');if(v>0)el.classList.add('g');else if(v<0)el.classList.add('r')}

function showToast(t){
  const isL=t.direction==='LONG';
  document.getElementById('t-ar').textContent=isL?'▲':'▼';
  document.getElementById('t-ar').style.color=isL?'var(--green)':'var(--red)';
  document.getElementById('t-dir').textContent=(isL?'⬆ LONG':'⬇ SHORT')+' #'+t.id;
  document.getElementById('t-dir').style.color=isL?'var(--green)':'var(--red)';
  document.getElementById('t-sc').textContent=t.score+'/100';
  document.getElementById('t-wp').textContent=(t.win_prob*100).toFixed(1)+'%';
  document.getElementById('t-st').textContent=fE(t.stake);
  document.getElementById('t-res').textContent=t.won?'✓ WIN':'✗ LOSS';
  document.getElementById('t-res').style.color=t.won?'var(--green)':'var(--red)';
  document.getElementById('t-bar').style.background=t.won?'var(--green)':'var(--red)';
  document.getElementById('t-rsn').textContent='Raison : '+t.reasoning;
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
  const spr=bestAsk&&bestBid?(bestAsk-bestBid).toFixed(1):'—';
  document.getElementById('ob-spr').textContent=`SPREAD $${spr}  ·  BID ${(mx.ob_ratio*100).toFixed(1)}%  /  ASK ${((1-mx.ob_ratio)*100).toFixed(1)}%`;
  document.getElementById('ob-bids').innerHTML=bids.map(([p,v])=>
    `<div class="obr">
      <span class="obp win">$${p.toLocaleString('en-US',{maximumFractionDigits:1})}</span>
      <span class="obvol">${v.toFixed(0)}</span>
      <div class="obbw"><div class="obb bid" style="width:${(v/maxVol*100).toFixed(0)}%"></div></div>
    </div>`).join('');
}

function updateUI(d){
  const sig=d.signal,c=sig.components,mx=d.mexc,div=d.divergence;

  // Header
  document.getElementById('hts').textContent=d.timestamp+' UTC';
  const pe=document.getElementById('hp');
  pe.textContent=fP(d.price);
  pe.style.color=d.price_change_pct>0?'var(--green)':d.price_change_pct<0?'var(--red)':'var(--tb)';
  const ce=document.getElementById('hc');
  ce.textContent=(d.price_change_pct>=0?'▲':'▼')+Math.abs(d.price_change_pct)+'%';
  ce.className='hc '+(d.price_change_pct>=0?'g':'r');

  document.getElementById('p-rsi').textContent='RSI '+c.rsi.value+' '+c.rsi.label;
  document.getElementById('p-macd').textContent='MACD '+(d.macd_hist>=0?'+':'')+d.macd_hist;
  document.getElementById('p-fg').textContent='F&G '+c.fear_greed.value;

  const divEl=document.getElementById('p-div');
  if(div.type==='bullish'){
    divEl.textContent='◆ BULL DIV';divEl.className='pill div-bull';
  } else if(div.type==='bearish'){
    divEl.textContent='◆ BEAR DIV';divEl.className='pill div-bear';
  } else {
    divEl.textContent='DIV none';divEl.className='pill';
  }

  const fr=mx.funding_rate;
  const frEl=document.getElementById('p-fr');
  frEl.textContent='FUND '+(fr>=0?'+':'')+( fr*100).toFixed(4)+'%';
  frEl.className='pill '+(fr>0.0002?'short':fr<-0.0001?'long':'');
  if(mx.mark_price)document.getElementById('ph-mark').textContent='MARK '+fP(mx.mark_price);

  // Stats
  const brel=document.getElementById('sbr');brel.textContent=fE(d.bankroll);setG(brel,d.pnl);
  document.getElementById('brv').textContent=fE(d.bankroll);
  document.getElementById('brv').style.color=d.pnl>=0?'var(--green)':'var(--red)';
  const pe2=document.getElementById('spnl');pe2.textContent=fE(d.pnl,true);setG(pe2,d.pnl);
  const pp=document.getElementById('spnl-p');pp.textContent=(d.pnl_pct>=0?'+':'')+d.pnl_pct.toFixed(2)+'%';setG(pp,d.pnl);
  const wrel=document.getElementById('swr');wrel.textContent=d.total_trades>0?d.win_rate+'%':'—';setG(wrel,d.pnl);
  document.getElementById('swrsub').textContent=d.wins+' W · '+d.losses+' L · '+d.total_trades+' trades';

  if(d.position){
    const p=d.position,isL=p.direction==='LONG';
    document.getElementById('la-ar').textContent=isL?'▲':'▼';
    document.getElementById('la-ar').style.color=isL?'var(--green)':'var(--red)';
    document.getElementById('la-ar').style.animation='pulse 1s ease-in-out infinite';
    const re=document.getElementById('la-res');
    const upnl=p.unrealized_pnl||0;
    re.textContent=(isL?'LONG':'SHORT')+' OPEN '+fE(upnl,true);
    re.style.color=upnl>=0?'var(--green)':'var(--red)';
    document.getElementById('la-det').textContent='Stop: $'+p.stop_loss.toLocaleString()+' · '+Math.round(p.duration)+'s';
  } else if(d.trades&&d.trades.length>0){
    const lt=d.trades[0],isL=lt.direction==='LONG';
    document.getElementById('la-ar').textContent=isL?'▲':'▼';
    document.getElementById('la-ar').style.color=isL?'var(--green)':'var(--red)';
    document.getElementById('la-ar').style.animation='none';
    const re=document.getElementById('la-res');
    re.textContent=(lt.won?'✓ WIN ':'✗ LOSS ')+fE(lt.profit,true);
    re.style.color=lt.won?'var(--green)':'var(--red)';
    document.getElementById('la-det').textContent=lt.direction+' '+fE(lt.stake)+' @'+lt.time;
  }

  // Score
  const sf=document.getElementById('scf');
  sf.style.width=sig.score+'%';sf.className='sf '+(sig.direction==='SHORT'?'sh':'');
  const sn=document.getElementById('scn');
  sn.textContent=sig.score;sn.className='sn '+(sig.direction==='LONG'?'g':sig.direction==='SHORT'?'r':'');
  const wpPct=(sig.win_prob*100).toFixed(1);
  const enough=sig.win_prob>=0.70;
  document.getElementById('scwp').textContent='win prob: '+wpPct+'% · trade every '+d.cooldown_seconds+'s';
  document.getElementById('scwp').style.color=sig.win_prob>=0.60?'var(--green)':'var(--yellow)';
  const dt=document.getElementById('dtag');
  dt.textContent=sig.direction;
  dt.className='dtag '+(sig.direction==='LONG'?'dl':sig.direction==='SHORT'?'ds':'df');

  // Signal bars
  function sb(bid,vid,score,label,special){
    const b=document.getElementById(bid);
    b.style.width=score+'%';
    b.className='sigf '+(special||sc(score));
    document.getElementById(vid).textContent=label+'  ['+score+']';
  }
  sb('sb-rsi','sv-rsi',c.rsi.score,c.rsi.value+' '+c.rsi.label);
  sb('sb-macd','sv-macd',c.macd.score,'HIST '+(d.macd_hist>=0?'+':'')+d.macd_hist);
  const divSp=div.type==='bullish'?'div-bull':div.type==='bearish'?'div-bear':'neut';
  sb('sb-div','sv-div',c.divergence.score,c.divergence.label+(div.rsi_delta?' ΔRSI='+(div.rsi_delta>0?'+':'')+div.rsi_delta:''),divSp);
  sb('sb-fg','sv-fg',c.fear_greed.score,'Index '+c.fear_greed.value);
  sb('sb-mx','sv-mx',c.mexc.score,'OB '+(mx.ob_ratio*100).toFixed(1)+'% bid');
  sb('sb-mom','sv-mom',c.momentum.score,c.momentum.label);

  const an=document.getElementById('agn');
  an.textContent=sig.score;
  an.className='aggn '+(sig.direction==='LONG'?'g':sig.direction==='SHORT'?'r':'');
  document.getElementById('agwp').textContent='win prob: '+wpPct+'% · direction: '+sig.direction;

  // MEXC meta
  if(mx.mark_price){
    document.getElementById('mx-mark').textContent=fP(mx.mark_price);
    document.getElementById('mx-idx').textContent=fP(mx.index_price);
  }
  const frv=document.getElementById('mx-fr');
  frv.textContent=(fr>=0?'+':'')+(fr*100).toFixed(4)+'%';
  frv.className='mxv '+(fr>0.0002?'r':fr<-0.0001?'g':'');
  const obv=document.getElementById('mx-ob');
  obv.textContent=(mx.ob_ratio*100).toFixed(1)+'% BID / '+((1-mx.ob_ratio)*100).toFixed(1)+'% ASK';
  obv.className='mxv '+(mx.ob_ratio>0.55?'g':mx.ob_ratio<0.45?'r':'');
  if(mx.bids&&mx.bids.length)updateOB(mx);

  // Trade log
  if(d.trades&&d.trades.length>0){
    const newId=d.new_trade?d.new_trade.id:-1;
    document.getElementById('logbody').innerHTML=d.trades.map(t=>{
      const isNew=t.id===newId&&t.id>lastTradeId;
      const isL=t.direction==='LONG';
      const exitP=t.exit_price?'$'+(t.exit_price).toLocaleString('en-US',{maximumFractionDigits:0}):t.current_price?'$'+(t.current_price).toLocaleString('en-US',{maximumFractionDigits:0}):'—';
      const pnlVal=t.profit||0;
      const pnlClass=t.status==='OPEN'?(pnlVal>=0?'win':'loss'):(t.won?'win':'loss');
      const pnlText=t.status==='OPEN'?fE(pnlVal,true)+' ⟳':fE(pnlVal,true);
      const closeInfo=t.close_reason?t.close_reason+(t.close_time?' @'+t.close_time:''):(t.reasoning||'-');
      return `<tr class="${t.status==='OPEN'?'te':t.won?'wr':'lr'}${isNew?' te':''}">
        <td class="ti">#${t.id}</td>
        <td class="tt">${t.time}</td>
        <td class="tdir ${isL?'win':'loss'}">${isL?'▲ LONG':'▼ SHORT'}</td>
        <td class="tsc">${t.score}</td>
        <td class="twp">${((t.win_prob||0)*100).toFixed(0)}%</td>
        <td class="tstk">${fE(t.stake)}</td>
        <td class="tep">$${(t.entry_price||0).toLocaleString('en-US',{maximumFractionDigits:0})}</td>
        <td class="tep">${exitP}</td>
        <td class="tres ${t.status==='OPEN'?'y':t.won?'win':'loss'}">${t.status==='OPEN'?'⟳ LIVE':t.won?'✓ WIN':'✗ LOSS'}</td>
        <td class="tpnl ${pnlClass}">${pnlText}</td>
        <td class="tbr">${fE(t.bankroll)}</td>
        <td class="treason">${closeInfo}</td>
      </tr>`;
    }).join('');
    if(d.new_trade&&d.new_trade.id>lastTradeId){showToast(d.new_trade);lastTradeId=d.new_trade.id;}
  }

  // Analysis log
  const al=document.getElementById('analist');
  if(d.analysis_log&&d.analysis_log.length>0){
    al.innerHTML=d.analysis_log.map(a=>`
      <div class="ana-entry">
        <div class="ana-hdr">
          <span class="ana-time">${a.time} UTC</span>
          <span class="ana-score" style="color:${a.win_prob>=70?'var(--green)':'var(--red)'}">${a.win_prob}% win prob · score ${a.score}</span>
        </div>
        <div class="ana-reason">${a.reason}</div>
      </div>`).join('');
  } else {
    al.innerHTML='<div class="ana-empty">Agents starting up...</div>';
  }

  // Current state
  let curText;
  if(d.position){
    const p=d.position,upnl=p.unrealized_pnl||0;
    curText=`POSITION ${p.direction} OUVERTE — Entry: $${p.entry_price.toLocaleString()} · Stop: $${p.stop_loss.toLocaleString()} · PnL: ${upnl>=0?'+':''}${upnl.toFixed(2)}€ · ${Math.round(p.duration)}s`;
  } else {
    curText=`Score: ${sig.score}/100 · Direction: ${sig.direction} · Win prob: ${wpPct}% · Next trade in ${d.next_trade_in}s`;
  }
  document.getElementById('ana-cur').textContent=curText;

  // Bankroll chart
  if(d.bankroll_history&&d.bankroll_history.length>1&&brLine){
    brLine.setData(d.bankroll_history.map(h=>({time:h.time,value:h.value})));
    const up=d.bankroll>=d.initial;
    brLine.applyOptions({lineColor:up?'#00e87a':'#ff2d55',topColor:up?'rgba(0,232,122,.18)':'rgba(255,45,85,.12)'});
    // Trade entry markers
    if(d.trades&&d.trades.length){
      const markers=[...d.trades]
        .filter(t=>t.ts)
        .sort((a,b)=>a.ts-b.ts)
        .map(t=>({
          time:t.ts,
          position:t.won?'belowBar':'aboveBar',
          color:t.won?'#00e87a':'#ff2d55',
          shape:t.won?'arrowUp':'arrowDown',
          text:t.direction==='LONG'?'L':'S',
        }));
      brLine.setMarkers(markers);
    }
    brChart.timeScale().fitContent();
  }

  // Live 10s candles
  if(d.candles_5s&&d.candles_5s.length&&candleSeries){
    candleSeries.setData(d.candles_5s.map(c=>({time:c.time,open:c.open,high:c.high,low:c.low,close:c.close})));
    btcChart.timeScale().scrollToRealTime();
  }

  cdRemaining=d.next_trade_in;
}

function startCd(){
  if(cdInterval)clearInterval(cdInterval);
  cdInterval=setInterval(()=>{
    cdRemaining=Math.max(0,cdRemaining-1);
    const el=document.getElementById('cd');
    el.textContent=cdRemaining>0?cdRemaining+'s':'READY';
    el.style.color=cdRemaining===0?'var(--green)':'var(--yellow)';
  },1000);
}

async function refresh(){
  try{
    const r=await fetch('/api/data');
    if(!r.ok)throw new Error(r.status);
    updateUI(await r.json());
  }catch(e){document.getElementById('hts').textContent='ERROR — retrying…';console.error(e);}
  // Fetch smart trading stats
  try{
    const sr=await fetch('/api/stats');
    if(sr.ok){
      const s=await sr.json();
      const p=s.performance||{};
      const o=s.optimizer||{};
      document.getElementById('sq-val').textContent=p.win_rate!=null?(p.win_rate*100).toFixed(0)+'%':'—';
      document.getElementById('sq-sub').textContent='min threshold: '+(o.min_quality||0.55).toFixed(2);
      document.getElementById('ss-val').textContent=s.setups_skipped||0;
      document.getElementById('ss-sub').textContent='Taken: '+(s.setups_taken||0);
      document.getElementById('sl-val').textContent=(s.win_streak||0)+'W streak';
      document.getElementById('sl-sub').textContent='Dynamic leverage';
      const mq=(o.min_quality||0.55).toFixed(2);
      const ms=((o.max_stake_pct||0.25)*100).toFixed(0);
      document.getElementById('so-val').textContent=p.total_trades>0?'Active':'Learning';
      document.getElementById('so-sub').textContent='min_q='+mq+' | stake_max='+ms+'%';
    }
  }catch(e){}
}

document.addEventListener('DOMContentLoaded',async()=>{
  initCharts();await refresh();startCd();setInterval(refresh,2000);
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
