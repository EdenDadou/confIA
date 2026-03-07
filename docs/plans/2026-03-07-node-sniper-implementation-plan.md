# Sniper BTC Trading Bot — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an event-driven Node.js/TypeScript BTC trading bot that only trades on high-quality setups, uses Claude for contextual analysis, and learns from every outcome.

**Architecture:** Market engine streams Binance data → builds multi-TF candles → event detector spots patterns → Claude brain decides TRADE/SKIP with full context + pattern memory → position manager handles trailing stop exits → learning engine adjusts parameters after each trade. Fastify dashboard serves Jarvis HUD frontend.

**Tech Stack:** Node.js 20+, TypeScript, ws (WebSocket), Fastify, @anthropic-ai/sdk, LightweightCharts, GridStack.js, Vitest

---

### Task 1: Project Scaffold

**Files:**
- Create: `btc-sniper/package.json`
- Create: `btc-sniper/tsconfig.json`
- Create: `btc-sniper/src/index.ts`
- Create: `btc-sniper/src/config/default.ts`

**Step 1: Create project directory and init**

```bash
mkdir -p btc-sniper && cd btc-sniper
npm init -y
```

**Step 2: Install dependencies**

```bash
npm install typescript ws fastify @fastify/static @anthropic-ai/sdk
npm install -D @types/node @types/ws vitest tsx
```

**Step 3: Create tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "outDir": "dist",
    "rootDir": "src",
    "strict": true,
    "esModuleInterop": true,
    "resolveJsonModule": true,
    "declaration": true,
    "sourceMap": true,
    "skipLibCheck": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist", "tests"]
}
```

**Step 4: Create config/default.ts**

```typescript
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

  // Event detection
  events: {
    RSI_EXTREME: { threshold_low: 25, threshold_high: 75, cooldown_ms: 5 * 60_000 },
    EMA_CROSS: { cooldown_ms: 15 * 60_000 },
    DIVERGENCE: { min_candles: 3, cooldown_ms: 10 * 60_000 },
    VOLUME_SPIKE: { multiplier: 3, cooldown_ms: 3 * 60_000 },
    BB_BREAKOUT: { cooldown_ms: 5 * 60_000 },
    FUNDING_FLIP: { cooldown_ms: 30 * 60_000 },
    LIQUIDATION_WAVE: { threshold_usd: 5_000_000, cooldown_ms: 15 * 60_000 },
  },

  // Trading
  trading: {
    initial_bankroll: 100,
    stop_multiplier: 0.8,
    max_stake_pct: 0.25,
    min_conviction: 0.6,
    leverage_min: 5,
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
```

**Step 5: Create src/index.ts (minimal entry point)**

```typescript
import { CONFIG } from "./config/default.js";

console.log(`
  ╔══════════════════════════════════════╗
  ║   BTC SNIPER — Event-Driven Bot     ║
  ║   Mode: Simulation                   ║
  ║   Dashboard: http://localhost:${CONFIG.dashboard.port}   ║
  ╚══════════════════════════════════════╝
`);
```

**Step 6: Add scripts to package.json**

Add to `package.json`:
```json
{
  "type": "module",
  "scripts": {
    "dev": "tsx watch src/index.ts",
    "build": "tsc",
    "start": "node dist/index.js",
    "test": "vitest run",
    "test:watch": "vitest"
  }
}
```

**Step 7: Verify it runs**

Run: `cd btc-sniper && npx tsx src/index.ts`
Expected: Banner prints without errors

**Step 8: Commit**

```bash
git add -A
git commit -m "feat: scaffold Node.js/TypeScript project with config"
```

---

### Task 2: Technical Indicators Library

**Files:**
- Create: `btc-sniper/src/engine/indicators.ts`
- Create: `btc-sniper/tests/indicators.test.ts`

**Step 1: Write the failing tests**

```typescript
// tests/indicators.test.ts
import { describe, it, expect } from "vitest";
import {
  computeRSI, computeEMA, computeMACD, computeBollinger,
  computeATR, computeOBV, computeVolumeSMA, detectDivergence,
} from "../src/engine/indicators.js";

describe("indicators", () => {
  const closes = [
    44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
    46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
    46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03,
    44.18, 44.22, 44.57, 43.42, 42.66, 43.13,
  ];

  it("computes RSI(14)", () => {
    const rsi = computeRSI(closes, 14);
    expect(rsi).toHaveLength(closes.length - 14);
    expect(rsi[0]).toBeGreaterThan(0);
    expect(rsi[0]).toBeLessThan(100);
    // Last RSI should be bearish (price dropped)
    expect(rsi[rsi.length - 1]).toBeLessThan(50);
  });

  it("computes EMA", () => {
    const ema12 = computeEMA(closes, 12);
    expect(ema12).toHaveLength(closes.length);
    // EMA should smooth the data
    expect(ema12[ema12.length - 1]).toBeGreaterThan(42);
    expect(ema12[ema12.length - 1]).toBeLessThan(46);
  });

  it("computes MACD", () => {
    const macd = computeMACD(closes);
    expect(macd.line).toHaveLength(closes.length);
    expect(macd.signal).toHaveLength(closes.length);
    expect(macd.histogram).toHaveLength(closes.length);
  });

  it("computes Bollinger Bands", () => {
    const bb = computeBollinger(closes, 20, 2);
    expect(bb).toHaveLength(closes.length - 19);
    const last = bb[bb.length - 1];
    expect(last.upper).toBeGreaterThan(last.middle);
    expect(last.lower).toBeLessThan(last.middle);
  });

  it("computes ATR", () => {
    const candles = closes.map((c, i) => ({
      open: c - 0.2, high: c + 0.5, low: c - 0.5, close: c, volume: 100,
      time: i,
    }));
    const atr = computeATR(candles, 14);
    expect(atr).toBeGreaterThan(0);
  });

  it("computes OBV", () => {
    const candles = closes.map((c, i) => ({
      open: c, high: c + 0.5, low: c - 0.5, close: c, volume: 100 + i * 10,
      time: i,
    }));
    const obv = computeOBV(candles);
    expect(obv).toHaveLength(candles.length);
  });

  it("computes volume SMA", () => {
    const volumes = Array.from({ length: 30 }, (_, i) => 100 + i);
    const sma = computeVolumeSMA(volumes, 20);
    expect(sma).toBeGreaterThan(0);
  });

  it("detects divergence", () => {
    // Bullish divergence: price lower low, RSI higher low
    const priceLows = [100, 98, 95]; // descending
    const rsiLows = [25, 28, 32]; // ascending
    const result = detectDivergence(priceLows, rsiLows);
    expect(result.type).toBe("bullish");
  });
});
```

**Step 2: Run test to verify it fails**

Run: `cd btc-sniper && npx vitest run tests/indicators.test.ts`
Expected: FAIL — module not found

**Step 3: Implement indicators**

```typescript
// src/engine/indicators.ts
export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface BollingerBand {
  upper: number;
  middle: number;
  lower: number;
}

export interface MACDResult {
  line: number[];
  signal: number[];
  histogram: number[];
}

export interface DivergenceResult {
  type: "bullish" | "bearish" | "none";
  strength: number; // 0-1
}

export function computeEMA(data: number[], period: number): number[] {
  if (data.length === 0) return [];
  const mult = 2 / (period + 1);
  const result = [data[0]];
  for (let i = 1; i < data.length; i++) {
    result.push(data[i] * mult + result[i - 1] * (1 - mult));
  }
  return result;
}

export function computeRSI(closes: number[], period: number): number[] {
  if (closes.length <= period) return [];
  const results: number[] = [];
  let avgGain = 0;
  let avgLoss = 0;

  // Initial average
  for (let i = 1; i <= period; i++) {
    const delta = closes[i] - closes[i - 1];
    if (delta > 0) avgGain += delta;
    else avgLoss += Math.abs(delta);
  }
  avgGain /= period;
  avgLoss /= period;
  results.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));

  // Smoothed
  for (let i = period + 1; i < closes.length; i++) {
    const delta = closes[i] - closes[i - 1];
    const gain = delta > 0 ? delta : 0;
    const loss = delta < 0 ? Math.abs(delta) : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    results.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
  }
  return results;
}

export function computeMACD(
  closes: number[],
  fast = 12,
  slow = 26,
  signal = 9
): MACDResult {
  const emaFast = computeEMA(closes, fast);
  const emaSlow = computeEMA(closes, slow);
  const line = emaFast.map((v, i) => v - emaSlow[i]);
  const signalLine = computeEMA(line, signal);
  const histogram = line.map((v, i) => v - signalLine[i]);
  return { line, signal: signalLine, histogram };
}

export function computeBollinger(
  closes: number[],
  period = 20,
  stdDev = 2
): BollingerBand[] {
  const results: BollingerBand[] = [];
  for (let i = period - 1; i < closes.length; i++) {
    const window = closes.slice(i - period + 1, i + 1);
    const sma = window.reduce((a, b) => a + b, 0) / period;
    const variance =
      window.reduce((sum, v) => sum + (v - sma) ** 2, 0) / period;
    const std = Math.sqrt(variance);
    results.push({
      upper: sma + stdDev * std,
      middle: sma,
      lower: sma - stdDev * std,
    });
  }
  return results;
}

export function computeATR(candles: Candle[], period = 14): number {
  if (candles.length < 2) return 0;
  const trs: number[] = [];
  for (let i = 1; i < candles.length; i++) {
    const tr = Math.max(
      candles[i].high - candles[i].low,
      Math.abs(candles[i].high - candles[i - 1].close),
      Math.abs(candles[i].low - candles[i - 1].close)
    );
    trs.push(tr);
  }
  if (trs.length < period) return trs.reduce((a, b) => a + b, 0) / trs.length;
  // Wilder's smoothing
  let atr = trs.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = period; i < trs.length; i++) {
    atr = (atr * (period - 1) + trs[i]) / period;
  }
  return atr;
}

export function computeOBV(candles: Candle[]): number[] {
  const obv = [0];
  for (let i = 1; i < candles.length; i++) {
    if (candles[i].close > candles[i - 1].close) {
      obv.push(obv[i - 1] + candles[i].volume);
    } else if (candles[i].close < candles[i - 1].close) {
      obv.push(obv[i - 1] - candles[i].volume);
    } else {
      obv.push(obv[i - 1]);
    }
  }
  return obv;
}

export function computeVolumeSMA(volumes: number[], period = 20): number {
  if (volumes.length < period) return volumes.reduce((a, b) => a + b, 0) / volumes.length;
  const recent = volumes.slice(-period);
  return recent.reduce((a, b) => a + b, 0) / period;
}

