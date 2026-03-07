export const CONFIG = {
  // Binance
  binance: {
    wsUrl: "wss://stream.binance.com:9443/ws/btcusdt@kline_1m",
    restUrl: "https://api.binance.com",
    symbol: "BTCUSDT",
  },

  // Candle buffers
  candles: {
    maxPerTimeframe: 500,
    timeframes: ["1m", "5m", "15m"] as const,
  },

  // Event detection — AGGRESSIVE: short cooldowns for testing
  events: {
    RSI_EXTREME: { threshold_low: 30, threshold_high: 70, cooldown_ms: 60_000 },
    EMA_CROSS: { cooldown_ms: 2 * 60_000 },
    DIVERGENCE: { min_candles: 3, cooldown_ms: 2 * 60_000 },
    VOLUME_SPIKE: { multiplier: 2, cooldown_ms: 60_000 },
    BB_BREAKOUT: { cooldown_ms: 60_000 },
    FUNDING_FLIP: { cooldown_ms: 5 * 60_000 },
    LIQUIDATION_WAVE: { threshold_usd: 5_000_000, cooldown_ms: 5 * 60_000 },
  },

  // Trading — AGGRESSIVE for testing
  trading: {
    initial_bankroll: 100,
    stop_multiplier: 0.8,
    max_stake_pct: 0.35,
    min_conviction: 0.3,
    leverage_min: 10,
    leverage_max: 50,
  },

  // Circuit breakers
  circuit: {
    max_consecutive_losses: 5,
    pause_after_losses_ms: 60 * 60_000,
    max_drawdown_pct: 0.15,
    pause_after_drawdown_ms: 24 * 60 * 60_000,
  },

  // Claude
  claude: {
    model: "claude-sonnet-4-6",
    max_tokens: 512,
  },

  // Dashboard
  dashboard: {
    port: 8080,
    host: "0.0.0.0",
  },

  // Persistence
  data: {
    trades_file: "data/trades.json",
    patterns_file: "data/patterns.json",
    optimizer_file: "data/optimizer.json",
  },
} as const;

export type Timeframe = (typeof CONFIG.candles.timeframes)[number];
export type EventType = keyof typeof CONFIG.events;
