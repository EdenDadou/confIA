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