export function detectDivergence(
  priceLows: number[],
  rsiLows: number[]
): DivergenceResult {
  if (priceLows.length < 2 || rsiLows.length < 2) {
    return { type: "none", strength: 0 };
  }
  const lastPrice = priceLows[priceLows.length - 1];
  const prevPrice = priceLows[priceLows.length - 2];
  const lastRSI = rsiLows[rsiLows.length - 1];
  const prevRSI = rsiLows[rsiLows.length - 2];

  // Bullish: price lower low, RSI higher low
  if (lastPrice < prevPrice && lastRSI > prevRSI) {
    const strength = Math.min(1, Math.abs(lastRSI - prevRSI) / 20);
    return { type: "bullish", strength };
  }
  // Bearish: price higher high, RSI lower high
  if (lastPrice > prevPrice && lastRSI < prevRSI) {
    const strength = Math.min(1, Math.abs(lastRSI - prevRSI) / 20);
    return { type: "bearish", strength };
  }
  return { type: "none", strength: 0 };
}
```

**Step 4: Run tests**

Run: `cd btc-sniper && npx vitest run tests/indicators.test.ts`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/engine/indicators.ts tests/indicators.test.ts
git commit -m "feat: add technical indicators library with tests"
```

---

### Task 3: Market Engine (Binance WS + Multi-TF Candles)

**Files:**
- Create: `btc-sniper/src/engine/market-engine.ts`
- Create: `btc-sniper/tests/market-engine.test.ts`

**Step 1: Write the failing tests**

```typescript
// tests/market-engine.test.ts
import { describe, it, expect } from "vitest";
import { CandleBuffer, aggregateCandles } from "../src/engine/market-engine.js";

describe("CandleBuffer", () => {
  it("adds candles and respects max size", () => {
    const buf = new CandleBuffer(5);
    for (let i = 0; i < 10; i++) {
      buf.add({ time: i, open: 100, high: 101, low: 99, close: 100, volume: 10 });
    }
    expect(buf.getAll()).toHaveLength(5);
    expect(buf.getAll()[0].time).toBe(5); // oldest remaining
  });

  it("returns latest candle", () => {
    const buf = new CandleBuffer(10);
    buf.add({ time: 1, open: 100, high: 101, low: 99, close: 100, volume: 10 });
    buf.add({ time: 2, open: 101, high: 102, low: 100, close: 101, volume: 20 });
    expect(buf.latest()?.time).toBe(2);
  });

  it("returns closes array", () => {
    const buf = new CandleBuffer(10);
    buf.add({ time: 1, open: 100, high: 101, low: 99, close: 100, volume: 10 });
    buf.add({ time: 2, open: 101, high: 102, low: 100, close: 105, volume: 20 });
    expect(buf.closes()).toEqual([100, 105]);
  });
});

describe("aggregateCandles", () => {
  it("aggregates 1m candles into 5m", () => {
    const candles1m = Array.from({ length: 5 }, (_, i) => ({
      time: i * 60, open: 100 + i, high: 105 + i, low: 95 + i,
      close: 102 + i, volume: 10 + i,
    }));
    const candle5m = aggregateCandles(candles1m);
    expect(candle5m.open).toBe(100);  // first open
    expect(candle5m.close).toBe(106); // last close
    expect(candle5m.high).toBe(109);  // max high
    expect(candle5m.low).toBe(95);    // min low
    expect(candle5m.volume).toBe(60); // sum volumes
  });
});
```

**Step 2: Run test to verify it fails**

Run: `cd btc-sniper && npx vitest run tests/market-engine.test.ts`
Expected: FAIL

**Step 3: Implement market engine**

```typescript
// src/engine/market-engine.ts
import WebSocket from "ws";
import { EventEmitter } from "events";
import { CONFIG, type Timeframe } from "../config/default.js";
import type { Candle } from "./indicators.js";

export class CandleBuffer {
  private candles: Candle[] = [];
  private max: number;

  constructor(max = 500) {
    this.max = max;
  }

  add(candle: Candle): void {
    // Update existing or add new
    const existing = this.candles.findIndex((c) => c.time === candle.time);
    if (existing >= 0) {
      this.candles[existing] = candle;
    } else {
      this.candles.push(candle);
      if (this.candles.length > this.max) this.candles.shift();
    }
  }

  latest(): Candle | undefined {
    return this.candles[this.candles.length - 1];
  }

  getAll(): Candle[] {
    return [...this.candles];
  }

  closes(): number[] {
    return this.candles.map((c) => c.close);
  }

  length(): number {
    return this.candles.length;
  }
}

export function aggregateCandles(candles: Candle[]): Candle {
  return {
    time: candles[0].time,
    open: candles[0].open,
    high: Math.max(...candles.map((c) => c.high)),
    low: Math.min(...candles.map((c) => c.low)),
    close: candles[candles.length - 1].close,
    volume: candles.reduce((sum, c) => sum + c.volume, 0),
  };
}

export class MarketEngine extends EventEmitter {
  private ws: WebSocket | null = null;
  private buffers: Record<Timeframe, CandleBuffer> = {
    "1m": new CandleBuffer(CONFIG.candles.maxPerTimeframe),
    "5m": new CandleBuffer(CONFIG.candles.maxPerTimeframe),
    "15m": new CandleBuffer(CONFIG.candles.maxPerTimeframe),
  };
  private pending1m: Candle[] = [];
  private lastPrice = 0;

  get price(): number {
    return this.lastPrice;
  }

  getBuffer(tf: Timeframe): CandleBuffer {
    return this.buffers[tf];
  }

  async seedHistorical(): Promise<void> {
    try {
      const url = `${CONFIG.binance.restUrl}/api/v3/klines?symbol=${CONFIG.binance.symbol}&interval=1m&limit=500`;
      const res = await fetch(url);
      const data = (await res.json()) as any[];
      for (const k of data) {
        const candle: Candle = {
          time: Math.floor(k[0] / 1000),
          open: parseFloat(k[1]),
          high: parseFloat(k[2]),
          low: parseFloat(k[3]),
          close: parseFloat(k[4]),
          volume: parseFloat(k[5]),
        };
        this.buffers["1m"].add(candle);
        this.lastPrice = candle.close;
      }
      this.rebuildHigherTimeframes();
      console.log(`[MARKET] Seeded ${data.length} historical 1m candles`);
    } catch (e) {
      console.error("[MARKET] Failed to seed historical data:", e);
    }
  }

  connect(): void {
    this.ws = new WebSocket(CONFIG.binance.wsUrl);

    this.ws.on("message", (raw: Buffer) => {
      try {
        const msg = JSON.parse(raw.toString());
        if (!msg.k) return;
        const k = msg.k;
        const candle: Candle = {
          time: Math.floor(k.t / 1000),
          open: parseFloat(k.o),
          high: parseFloat(k.h),
          low: parseFloat(k.l),
          close: parseFloat(k.c),
          volume: parseFloat(k.v),
        };
        this.lastPrice = candle.close;
        this.buffers["1m"].add(candle);

        // If candle is closed, rebuild higher TFs and emit
        if (k.x) {
          this.rebuildHigherTimeframes();
          this.emit("candle", { timeframe: "1m" as Timeframe, candle });
        }

        this.emit("tick", candle.close);
      } catch {
        // ignore parse errors
      }
    });

    this.ws.on("open", () => console.log("[MARKET] Binance WS connected"));
    this.ws.on("close", () => {
      console.log("[MARKET] WS disconnected, reconnecting in 5s...");
      setTimeout(() => this.connect(), 5000);
    });
    this.ws.on("error", (err) => console.error("[MARKET] WS error:", err.message));
  }

  private rebuildHigherTimeframes(): void {
    const all1m = this.buffers["1m"].getAll();
    // Build 5m candles
    this.buildAggregated(all1m, 5, "5m");
    // Build 15m candles
    this.buildAggregated(all1m, 15, "15m");
  }

  private buildAggregated(candles1m: Candle[], minutes: number, tf: Timeframe): void {
    const interval = minutes * 60;
    const groups = new Map<number, Candle[]>();
    for (const c of candles1m) {
      const bucket = Math.floor(c.time / interval) * interval;
      if (!groups.has(bucket)) groups.set(bucket, []);
      groups.get(bucket)!.push(c);
    }
    const sorted = [...groups.entries()].sort((a, b) => a[0] - b[0]);
    this.buffers[tf] = new CandleBuffer(CONFIG.candles.maxPerTimeframe);
    for (const [bucketTime, group] of sorted) {
      const agg = aggregateCandles(group);
      agg.time = bucketTime;
      this.buffers[tf].add(agg);
    }
  }

  disconnect(): void {
    this.ws?.close();
  }
}
```

**Step 4: Run tests**

Run: `cd btc-sniper && npx vitest run tests/market-engine.test.ts`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/engine/market-engine.ts tests/market-engine.test.ts
git commit -m "feat: add market engine with Binance WS and multi-TF candle builder"
```

---

### Task 4: Event Detector

**Files:**
- Create: `btc-sniper/src/engine/event-detector.ts`
- Create: `btc-sniper/tests/event-detector.test.ts`

**Step 1: Write the failing tests**

```typescript
// tests/event-detector.test.ts
import { describe, it, expect } from "vitest";
import { EventDetector, type DetectedEvent } from "../src/engine/event-detector.js";

