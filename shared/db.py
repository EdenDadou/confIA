# shared/db.py
import json as _json
import os
import time
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TRADES_FILE = os.path.join(DATA_DIR, "trades.json")


class TradeRepository:
    """Trade journal -- PostgreSQL in production, JSON file for local dev, in-memory for tests."""

    def __init__(self, database_url: str = None, fake: bool = False):
        self._url = database_url or os.environ.get("DATABASE_URL", "")
        self._fake = fake
        self._pool = None
        # Fake storage for tests / local dev
        self._fake_trades: list[dict] = []
        self._fake_id = 0

    def _load_from_file(self):
        """Load trades from JSON file if it exists."""
        if os.path.exists(TRADES_FILE):
            try:
                with open(TRADES_FILE) as f:
                    data = _json.load(f)
                self._fake_trades = data.get("trades", [])
                self._fake_id = data.get("next_id", 0)
            except Exception:
                pass

    def _save_to_file(self):
        """Persist trades to JSON file."""
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(TRADES_FILE, "w") as f:
            _json.dump({"trades": self._fake_trades, "next_id": self._fake_id}, f, indent=2)

    async def connect(self):
        if self._fake:
            self._load_from_file()
            if self._fake_trades:
                print(f"[DB] Loaded {len(self._fake_trades)} trades from file")
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
            self._save_to_file()
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
            self._save_to_file()
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
