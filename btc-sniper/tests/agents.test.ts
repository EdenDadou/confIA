import { describe, it, expect } from "vitest";
import { TechnicalAgent } from "../src/agents/technical.js";

describe("TechnicalAgent", () => {
  it("computes indicators from candle buffers", () => {
    const agent = new TechnicalAgent();
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