describe("EventDetector", () => {
  it("detects RSI extreme", () => {
    const detector = new EventDetector();
    const indicators = {
      "1m": { rsi: 22, ema12: 100, ema26: 101, macd_hist: -1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 200, volumeSma: 100, price: 96 },
      "5m": { rsi: 30, ema12: 100, ema26: 101, macd_hist: -1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 96 },
      "15m": { rsi: 45, ema12: 100, ema26: 99, macd_hist: 1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 100 },
    };
    const events = detector.detect(indicators);
    expect(events.some((e) => e.type === "RSI_EXTREME")).toBe(true);
  });

  it("respects cooldown", () => {
    const detector = new EventDetector();
    const indicators = {
      "1m": { rsi: 22, ema12: 100, ema26: 101, macd_hist: -1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 200, volumeSma: 100, price: 96 },
      "5m": { rsi: 30, ema12: 100, ema26: 101, macd_hist: -1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 96 },
      "15m": { rsi: 45, ema12: 100, ema26: 99, macd_hist: 1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 100 },
    };
    const events1 = detector.detect(indicators);
    expect(events1.length).toBeGreaterThan(0);
    // Second call immediately — should be on cooldown
    const events2 = detector.detect(indicators);
    expect(events2.filter((e) => e.type === "RSI_EXTREME")).toHaveLength(0);
  });

  it("detects volume spike", () => {
    const detector = new EventDetector();
    const indicators = {
      "1m": { rsi: 50, ema12: 100, ema26: 99, macd_hist: 1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 400, volumeSma: 100, price: 100 },
      "5m": { rsi: 50, ema12: 100, ema26: 99, macd_hist: 1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 100 },
      "15m": { rsi: 50, ema12: 100, ema26: 99, macd_hist: 1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 100 },
    };
    const events = detector.detect(indicators);
    expect(events.some((e) => e.type === "VOLUME_SPIKE")).toBe(true);
  });

  it("calculates confluence score", () => {
    const detector = new EventDetector();
    // RSI extreme + volume spike together
    const indicators = {
      "1m": { rsi: 22, ema12: 100, ema26: 101, macd_hist: -1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 400, volumeSma: 100, price: 94 },
      "5m": { rsi: 30, ema12: 100, ema26: 101, macd_hist: -1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 94 },
      "15m": { rsi: 45, ema12: 100, ema26: 99, macd_hist: 1, bb: { upper: 105, middle: 100, lower: 95 }, volume: 100, volumeSma: 100, price: 100 },
    };
    const events = detector.detect(indicators);
    expect(events.length).toBeGreaterThanOrEqual(2);
  });
});
```

**Step 2: Run test to verify it fails**

Run: `cd btc-sniper && npx vitest run tests/event-detector.test.ts`
Expected: FAIL

**Step 3: Implement event detector**

```typescript
// src/engine/event-detector.ts
import { CONFIG, type EventType } from "../config/default.js";

export interface TFIndicators {
  rsi: number;
  ema12: number;
  ema26: number;
  macd_hist: number;
  bb: { upper: number; middle: number; lower: number };
  volume: number;
  volumeSma: number;
  price: number;
}

export type MultiTFIndicators = Record<"1m" | "5m" | "15m", TFIndicators>;

export interface DetectedEvent {
  type: EventType;
  direction: "bullish" | "bearish" | "neutral";
  strength: number; // 0-1
  detail: string;
  timestamp: number;
}

export class EventDetector {
  private lastFired: Map<EventType, number> = new Map();
  private prevEma12_5m: number | null = null;
  private prevEma26_5m: number | null = null;

  detect(indicators: MultiTFIndicators): DetectedEvent[] {
    const now = Date.now();
    const events: DetectedEvent[] = [];
    const m1 = indicators["1m"];
    const m5 = indicators["5m"];

    // RSI Extreme
    if (this.canFire("RSI_EXTREME", now)) {
      const cfg = CONFIG.events.RSI_EXTREME;
      if (m1.rsi < cfg.threshold_low && m5.rsi < 40) {
        events.push({ type: "RSI_EXTREME", direction: "bullish", strength: (cfg.threshold_low - m1.rsi) / cfg.threshold_low, detail: `RSI 1m=${m1.rsi.toFixed(1)} 5m=${m5.rsi.toFixed(1)} — oversold`, timestamp: now });
        this.lastFired.set("RSI_EXTREME", now);
      } else if (m1.rsi > cfg.threshold_high && m5.rsi > 60) {
        events.push({ type: "RSI_EXTREME", direction: "bearish", strength: (m1.rsi - cfg.threshold_high) / (100 - cfg.threshold_high), detail: `RSI 1m=${m1.rsi.toFixed(1)} 5m=${m5.rsi.toFixed(1)} — overbought`, timestamp: now });
        this.lastFired.set("RSI_EXTREME", now);
      }
    }

    // EMA Cross on 5m
    if (this.canFire("EMA_CROSS", now) && this.prevEma12_5m !== null) {
      const wasBullish = this.prevEma12_5m > this.prevEma26_5m!;
      const isBullish = m5.ema12 > m5.ema26;
      if (wasBullish !== isBullish) {
        events.push({ type: "EMA_CROSS", direction: isBullish ? "bullish" : "bearish", strength: 0.7, detail: `EMA12 ${isBullish ? "crossed above" : "crossed below"} EMA26 on 5m`, timestamp: now });
        this.lastFired.set("EMA_CROSS", now);
      }
    }
    this.prevEma12_5m = m5.ema12;
    this.prevEma26_5m = m5.ema26;

    // Volume Spike
    if (this.canFire("VOLUME_SPIKE", now)) {
      const ratio = m1.volume / Math.max(m1.volumeSma, 1);
      if (ratio >= CONFIG.events.VOLUME_SPIKE.multiplier) {
        const direction = m1.price > m1.ema12 ? "bullish" : "bearish";
        events.push({ type: "VOLUME_SPIKE", direction, strength: Math.min(1, ratio / 5), detail: `Volume ${ratio.toFixed(1)}x average`, timestamp: now });
        this.lastFired.set("VOLUME_SPIKE", now);
      }
    }

    // BB Breakout (price was outside, came back in)
    if (this.canFire("BB_BREAKOUT", now)) {
      if (m1.price <= m1.bb.lower) {
        events.push({ type: "BB_BREAKOUT", direction: "bullish", strength: 0.6, detail: `Price at lower Bollinger Band`, timestamp: now });
        this.lastFired.set("BB_BREAKOUT", now);
      } else if (m1.price >= m1.bb.upper) {
        events.push({ type: "BB_BREAKOUT", direction: "bearish", strength: 0.6, detail: `Price at upper Bollinger Band`, timestamp: now });
        this.lastFired.set("BB_BREAKOUT", now);
      }
    }

    return events;
  }

  // External data events (called separately from API polling)
  detectFundingFlip(prevRate: number, currentRate: number): DetectedEvent | null {
    const now = Date.now();
    if (!this.canFire("FUNDING_FLIP", now)) return null;
    if ((prevRate >= 0 && currentRate < 0) || (prevRate < 0 && currentRate >= 0)) {
      this.lastFired.set("FUNDING_FLIP", now);
      return {
        type: "FUNDING_FLIP",
        direction: currentRate < 0 ? "bullish" : "bearish",
        strength: 0.5,
        detail: `Funding flipped from ${(prevRate * 100).toFixed(4)}% to ${(currentRate * 100).toFixed(4)}%`,
        timestamp: now,
      };
    }
    return null;
  }

  detectLiquidationWave(longLiqs: number, shortLiqs: number): DetectedEvent | null {
    const now = Date.now();
    if (!this.canFire("LIQUIDATION_WAVE", now)) return null;
    const threshold = CONFIG.events.LIQUIDATION_WAVE.threshold_usd;
    if (longLiqs > threshold) {
      this.lastFired.set("LIQUIDATION_WAVE", now);
      return { type: "LIQUIDATION_WAVE", direction: "bullish", strength: Math.min(1, longLiqs / (threshold * 3)), detail: `Long liquidations $${(longLiqs / 1e6).toFixed(1)}M — capitulation`, timestamp: now };
    }
    if (shortLiqs > threshold) {
      this.lastFired.set("LIQUIDATION_WAVE", now);
      return { type: "LIQUIDATION_WAVE", direction: "bearish", strength: Math.min(1, shortLiqs / (threshold * 3)), detail: `Short liquidations $${(shortLiqs / 1e6).toFixed(1)}M — short squeeze ending`, timestamp: now };
    }
    return null;
  }

  private canFire(type: EventType, now: number): boolean {
    const last = this.lastFired.get(type) ?? 0;
    const cooldown = CONFIG.events[type].cooldown_ms;
    return now - last >= cooldown;
  }
}
```

**Step 4: Run tests**

Run: `cd btc-sniper && npx vitest run tests/event-detector.test.ts`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/engine/event-detector.ts tests/event-detector.test.ts
git commit -m "feat: add event detector with cooldowns and confluence"
```

---

### Task 5: Data Agents (Technical, Sentiment, On-Chain, Order Flow)

**Files:**
- Create: `btc-sniper/src/agents/technical.ts`
- Create: `btc-sniper/src/agents/sentiment.ts`
- Create: `btc-sniper/src/agents/onchain.ts`
- Create: `btc-sniper/src/agents/orderflow.ts`
- Create: `btc-sniper/tests/agents.test.ts`

**Step 1: Write the failing test**

```typescript
// tests/agents.test.ts
import { describe, it, expect } from "vitest";
import { TechnicalAgent } from "../src/agents/technical.js";
import type { CandleBuffer } from "../src/engine/market-engine.js";
import type { MultiTFIndicators } from "../src/engine/event-detector.js";

describe("TechnicalAgent", () => {
  it("computes indicators from candle buffers", () => {
    const agent = new TechnicalAgent();
    // Create mock buffers with enough data
    const closes = Array.from({ length: 50 }, (_, i) => 100 + Math.sin(i / 5) * 5);
    const candles = closes.map((c, i) => ({
      time: i * 60, open: c - 0.5, high: c + 1, low: c - 1, close: c, volume: 100 + i,
    }));

    const result = agent.analyze(candles);
    expect(result.rsi).toBeGreaterThan(0);
    expect(result.rsi).toBeLessThan(100);
    expect(result.ema12).toBeGreaterThan(0);
    expect(result.ema26).toBeGreaterThan(0);
    expect(result.bb.upper).toBeGreaterThan(result.bb.lower);
    expect(result.volumeSma).toBeGreaterThan(0);
  });
});
```

**Step 2: Run test to verify fails**

Run: `cd btc-sniper && npx vitest run tests/agents.test.ts`
Expected: FAIL

**Step 3: Implement agents**

```typescript
// src/agents/technical.ts
import type { Candle } from "../engine/indicators.js";
import type { TFIndicators } from "../engine/event-detector.js";
import {
  computeRSI, computeEMA, computeMACD, computeBollinger,
  computeATR, computeVolumeSMA,
} from "../engine/indicators.js";

export class TechnicalAgent {
  analyze(candles: Candle[]): TFIndicators {
    const closes = candles.map((c) => c.close);
    const volumes = candles.map((c) => c.volume);
    const price = closes[closes.length - 1] ?? 0;

    const rsiArr = computeRSI(closes, 14);
    const rsi = rsiArr.length > 0 ? rsiArr[rsiArr.length - 1] : 50;
    const ema12Arr = computeEMA(closes, 12);
    const ema12 = ema12Arr[ema12Arr.length - 1] ?? price;
    const ema26Arr = computeEMA(closes, 26);
    const ema26 = ema26Arr[ema26Arr.length - 1] ?? price;
    const macd = computeMACD(closes);
    const macdHist = macd.histogram[macd.histogram.length - 1] ?? 0;
    const bbArr = computeBollinger(closes, 20, 2);
    const bb = bbArr.length > 0
      ? bbArr[bbArr.length - 1]
      : { upper: price + 1, middle: price, lower: price - 1 };
    const volumeSma = computeVolumeSMA(volumes, 20);
    const currentVol = volumes[volumes.length - 1] ?? 0;

    return {
      rsi, ema12, ema26, macd_hist: macdHist,
      bb, volume: currentVol, volumeSma, price,
    };
  }

  computeATR(candles: Candle[]): number {
    return computeATR(candles, 14);
  }
}
```

