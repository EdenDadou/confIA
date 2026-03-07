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
    expect(rsi[rsi.length - 1]).toBeLessThan(50);
  });

  it("computes EMA", () => {
    const ema12 = computeEMA(closes, 12);
    expect(ema12).toHaveLength(closes.length);
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
      open: c - 0.2, high: c + 0.5, low: c - 0.5, close: c, volume: 100, time: i,
    }));
    const atr = computeATR(candles, 14);
    expect(atr).toBeGreaterThan(0);
  });

  it("computes OBV", () => {
    const candles = closes.map((c, i) => ({
      open: c, high: c + 0.5, low: c - 0.5, close: c, volume: 100 + i * 10, time: i,
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
    const priceLows = [100, 98, 95];
    const rsiLows = [25, 28, 32];
    const result = detectDivergence(priceLows, rsiLows);
    expect(result.type).toBe("bullish");
  });
});
