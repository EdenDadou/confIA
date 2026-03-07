import { describe, it, expect } from "vitest";
import { CandleBuffer, aggregateCandles } from "../src/engine/market-engine.js";

describe("CandleBuffer", () => {
  it("adds candles and respects max size", () => {
    const buf = new CandleBuffer(5);
    for (let i = 0; i < 10; i++) {
      buf.add({ time: i, open: 100, high: 101, low: 99, close: 100, volume: 10 });
    }
    expect(buf.getAll()).toHaveLength(5);
    expect(buf.getAll()[0].time).toBe(5);
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
    expect(candle5m.open).toBe(100);
    expect(candle5m.close).toBe(106);
    expect(candle5m.high).toBe(109);
    expect(candle5m.low).toBe(95);
    expect(candle5m.volume).toBe(60);
  });
});