```typescript
// src/agents/sentiment.ts
export interface SentimentData {
  fear_greed: number;       // 0-100
  fear_greed_label: string; // "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"
}

export class SentimentAgent {
  private lastValue: SentimentData = { fear_greed: 50, fear_greed_label: "Neutral" };
  private lastFetch = 0;

  async fetch(): Promise<SentimentData> {
    // Rate limit: once per 5 minutes
    if (Date.now() - this.lastFetch < 5 * 60_000) return this.lastValue;
    try {
      const res = await fetch("https://api.alternative.me/fng/?limit=1");
      const data = (await res.json()) as any;
      if (data?.data?.[0]) {
        const val = parseInt(data.data[0].value, 10);
        this.lastValue = {
          fear_greed: val,
          fear_greed_label: data.data[0].value_classification,
        };
      }
      this.lastFetch = Date.now();
    } catch {
      // keep last value
    }
    return this.lastValue;
  }

  get data(): SentimentData {
    return this.lastValue;
  }
}
```

```typescript
// src/agents/onchain.ts
export interface OnChainData {
  funding_rate: number;
  long_short_ratio: number;
  open_interest: number;
}

export class OnChainAgent {
  private lastData: OnChainData = { funding_rate: 0.0001, long_short_ratio: 1.0, open_interest: 0 };
  private lastFetch = 0;
  private prevFundingRate = 0.0001;

  async fetch(): Promise<OnChainData> {
    if (Date.now() - this.lastFetch < 30_000) return this.lastData;
    this.prevFundingRate = this.lastData.funding_rate;
    try {
      const [frRes, lsRes, oiRes] = await Promise.allSettled([
        fetch("https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1"),
        fetch("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=5m&limit=1"),
        fetch("https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT"),
      ]);
      if (frRes.status === "fulfilled" && frRes.value.ok) {
        const d = (await frRes.value.json()) as any[];
        if (d?.[0]) this.lastData.funding_rate = parseFloat(d[0].fundingRate);
      }
      if (lsRes.status === "fulfilled" && lsRes.value.ok) {
        const d = (await lsRes.value.json()) as any[];
        if (d?.[0]) this.lastData.long_short_ratio = parseFloat(d[0].longShortRatio);
      }
      if (oiRes.status === "fulfilled" && oiRes.value.ok) {
        const d = (await oiRes.value.json()) as any;
        if (d) this.lastData.open_interest = parseFloat(d.openInterest);
      }
      this.lastFetch = Date.now();
    } catch {
      // keep last
    }
    return this.lastData;
  }

  get data(): OnChainData { return this.lastData; }
  get previousFundingRate(): number { return this.prevFundingRate; }
}
```

```typescript
// src/agents/orderflow.ts
export interface OrderFlowData {
  ob_ratio: number;       // 0-1 (bid dominance)
  best_bid: number;
  best_ask: number;
  spread: number;
  bids: [number, number][]; // [price, qty][]
  asks: [number, number][];
}

export class OrderFlowAgent {
  private lastData: OrderFlowData = {
    ob_ratio: 0.5, best_bid: 0, best_ask: 0, spread: 0, bids: [], asks: [],
  };
  private lastFetch = 0;

  async fetch(): Promise<OrderFlowData> {
    if (Date.now() - this.lastFetch < 5_000) return this.lastData;
    try {
      const res = await fetch(
        "https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=20"
      );
      const data = (await res.json()) as any;
      const bids: [number, number][] = (data.bids || []).map((b: string[]) => [
        parseFloat(b[0]), parseFloat(b[1]),
      ]);
      const asks: [number, number][] = (data.asks || []).map((a: string[]) => [
        parseFloat(a[0]), parseFloat(a[1]),
      ]);
      const bidVol = bids.reduce((s, b) => s + b[1], 0);
      const askVol = asks.reduce((s, a) => s + a[1], 0);
      const total = bidVol + askVol;

      this.lastData = {
        ob_ratio: total > 0 ? bidVol / total : 0.5,
        best_bid: bids[0]?.[0] ?? 0,
        best_ask: asks[0]?.[0] ?? 0,
        spread: (asks[0]?.[0] ?? 0) - (bids[0]?.[0] ?? 0),
        bids, asks,
      };
      this.lastFetch = Date.now();
    } catch {
      // keep last
    }
    return this.lastData;
  }

  get data(): OrderFlowData { return this.lastData; }
}
```

**Step 4: Run tests**

Run: `cd btc-sniper && npx vitest run tests/agents.test.ts`
Expected: PASS

**Step 5: Commit**

```bash
git add src/agents/ tests/agents.test.ts
git commit -m "feat: add technical, sentiment, on-chain, and orderflow agents"
```

---

### Task 6: Pattern Memory + Trade Journal

**Files:**
- Create: `btc-sniper/src/learning/pattern-memory.ts`
- Create: `btc-sniper/src/learning/trade-journal.ts`
- Create: `btc-sniper/tests/pattern-memory.test.ts`

**Step 1: Write the failing tests**

```typescript
// tests/pattern-memory.test.ts
import { describe, it, expect } from "vitest";
import { PatternMemory, type SetupRecord } from "../src/learning/pattern-memory.js";

describe("PatternMemory", () => {
  it("stores and retrieves setups", () => {
    const mem = new PatternMemory();
    mem.record({
      events: ["RSI_EXTREME", "VOLUME_SPIKE"],
      indicators_1m: { rsi: 22 },
      indicators_5m: { rsi: 35 },
      direction: "LONG",
      conviction: 0.8,
    });
    expect(mem.totalSetups()).toBe(1);
  });

  it("finds similar setups", () => {
    const mem = new PatternMemory();
    // Add 3 setups
    mem.record({ events: ["RSI_EXTREME"], indicators_1m: { rsi: 22 }, indicators_5m: { rsi: 30 }, direction: "LONG", conviction: 0.7 });
    mem.record({ events: ["RSI_EXTREME", "VOLUME_SPIKE"], indicators_1m: { rsi: 24 }, indicators_5m: { rsi: 32 }, direction: "LONG", conviction: 0.8 });
    mem.record({ events: ["EMA_CROSS"], indicators_1m: { rsi: 55 }, indicators_5m: { rsi: 60 }, direction: "SHORT", conviction: 0.6 });

    // Close first two as wins
    mem.recordOutcome(0, { won: true, pnl_pct: 0.5, duration_s: 200, exit_reason: "trailing_stop" });
    mem.recordOutcome(1, { won: true, pnl_pct: 0.8, duration_s: 300, exit_reason: "trailing_stop" });

    // Find similar to RSI_EXTREME with rsi~23
    const similar = mem.findSimilar(["RSI_EXTREME"], { rsi: 23 }, 5);
    expect(similar.length).toBe(2);
    expect(similar[0].events).toContain("RSI_EXTREME");
  });

  it("computes win rate for event type", () => {
    const mem = new PatternMemory();
    mem.record({ events: ["RSI_EXTREME"], indicators_1m: { rsi: 22 }, indicators_5m: {}, direction: "LONG", conviction: 0.8 });
    mem.record({ events: ["RSI_EXTREME"], indicators_1m: { rsi: 24 }, indicators_5m: {}, direction: "LONG", conviction: 0.7 });
    mem.record({ events: ["RSI_EXTREME"], indicators_1m: { rsi: 20 }, indicators_5m: {}, direction: "LONG", conviction: 0.9 });
    mem.recordOutcome(0, { won: true, pnl_pct: 1.0, duration_s: 100, exit_reason: "trailing_stop" });
    mem.recordOutcome(1, { won: false, pnl_pct: -0.5, duration_s: 60, exit_reason: "trailing_stop" });
    mem.recordOutcome(2, { won: true, pnl_pct: 0.8, duration_s: 150, exit_reason: "trailing_stop" });

    const stats = mem.eventStats("RSI_EXTREME");
    expect(stats.total).toBe(3);
    expect(stats.wins).toBe(2);
    expect(stats.winRate).toBeCloseTo(0.667, 1);
  });
});
```

**Step 2: Run test to verify fails**

Run: `cd btc-sniper && npx vitest run tests/pattern-memory.test.ts`
Expected: FAIL

**Step 3: Implement pattern memory and trade journal**

