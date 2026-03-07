import WebSocket from "ws";
import { EventEmitter } from "events";
import { CONFIG, type Timeframe } from "../config/default.js";
import type { Candle } from "./indicators.js";

export { type Candle } from "./indicators.js";

export class CandleBuffer {
  private candles: Candle[] = [];
  private max: number;

  constructor(max = 500) { this.max = max; }

  add(candle: Candle): void {
    const existing = this.candles.findIndex((c) => c.time === candle.time);
    if (existing >= 0) { this.candles[existing] = candle; }
    else {
      this.candles.push(candle);
      if (this.candles.length > this.max) this.candles.shift();
    }
  }

  latest(): Candle | undefined { return this.candles[this.candles.length - 1]; }
  getAll(): Candle[] { return [...this.candles]; }
  closes(): number[] { return this.candles.map((c) => c.close); }
  length(): number { return this.candles.length; }
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
  private lastPrice = 0;

  get price(): number { return this.lastPrice; }
  getBuffer(tf: Timeframe): CandleBuffer { return this.buffers[tf]; }

  async seedHistorical(): Promise<void> {
    try {
      const url = `${CONFIG.binance.restUrl}/api/v3/klines?symbol=${CONFIG.binance.symbol}&interval=1m&limit=500`;
      const res = await fetch(url);
      const data = (await res.json()) as any[];
      for (const k of data) {
        const candle: Candle = {
          time: Math.floor(k[0] / 1000), open: parseFloat(k[1]),
          high: parseFloat(k[2]), low: parseFloat(k[3]),
          close: parseFloat(k[4]), volume: parseFloat(k[5]),
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
          time: Math.floor(k.t / 1000), open: parseFloat(k.o),
          high: parseFloat(k.h), low: parseFloat(k.l),
          close: parseFloat(k.c), volume: parseFloat(k.v),
        };
        this.lastPrice = candle.close;
        this.buffers["1m"].add(candle);
        if (k.x) {
          this.rebuildHigherTimeframes();
          this.emit("candle", { timeframe: "1m" as Timeframe, candle });
        }
        this.emit("tick", candle.close);
      } catch { /* ignore */ }
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
    this.buildAggregated(all1m, 5, "5m");
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

  disconnect(): void { this.ws?.close(); }
}
