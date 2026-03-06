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