```typescript
// src/learning/pattern-memory.ts
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { dirname } from "path";
import { CONFIG, type EventType } from "../config/default.js";

export interface SetupOutcome {
  won: boolean;
  pnl_pct: number;
  duration_s: number;
  exit_reason: string;
}

export interface SetupRecord {
  id: number;
  timestamp: number;
  events: EventType[];
  indicators_1m: Record<string, number>;
  indicators_5m: Record<string, number>;
  direction: string;
  conviction: number;
  outcome?: SetupOutcome;
}

export class PatternMemory {
  private setups: SetupRecord[] = [];
  private nextId = 0;

  constructor() {
    this.load();
  }

  record(data: Omit<SetupRecord, "id" | "timestamp">): number {
    const id = this.nextId++;
    this.setups.push({ ...data, id, timestamp: Date.now() });
    this.save();
    return id;
  }

  recordOutcome(id: number, outcome: SetupOutcome): void {
    const setup = this.setups.find((s) => s.id === id);
    if (setup) {
      setup.outcome = outcome;
      this.save();
    }
  }

  findSimilar(
    events: EventType[],
    indicators1m: Record<string, number>,
    limit = 5
  ): SetupRecord[] {
    // Score similarity: shared events + close RSI values
    const completed = this.setups.filter((s) => s.outcome);
    const scored = completed.map((s) => {
      let score = 0;
      // Event overlap
      const overlap = s.events.filter((e) => events.includes(e)).length;
      score += overlap * 10;
      // RSI proximity (if both have rsi)
      const sRsi = s.indicators_1m.rsi;
      const qRsi = indicators1m.rsi;
      if (sRsi !== undefined && qRsi !== undefined) {
        score += Math.max(0, 10 - Math.abs(sRsi - qRsi));
      }
      return { setup: s, score };
    });
    return scored
      .sort((a, b) => b.score - a.score)
      .slice(0, limit)
      .filter((s) => s.score > 0)
      .map((s) => s.setup);
  }

  eventStats(type: EventType): { total: number; wins: number; winRate: number; avgPnl: number } {
    const matching = this.setups.filter(
      (s) => s.events.includes(type) && s.outcome
    );
    const wins = matching.filter((s) => s.outcome!.won);
    const totalPnl = matching.reduce((sum, s) => sum + s.outcome!.pnl_pct, 0);
    return {
      total: matching.length,
      wins: wins.length,
      winRate: matching.length > 0 ? wins.length / matching.length : 0,
      avgPnl: matching.length > 0 ? totalPnl / matching.length : 0,
    };
  }

  allEventStats(): Record<string, { total: number; wins: number; winRate: number; avgPnl: number }> {
    const types: EventType[] = ["RSI_EXTREME", "EMA_CROSS", "DIVERGENCE", "VOLUME_SPIKE", "BB_BREAKOUT", "FUNDING_FLIP", "LIQUIDATION_WAVE"];
    const result: Record<string, ReturnType<typeof this.eventStats>> = {};
    for (const t of types) {
      const stats = this.eventStats(t);
      if (stats.total > 0) result[t] = stats;
    }
    return result;
  }

  totalSetups(): number {
    return this.setups.length;
  }

  getAll(): SetupRecord[] {
    return [...this.setups];
  }

  private load(): void {
    try {
      if (existsSync(CONFIG.data.patterns_file)) {
        const raw = readFileSync(CONFIG.data.patterns_file, "utf-8");
        const data = JSON.parse(raw);
        this.setups = data.setups || [];
        this.nextId = data.nextId || this.setups.length;
      }
    } catch { /* start fresh */ }
  }

  private save(): void {
    try {
      const dir = dirname(CONFIG.data.patterns_file);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      writeFileSync(
        CONFIG.data.patterns_file,
        JSON.stringify({ setups: this.setups, nextId: this.nextId }, null, 2)
      );
    } catch { /* ignore */ }
  }
}
```

```typescript
// src/learning/trade-journal.ts
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { dirname } from "path";
import { CONFIG } from "../config/default.js";

export interface TradeRecord {
  id: number;
  timestamp: number;
  direction: string;
  entry_price: number;
  exit_price?: number;
  stake: number;
  leverage: number;
  score: number;
  conviction: number;
  events: string[];
  reasoning: string;
  pnl?: number;
  won?: boolean;
  close_reason?: string;
  duration_s?: number;
  status: "OPEN" | "CLOSED";
  bankroll_after?: number;
  pattern_id?: number;
}

export class TradeJournal {
  private trades: TradeRecord[] = [];
  private nextId = 1;

  constructor() {
    this.load();
  }

  recordOpen(data: Omit<TradeRecord, "id" | "status">): number {
    const id = this.nextId++;
    this.trades.push({ ...data, id, status: "OPEN" });
    this.save();
    return id;
  }

  recordClose(id: number, exit_price: number, pnl: number, close_reason: string, duration_s: number, bankroll_after: number): void {
    const trade = this.trades.find((t) => t.id === id);
    if (trade) {
      trade.exit_price = exit_price;
      trade.pnl = pnl;
      trade.won = pnl > 0;
      trade.close_reason = close_reason;
      trade.duration_s = duration_s;
      trade.status = "CLOSED";
      trade.bankroll_after = bankroll_after;
      this.save();
    }
  }

  getOpen(): TradeRecord | undefined {
    return this.trades.find((t) => t.status === "OPEN");
  }

  getRecent(limit = 30): TradeRecord[] {
    return [...this.trades].reverse().slice(0, limit);
  }

  getClosed(): TradeRecord[] {
    return this.trades.filter((t) => t.status === "CLOSED");
  }

  stats(): { total: number; wins: number; losses: number; winRate: number; totalPnl: number; avgWin: number; avgLoss: number } {
    const closed = this.getClosed();
    const wins = closed.filter((t) => t.won);
    const losses = closed.filter((t) => !t.won);
    const totalPnl = closed.reduce((s, t) => s + (t.pnl ?? 0), 0);
    return {
      total: closed.length,
      wins: wins.length,
      losses: losses.length,
      winRate: closed.length > 0 ? wins.length / closed.length : 0,
      totalPnl: Math.round(totalPnl * 100) / 100,
      avgWin: wins.length > 0 ? wins.reduce((s, t) => s + (t.pnl ?? 0), 0) / wins.length : 0,
      avgLoss: losses.length > 0 ? losses.reduce((s, t) => s + (t.pnl ?? 0), 0) / losses.length : 0,
    };
  }

  private load(): void {
    try {
      if (existsSync(CONFIG.data.trades_file)) {
        const raw = readFileSync(CONFIG.data.trades_file, "utf-8");
        const data = JSON.parse(raw);
        this.trades = data.trades || [];
        this.nextId = data.nextId || this.trades.length + 1;
      }
    } catch { /* start fresh */ }
  }

  private save(): void {
    try {
      const dir = dirname(CONFIG.data.trades_file);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      writeFileSync(
        CONFIG.data.trades_file,
        JSON.stringify({ trades: this.trades, nextId: this.nextId }, null, 2)
      );
    } catch { /* ignore */ }
  }
}
```

**Step 4: Run tests**

Run: `cd btc-sniper && npx vitest run tests/pattern-memory.test.ts`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/learning/ tests/pattern-memory.test.ts
git commit -m "feat: add pattern memory and trade journal with persistence"
```

---

### Task 7: Position Manager + Risk Manager

**Files:**
- Create: `btc-sniper/src/trading/position-manager.ts`
- Create: `btc-sniper/src/trading/risk-manager.ts`
- Create: `btc-sniper/tests/trading.test.ts`

**Step 1: Write the failing tests**

```typescript
// tests/trading.test.ts
import { describe, it, expect } from "vitest";
import { PositionManager } from "../src/trading/position-manager.js";
import { RiskManager } from "../src/trading/risk-manager.js";

describe("PositionManager", () => {
  it("opens and tracks position", () => {
    const pm = new PositionManager();
    pm.open("LONG", 68000, 10, 150, 0.8);
    expect(pm.hasPosition).toBe(true);
    expect(pm.position!.direction).toBe("LONG");
    expect(pm.position!.stopLoss).toBeCloseTo(68000 - 150 * 0.8);
  });

  it("updates trailing stop on new high (LONG)", () => {
    const pm = new PositionManager();
    pm.open("LONG", 68000, 10, 150, 0.8);
    const r1 = pm.tick(68200); // new high
    expect(r1.action).toBe("HOLD");
    expect(pm.position!.stopLoss).toBeCloseTo(68200 - 120);
    const r2 = pm.tick(68100); // drops but above stop
    expect(r2.action).toBe("HOLD");
    expect(pm.position!.stopLoss).toBeCloseTo(68200 - 120); // stop didn't move down
  });

  it("closes on trailing stop hit", () => {
    const pm = new PositionManager();
    pm.open("LONG", 68000, 10, 150, 0.8);
    const result = pm.tick(67800); // below stop
    expect(result.action).toBe("CLOSE");
    expect(result.reason).toBe("trailing_stop");
  });

  it("calculates unrealized PnL", () => {
    const pm = new PositionManager();
    pm.open("LONG", 68000, 10, 150, 0.8, 20);
    const pnl = pm.unrealizedPnl(68200);
    // (68200-68000)/68000 * 10 * 20 = 0.588
    expect(pnl).toBeCloseTo(0.588, 1);
  });
});

describe("RiskManager", () => {
  it("computes Kelly stake", () => {
    const rm = new RiskManager(100);
    const stake = rm.computeStake(0.8, 0.25);
    expect(stake).toBeGreaterThan(0);
    expect(stake).toBeLessThanOrEqual(25); // max 25% of 100
  });

  it("blocks trade after 5 consecutive losses", () => {
    const rm = new RiskManager(100);
    for (let i = 0; i < 5; i++) rm.recordLoss();
    expect(rm.canTrade()).toBe(false);
  });

  it("blocks trade after max drawdown", () => {
    const rm = new RiskManager(100);
    rm.updateBankroll(84); // 16% drawdown > 15% limit
    expect(rm.canTrade()).toBe(false);
  });

  it("computes leverage from conviction and ATR", () => {
    const rm = new RiskManager(100);
    const lev = rm.computeLeverage(0.8, 0.2);
    expect(lev).toBeGreaterThanOrEqual(5);
    expect(lev).toBeLessThanOrEqual(50);
  });
});
```

**Step 2: Run test to verify fails**

Run: `cd btc-sniper && npx vitest run tests/trading.test.ts`
Expected: FAIL

**Step 3: Implement**

```typescript
// src/trading/position-manager.ts
export interface Position {
  direction: "LONG" | "SHORT";
  entryPrice: number;
  stake: number;
  atr: number;
  stopMultiplier: number;
  stopDistance: number;
  stopLoss: number;
  highestPrice: number;
  lowestPrice: number;
  leverage: number;
  openTime: number;
}

export interface TickResult {
  action: "HOLD" | "CLOSE";
  reason?: string;
  pnl?: number;
}

export class PositionManager {
  position: Position | null = null;

  get hasPosition(): boolean {
    return this.position !== null;
  }

  open(direction: "LONG" | "SHORT", price: number, stake: number, atr: number, stopMultiplier = 0.8, leverage = 20): void {
    const stopDistance = atr * stopMultiplier;
    this.position = {
      direction, entryPrice: price, stake, atr, stopMultiplier, stopDistance, leverage,
      stopLoss: direction === "LONG" ? price - stopDistance : price + stopDistance,
      highestPrice: direction === "LONG" ? price : 0,
      lowestPrice: direction === "SHORT" ? price : Infinity,
      openTime: Date.now(),
    };
  }

  tick(price: number): TickResult {
    if (!this.position) return { action: "HOLD" };
    const pos = this.position;

    if (pos.direction === "LONG") {
      if (price > pos.highestPrice) {
        pos.highestPrice = price;
        pos.stopLoss = Math.max(pos.stopLoss, price - pos.stopDistance);
      }
      if (price <= pos.stopLoss) {
        return { action: "CLOSE", reason: "trailing_stop", pnl: this.unrealizedPnl(price) };
      }
    } else {
      if (price < pos.lowestPrice) {
        pos.lowestPrice = price;
        pos.stopLoss = Math.min(pos.stopLoss, price + pos.stopDistance);
      }
      if (price >= pos.stopLoss) {
        return { action: "CLOSE", reason: "trailing_stop", pnl: this.unrealizedPnl(price) };
      }
    }
    return { action: "HOLD", pnl: this.unrealizedPnl(price) };
  }

  unrealizedPnl(price: number): number {
    if (!this.position) return 0;
    const { direction, entryPrice, stake, leverage } = this.position;
    const pct = direction === "LONG"
      ? (price - entryPrice) / entryPrice
      : (entryPrice - price) / entryPrice;
    return Math.round(stake * pct * leverage * 10000) / 10000;
  }

  close(price: number, reason = "manual"): { pnl: number; duration_s: number; direction: string; entryPrice: number; exitPrice: number; reason: string } {
    if (!this.position) throw new Error("No position to close");
    const pnl = this.unrealizedPnl(price);
    const result = {
      pnl,
      duration_s: Math.round((Date.now() - this.position.openTime) / 1000),
      direction: this.position.direction,
      entryPrice: this.position.entryPrice,
      exitPrice: price,
      reason,
    };
    this.position = null;
    return result;
  }

  getState(): Position | null {
    return this.position ? { ...this.position } : null;
  }
}
```

```typescript
// src/trading/risk-manager.ts
import { CONFIG } from "../config/default.js";

export class RiskManager {
  bankroll: number;
  initialBankroll: number;
  consecutiveLosses = 0;
  winStreak = 0;
  pauseUntil = 0;

  constructor(bankroll: number) {
    this.bankroll = bankroll;
    this.initialBankroll = bankroll;
  }

  canTrade(): boolean {
    if (Date.now() < this.pauseUntil) return false;
    if (this.consecutiveLosses >= CONFIG.circuit.max_consecutive_losses) {
      this.pauseUntil = Date.now() + CONFIG.circuit.pause_after_losses_ms;
      return false;
    }
    const drawdown = (this.initialBankroll - this.bankroll) / this.initialBankroll;
    if (drawdown >= CONFIG.circuit.max_drawdown_pct) {
      this.pauseUntil = Date.now() + CONFIG.circuit.pause_after_drawdown_ms;
      return false;
    }
    return true;
  }

  computeStake(conviction: number, maxStakePct: number): number {
    // Half-Kelly: f = (p * b - q) / b, then halved
    const p = 0.5 + conviction * 0.3; // estimated win prob from conviction
    const b = 1.5; // estimated payoff ratio
    const q = 1 - p;
    const kelly = Math.max(0, (p * b - q) / b) / 2;
    const stake = this.bankroll * Math.min(kelly, maxStakePct);
    return Math.round(stake * 100) / 100;
  }

  computeLeverage(conviction: number, atrPct: number): number {
    // Higher conviction + lower volatility = more leverage
    const base = CONFIG.trading.leverage_min + conviction * (CONFIG.trading.leverage_max - CONFIG.trading.leverage_min);
    // Scale down for high volatility
    const volFactor = Math.max(0.3, 1 - atrPct * 2);
    const lev = Math.round(base * volFactor);
    return Math.max(CONFIG.trading.leverage_min, Math.min(CONFIG.trading.leverage_max, lev));
  }

  recordWin(): void {
    this.consecutiveLosses = 0;
    this.winStreak++;
  }

  recordLoss(): void {
    this.consecutiveLosses++;
    this.winStreak = 0;
  }

  updateBankroll(newBankroll: number): void {
    this.bankroll = newBankroll;
  }
}
```

**Step 4: Run tests**

Run: `cd btc-sniper && npx vitest run tests/trading.test.ts`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/trading/ tests/trading.test.ts
git commit -m "feat: add position manager with trailing stop and risk manager"
```

---

### Task 8: Claude Brain

**Files:**
- Create: `btc-sniper/src/brain/claude-brain.ts`

**Step 1: Implement Claude brain**

```typescript
// src/brain/claude-brain.ts
import Anthropic from "@anthropic-ai/sdk";
import { CONFIG, type EventType } from "../config/default.js";
import type { DetectedEvent, MultiTFIndicators } from "../engine/event-detector.js";
import type { SetupRecord } from "../learning/pattern-memory.js";

const SYSTEM_PROMPT = `You are an elite BTC futures trader. You receive market events and multi-timeframe context.
You ONLY trade when the setup is high quality. You are a sniper — patient, precise, deadly.

RESPOND ONLY WITH JSON (no markdown, no extra text):
{
  "action": "TRADE" | "SKIP",
  "direction": "LONG" | "SHORT",
  "conviction": <float 0.0-1.0>,
  "reasoning": "<2-3 sentences explaining your decision>",
  "suggested_stop_atr_mult": <float 0.5-2.0 or null>
}

Rules:
- SKIP if signals conflict or setup is weak
- conviction > 0.8 only when 3+ signals align perfectly
- conviction 0.6-0.8 for solid setups with 2+ confirming signals
- conviction < 0.6 = usually SKIP
- Always consider the pattern memory — if similar setups lost money, be cautious
- Funding rate is CONTRARIAN: positive funding = crowded longs = bearish pressure
- Fear & Greed is CONTRARIAN: extreme fear = buying opportunity
- Multi-timeframe confluence is key: 1m signal confirmed on 5m/15m is much stronger`;

export interface BrainDecision {
  action: "TRADE" | "SKIP";
  direction: "LONG" | "SHORT";
  conviction: number;
  reasoning: string;
  suggested_stop_atr_mult: number | null;
}

export class ClaudeBrain {
  private client: Anthropic;

  constructor() {
    this.client = new Anthropic();
  }

  async analyze(
    events: DetectedEvent[],
    indicators: MultiTFIndicators,
    context: {
      funding_rate: number;
      long_short_ratio: number;
      ob_ratio: number;
      fear_greed: number;
      fear_greed_label: string;
      similar_setups: SetupRecord[];
      bankroll: number;
      has_position: boolean;
    }
  ): Promise<BrainDecision> {
    const eventsList = events.map((e) => `${e.type} (${e.direction}, strength=${e.strength.toFixed(2)}): ${e.detail}`).join("\n");

    const similarText = context.similar_setups.length > 0
      ? context.similar_setups.map((s) => {
          const result = s.outcome ? `${s.outcome.won ? "WIN" : "LOSS"} ${s.outcome.pnl_pct > 0 ? "+" : ""}${s.outcome.pnl_pct.toFixed(2)}%` : "pending";
          return `- ${s.events.join("+")} → ${s.direction} (conviction ${s.conviction.toFixed(2)}) → ${result}`;
        }).join("\n")
      : "No similar setups recorded yet.";

    const prompt = `EVENTS DETECTED (confluence: ${events.length}):
${eventsList}

MULTI-TIMEFRAME CONTEXT:
- 1min: RSI=${indicators["1m"].rsi.toFixed(1)}, EMA12${indicators["1m"].ema12 > indicators["1m"].ema26 ? ">" : "<"}EMA26, MACD hist=${indicators["1m"].macd_hist.toFixed(2)}, price=${indicators["1m"].price.toFixed(2)}, BB=[${indicators["1m"].bb.lower.toFixed(0)}-${indicators["1m"].bb.upper.toFixed(0)}]
- 5min: RSI=${indicators["5m"].rsi.toFixed(1)}, EMA12${indicators["5m"].ema12 > indicators["5m"].ema26 ? ">" : "<"}EMA26, MACD hist=${indicators["5m"].macd_hist.toFixed(2)}
- 15min: RSI=${indicators["15m"].rsi.toFixed(1)}, EMA12${indicators["15m"].ema12 > indicators["15m"].ema26 ? ">" : "<"}EMA26, MACD hist=${indicators["15m"].macd_hist.toFixed(2)}

MARKET DATA:
- Funding rate: ${(context.funding_rate * 100).toFixed(4)}% ${context.funding_rate > 0 ? "(longs pay → bearish pressure)" : "(shorts pay → bullish pressure)"}
- Long/Short ratio: ${context.long_short_ratio.toFixed(2)}
- Order book: ${(context.ob_ratio * 100).toFixed(0)}% bid / ${((1 - context.ob_ratio) * 100).toFixed(0)}% ask
- Fear & Greed: ${context.fear_greed} (${context.fear_greed_label})

PATTERN MEMORY (similar past setups):
${similarText}

PORTFOLIO: Bankroll $${context.bankroll.toFixed(2)} | Position: ${context.has_position ? "ALREADY OPEN — SKIP" : "None"}

Your decision:`;

    try {
      const response = await this.client.messages.create({
        model: CONFIG.claude.model,
        max_tokens: CONFIG.claude.max_tokens,
        system: SYSTEM_PROMPT,
        messages: [{ role: "user", content: prompt }],
      });
      const text = response.content[0].type === "text" ? response.content[0].text : "";
      return JSON.parse(text.trim()) as BrainDecision;
    } catch (e) {
      console.error("[BRAIN] Claude error, using fallback:", e);
      return this.fallback(events, indicators);
    }
  }

  private fallback(events: DetectedEvent[], indicators: MultiTFIndicators): BrainDecision {
    // Simple rule-based fallback
    const bullish = events.filter((e) => e.direction === "bullish");
    const bearish = events.filter((e) => e.direction === "bearish");

    if (bullish.length > bearish.length && bullish.length >= 2) {
      return {
        action: "TRADE", direction: "LONG",
        conviction: 0.6 + bullish.length * 0.05,
        reasoning: `Fallback: ${bullish.length} bullish events detected`,
        suggested_stop_atr_mult: null,
      };
    }
    if (bearish.length > bullish.length && bearish.length >= 2) {
      return {
        action: "TRADE", direction: "SHORT",
        conviction: 0.6 + bearish.length * 0.05,
        reasoning: `Fallback: ${bearish.length} bearish events detected`,
        suggested_stop_atr_mult: null,
      };
    }
    return {
      action: "SKIP", direction: "LONG", conviction: 0,
      reasoning: "Fallback: insufficient confluence", suggested_stop_atr_mult: null,
    };
  }
}
```

**Step 2: Commit**

```bash
git add src/brain/claude-brain.ts
git commit -m "feat: add Claude brain with contextual analysis and fallback"
```

---

### Task 9: Learning Engine (Optimizer)

**Files:**
- Create: `btc-sniper/src/learning/optimizer.ts`
- Create: `btc-sniper/tests/optimizer.test.ts`

**Step 1: Write failing test**

```typescript
// tests/optimizer.test.ts
import { describe, it, expect } from "vitest";
import { Optimizer } from "../src/learning/optimizer.js";

describe("Optimizer", () => {
  it("adjusts min_conviction up after losses", () => {
    const opt = new Optimizer();
    const initial = opt.params.min_conviction;
    // Record 8 losses, 2 wins
    for (let i = 0; i < 8; i++) opt.recordOutcome(false, -0.5, ["RSI_EXTREME"]);
    for (let i = 0; i < 2; i++) opt.recordOutcome(true, 1.0, ["RSI_EXTREME"]);
    expect(opt.params.min_conviction).toBeGreaterThan(initial);
  });

  it("adjusts event reliability", () => {
    const opt = new Optimizer();
    // RSI_EXTREME: 5 wins, 1 loss
    for (let i = 0; i < 5; i++) opt.recordOutcome(true, 0.5, ["RSI_EXTREME"]);
    opt.recordOutcome(false, -0.3, ["RSI_EXTREME"]);
    // EMA_CROSS: 1 win, 5 losses
    opt.recordOutcome(true, 0.5, ["EMA_CROSS"]);
    for (let i = 0; i < 5; i++) opt.recordOutcome(false, -0.3, ["EMA_CROSS"]);

    expect(opt.params.event_reliability["RSI_EXTREME"]).toBeGreaterThan(
      opt.params.event_reliability["EMA_CROSS"]
    );
  });
});
```

**Step 2: Run test to verify fails**

Run: `cd btc-sniper && npx vitest run tests/optimizer.test.ts`
Expected: FAIL

**Step 3: Implement**

```typescript
// src/learning/optimizer.ts
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { dirname } from "path";
import { CONFIG, type EventType } from "../config/default.js";

export interface OptimizerParams {
  min_conviction: number;
  stop_multiplier: number;
  max_stake_pct: number;
  event_reliability: Record<string, number>;
}

interface Outcome {
  won: boolean;
  pnl: number;
  events: EventType[];
}

export class Optimizer {
  params: OptimizerParams;
  private outcomes: Outcome[] = [];
  private alpha = 0.08; // learning rate

  constructor() {
    this.params = {
      min_conviction: CONFIG.trading.min_conviction,
      stop_multiplier: CONFIG.trading.stop_multiplier,
      max_stake_pct: CONFIG.trading.max_stake_pct,
      event_reliability: {
        RSI_EXTREME: 1.0, EMA_CROSS: 1.0, DIVERGENCE: 1.0,
        VOLUME_SPIKE: 1.0, BB_BREAKOUT: 1.0, FUNDING_FLIP: 1.0,
        LIQUIDATION_WAVE: 1.0,
      },
    };
    this.load();
  }

  recordOutcome(won: boolean, pnl: number, events: EventType[]): void {
    this.outcomes.push({ won, pnl, events });
    this.update();
    this.save();
  }

  private update(): void {
    if (this.outcomes.length < 5) return;
    const recent = this.outcomes.slice(-30);
    const wins = recent.filter((o) => o.won);
    const winRate = wins.length / recent.length;

    // Adjust min_conviction
    if (winRate < 0.45) {
      this.params.min_conviction = Math.min(0.85, this.params.min_conviction + this.alpha * 0.05);
    } else if (winRate > 0.60) {
      this.params.min_conviction = Math.max(0.40, this.params.min_conviction - this.alpha * 0.03);
    }

    // Adjust stop multiplier based on risk/reward
    const avgWin = wins.length > 0 ? wins.reduce((s, o) => s + o.pnl, 0) / wins.length : 0;
    const losses = recent.filter((o) => !o.won);
    const avgLoss = losses.length > 0 ? Math.abs(losses.reduce((s, o) => s + o.pnl, 0) / losses.length) : 1;
    if (avgLoss > 0 && avgWin / avgLoss < 1.2) {
      this.params.stop_multiplier = Math.max(0.5, this.params.stop_multiplier - this.alpha * 0.05);
    } else if (avgWin / avgLoss > 2.0) {
      this.params.stop_multiplier = Math.min(1.5, this.params.stop_multiplier + this.alpha * 0.05);
    }

    // Adjust max stake
    if (winRate < 0.40) {
      this.params.max_stake_pct = Math.max(0.10, this.params.max_stake_pct - this.alpha * 0.02);
    } else if (winRate > 0.55) {
      this.params.max_stake_pct = Math.min(0.30, this.params.max_stake_pct + this.alpha * 0.01);
    }

    // Adjust event reliability
    const eventTypes = Object.keys(this.params.event_reliability) as EventType[];
    for (const type of eventTypes) {
      const matching = recent.filter((o) => o.events.includes(type));
      if (matching.length < 3) continue;
      const typeWinRate = matching.filter((o) => o.won).length / matching.length;
      // Exponential moving toward actual win rate
      const current = this.params.event_reliability[type];
      this.params.event_reliability[type] = Math.round(
        (current * (1 - this.alpha) + typeWinRate * 2 * this.alpha) * 1000
      ) / 1000;
    }
  }

  private load(): void {
    try {
      if (existsSync(CONFIG.data.optimizer_file)) {
        const raw = readFileSync(CONFIG.data.optimizer_file, "utf-8");
        const data = JSON.parse(raw);
        if (data.params) this.params = { ...this.params, ...data.params };
        if (data.outcomes) this.outcomes = data.outcomes;
      }
    } catch { /* start fresh */ }
  }

  private save(): void {
    try {
      const dir = dirname(CONFIG.data.optimizer_file);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      writeFileSync(
        CONFIG.data.optimizer_file,
        JSON.stringify({ params: this.params, outcomes: this.outcomes }, null, 2)
      );
    } catch { /* ignore */ }
  }
}
```

**Step 4: Run tests**

Run: `cd btc-sniper && npx vitest run tests/optimizer.test.ts`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/learning/optimizer.ts tests/optimizer.test.ts
git commit -m "feat: add optimizer with event reliability learning"
```

---

### Task 10: Main Orchestrator (index.ts)

**Files:**
- Modify: `btc-sniper/src/index.ts`

**Step 1: Wire everything together**

```typescript
// src/index.ts
import { CONFIG } from "./config/default.js";
import { MarketEngine } from "./engine/market-engine.js";
import { EventDetector, type MultiTFIndicators } from "./engine/event-detector.js";
import { TechnicalAgent } from "./agents/technical.js";
import { SentimentAgent } from "./agents/sentiment.js";
import { OnChainAgent } from "./agents/onchain.js";
import { OrderFlowAgent } from "./agents/orderflow.js";
import { ClaudeBrain } from "./brain/claude-brain.js";
import { PatternMemory } from "./learning/pattern-memory.js";
import { TradeJournal } from "./learning/trade-journal.js";
import { PositionManager } from "./trading/position-manager.js";
import { RiskManager } from "./trading/risk-manager.js";
import { Optimizer } from "./learning/optimizer.js";

// Initialize components
const market = new MarketEngine();
const eventDetector = new EventDetector();
const techAgent = new TechnicalAgent();
const sentimentAgent = new SentimentAgent();
const onchainAgent = new OnChainAgent();
const orderflowAgent = new OrderFlowAgent();
const brain = new ClaudeBrain();
const patternMemory = new PatternMemory();
const journal = new TradeJournal();
const posManager = new PositionManager();
const riskManager = new RiskManager(CONFIG.trading.initial_bankroll);
const optimizer = new Optimizer();

// Track state
let currentPatternId: number | undefined;
let eventsLog: { time: number; events: string[]; action: string }[] = [];

// Export for dashboard access
export const state = {
  market, eventDetector, techAgent, sentimentAgent, onchainAgent,
  orderflowAgent, brain, patternMemory, journal, posManager,
  riskManager, optimizer, eventsLog,
  get price() { return market.price; },
};

async function onCandle() {
  const price = market.price;
  if (price <= 0) return;

  // 1. Compute indicators on all timeframes
  const indicators: MultiTFIndicators = {
    "1m": techAgent.analyze(market.getBuffer("1m").getAll()),
    "5m": techAgent.analyze(market.getBuffer("5m").getAll()),
    "15m": techAgent.analyze(market.getBuffer("15m").getAll()),
  };

  // 2. Check trailing stop if position is open
  if (posManager.hasPosition) {
    const tick = posManager.tick(price);
    if (tick.action === "CLOSE") {
      const result = posManager.close(price, tick.reason);
      const won = result.pnl > 0;
      riskManager.updateBankroll(riskManager.bankroll + result.pnl);
      if (won) riskManager.recordWin(); else riskManager.recordLoss();

      const openTrade = journal.getOpen();
      if (openTrade) {
        journal.recordClose(openTrade.id, price, result.pnl, result.reason, result.duration_s, riskManager.bankroll);
      }
      if (currentPatternId !== undefined) {
        patternMemory.recordOutcome(currentPatternId, {
          won, pnl_pct: result.pnl / (openTrade?.stake ?? 1) * 100,
          duration_s: result.duration_s, exit_reason: result.reason,
        });
      }
      optimizer.recordOutcome(won, result.pnl, openTrade?.events as any[] ?? []);
      currentPatternId = undefined;
      console.log(`[TRADE] CLOSED ${result.direction} pnl=$${result.pnl.toFixed(2)} reason=${result.reason} bankroll=$${riskManager.bankroll.toFixed(2)}`);
    }
    return; // Don't look for new trades while in position
  }

  // 3. Detect events
  const events = eventDetector.detect(indicators);

  // 3b. Check external data events
  const onchain = await onchainAgent.fetch();
  const fundingEvent = eventDetector.detectFundingFlip(onchainAgent.previousFundingRate, onchain.funding_rate);
  if (fundingEvent) events.push(fundingEvent);

  // No events = no trade, sniper waits
  if (events.length === 0) return;

  // 4. Log events
  eventsLog.push({ time: Date.now(), events: events.map((e) => e.type), action: "detecting" });
  if (eventsLog.length > 100) eventsLog.shift();
  console.log(`[EVENT] ${events.map((e) => `${e.type}(${e.direction})`).join(" + ")} — confluence: ${events.length}`);

  // 5. Check circuit breakers
  if (!riskManager.canTrade()) {
    console.log("[RISK] Circuit breaker active — skipping");
    return;
  }

  // 6. Get context for Claude
  const sentiment = await sentimentAgent.fetch();
  const orderflow = await orderflowAgent.fetch();
  const similar = patternMemory.findSimilar(
    events.map((e) => e.type),
    { rsi: indicators["1m"].rsi },
    5
  );

  // 7. Ask Claude
  const decision = await brain.analyze(events, indicators, {
    funding_rate: onchain.funding_rate,
    long_short_ratio: onchain.long_short_ratio,
    ob_ratio: orderflow.ob_ratio,
    fear_greed: sentiment.fear_greed,
    fear_greed_label: sentiment.fear_greed_label,
    similar_setups: similar,
    bankroll: riskManager.bankroll,
    has_position: posManager.hasPosition,
  });

  console.log(`[BRAIN] ${decision.action} ${decision.direction} conviction=${decision.conviction.toFixed(2)} — ${decision.reasoning}`);
  eventsLog[eventsLog.length - 1].action = decision.action;

  // 8. Execute if TRADE
  if (decision.action === "TRADE" && decision.conviction >= optimizer.params.min_conviction) {
    const atr = techAgent.computeATR(market.getBuffer("1m").getAll());
    const atrPct = atr / price;
    const stopMult = decision.suggested_stop_atr_mult ?? optimizer.params.stop_multiplier;
    const stake = riskManager.computeStake(decision.conviction, optimizer.params.max_stake_pct);
    const leverage = riskManager.computeLeverage(decision.conviction, atrPct);

    posManager.open(decision.direction as "LONG" | "SHORT", price, stake, atr, stopMult, leverage);

    // Record pattern
    currentPatternId = patternMemory.record({
      events: events.map((e) => e.type),
      indicators_1m: { rsi: indicators["1m"].rsi, ema12: indicators["1m"].ema12, ema26: indicators["1m"].ema26 },
      indicators_5m: { rsi: indicators["5m"].rsi, ema12: indicators["5m"].ema12, ema26: indicators["5m"].ema26 },
      direction: decision.direction,
      conviction: decision.conviction,
    });

    // Record trade
    journal.recordOpen({
      timestamp: Date.now(), direction: decision.direction,
      entry_price: price, stake, leverage, score: Math.round(decision.conviction * 100),
      conviction: decision.conviction, events: events.map((e) => e.type),
      reasoning: decision.reasoning, pattern_id: currentPatternId,
    });

    console.log(`[TRADE] OPENED ${decision.direction} $${stake.toFixed(2)} @${price.toFixed(2)} lev=${leverage}x stop=${posManager.position!.stopLoss.toFixed(2)}`);
  }
}

async function main() {
  console.log(`
  ╔══════════════════════════════════════╗
  ║   BTC SNIPER — Event-Driven Bot     ║
  ║   Mode: Simulation                   ║
  ║   Dashboard: http://localhost:${CONFIG.dashboard.port}   ║
  ╚══════════════════════════════════════╝
  `);

  // Seed historical data
  await market.seedHistorical();

  // Connect WebSocket
  market.connect();

  // On every closed 1m candle, run analysis
  market.on("candle", () => onCandle());

  // Also check trailing stop on every tick (more frequent)
  market.on("tick", (price: number) => {
    if (posManager.hasPosition) {
      const tick = posManager.tick(price);
      if (tick.action === "CLOSE") {
        const result = posManager.close(price, tick.reason);
        const won = result.pnl > 0;
        riskManager.updateBankroll(riskManager.bankroll + result.pnl);
        if (won) riskManager.recordWin(); else riskManager.recordLoss();
        const openTrade = journal.getOpen();
        if (openTrade) {
          journal.recordClose(openTrade.id, price, result.pnl, result.reason, result.duration_s, riskManager.bankroll);
        }
        if (currentPatternId !== undefined) {
          patternMemory.recordOutcome(currentPatternId, {
            won, pnl_pct: result.pnl / (openTrade?.stake ?? 1) * 100,
            duration_s: result.duration_s, exit_reason: result.reason,
          });
        }
        optimizer.recordOutcome(won, result.pnl, openTrade?.events as any[] ?? []);
        currentPatternId = undefined;
        console.log(`[TRADE] CLOSED ${result.direction} pnl=$${result.pnl.toFixed(2)} reason=${result.reason} bankroll=$${riskManager.bankroll.toFixed(2)}`);
      }
    }
  });

  // Periodic external data fetch
  setInterval(async () => {
    await sentimentAgent.fetch();
    await onchainAgent.fetch();
    await orderflowAgent.fetch();
  }, 30_000);

  // Start dashboard
  const { startDashboard } = await import("./dashboard/server.js");
  await startDashboard();
}

main().catch(console.error);
```

**Step 2: Commit**

```bash
git add src/index.ts
git commit -m "feat: wire main orchestrator — event-driven trading loop"
```

---

### Task 11: Dashboard Server + Jarvis HUD Frontend

**Files:**
- Create: `btc-sniper/src/dashboard/server.ts`
- Create: `btc-sniper/src/dashboard/public/index.html`
- Create: `btc-sniper/src/dashboard/public/css/style.css`
- Create: `btc-sniper/src/dashboard/public/js/app.js`

This is the largest task. The frontend should be ported from the existing Python dashboard's HTML/CSS/JS with the same Jarvis HUD design (Orbitron + Share Tech Mono fonts, cyan/green accents, dark theme, GridStack panels).

**Step 1: Create Fastify server**

```typescript
// src/dashboard/server.ts
import Fastify from "fastify";
import fastifyStatic from "@fastify/static";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { CONFIG } from "../config/default.js";
import { state } from "../index.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

export async function startDashboard() {
  const app = Fastify({ logger: false });

  await app.register(fastifyStatic, {
    root: join(__dirname, "public"),
  });

  app.get("/api/data", async () => {
    const pos = state.posManager.getState();
    const indicators1m = state.techAgent.analyze(state.market.getBuffer("1m").getAll());
    const stats = state.journal.stats();
    const candles1m = state.market.getBuffer("1m").getAll();
    const sentiment = state.sentimentAgent.data;
    const onchain = state.onchainAgent.data;
    const orderflow = state.orderflowAgent.data;

    // Chart indicators
    const closes = candles1m.map((c) => c.close);
    const { computeEMA, computeBollinger, computeRSI } = await import("../engine/indicators.js");
    const ema12 = computeEMA(closes, 12);
    const ema26 = computeEMA(closes, 26);
    const bb = computeBollinger(closes, 20, 2);
    const rsi = computeRSI(closes, 14);

    return {
      price: state.price,
      candles: candles1m.slice(-300),
      chart_indicators: {
        ema12: ema12.map((v, i) => ({ time: candles1m[i]?.time, value: Math.round(v * 100) / 100 })),
        ema26: ema26.map((v, i) => ({ time: candles1m[i]?.time, value: Math.round(v * 100) / 100 })),
        bollinger: bb.map((b, i) => ({ time: candles1m[i + 19]?.time, upper: Math.round(b.upper * 100) / 100, lower: Math.round(b.lower * 100) / 100 })),
        rsi: rsi.map((v, i) => ({ time: candles1m[i + 14]?.time, value: Math.round(v * 10) / 10 })),
      },
      position: pos ? {
        ...pos,
        unrealized_pnl: state.posManager.unrealizedPnl(state.price),
        duration: Math.round((Date.now() - pos.openTime) / 1000),
      } : null,
      bankroll: state.riskManager.bankroll,
      initial: state.riskManager.initialBankroll,
      pnl: Math.round((state.riskManager.bankroll - state.riskManager.initialBankroll) * 100) / 100,
      trades: state.journal.getRecent(30),
      stats,
      events_log: state.eventsLog.slice(-20),
      sentiment: { fear_greed: sentiment.fear_greed, label: sentiment.fear_greed_label },
      onchain: { funding_rate: onchain.funding_rate, long_short_ratio: onchain.long_short_ratio, open_interest: onchain.open_interest },
      orderflow: { ob_ratio: orderflow.ob_ratio, bids: orderflow.bids.slice(0, 10), asks: orderflow.asks.slice(0, 10), spread: orderflow.spread },
      optimizer: state.optimizer.params,
      pattern_stats: state.patternMemory.allEventStats(),
      total_patterns: state.patternMemory.totalSetups(),
    };
  });

  app.get("/api/stats", async () => {
    return {
      performance: state.journal.stats(),
      optimizer: state.optimizer.params,
      pattern_stats: state.patternMemory.allEventStats(),
      total_patterns: state.patternMemory.totalSetups(),
      events_log: state.eventsLog.slice(-50),
    };
  });

  await app.listen({ port: CONFIG.dashboard.port, host: CONFIG.dashboard.host });
  console.log(`[DASHBOARD] http://localhost:${CONFIG.dashboard.port}`);
}
```

**Step 2: Create the frontend files**

The frontend HTML/CSS/JS should be ported from the existing `dashboard.py` HTML output, preserving:
- Same Jarvis HUD aesthetic (Orbitron + Share Tech Mono fonts, cyan glow, dark theme)
- GridStack drag-and-drop layout
- LightweightCharts with EMA/BB overlays
- Same panel structure: Stats bar, Chart, Command Center, Agents, Position, Bankroll, Smart Trading, Trade Log, Order Book
- **New panels:** Event Feed (showing detected events in real-time), Pattern Memory stats

Port the existing embedded HTML from `dashboard.py` into `public/index.html`, CSS into `public/css/style.css`, and JS into `public/js/app.js`. Adapt the API endpoints from `/api/data` to match the new response format.

**Step 3: Commit**

```bash
git add src/dashboard/
git commit -m "feat: add Fastify dashboard with Jarvis HUD frontend"
```

---

### Task 12: Integration Test — Run Full Bot

**Step 1: Create data directory**

```bash
cd btc-sniper && mkdir -p data
```

**Step 2: Run the bot**

```bash
npx tsx src/index.ts
```

**Step 3: Verify**

- [ ] Binance WS connects and candles stream in
- [ ] Dashboard loads at http://localhost:8080
- [ ] Chart shows live BTC candles with EMA/BB overlays
- [ ] Events appear in console when detected
- [ ] Claude is called when events fire
- [ ] Trades open/close with trailing stop
- [ ] Trade journal persists to `data/trades.json`
- [ ] Pattern memory persists to `data/patterns.json`
- [ ] Optimizer persists to `data/optimizer.json`

**Step 4: Commit**

```bash
git add -A
git commit -m "feat: complete sniper bot — ready for 24h simulation"
```
