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
  strength: number;
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
        events.push({
          type: "RSI_EXTREME",
          direction: "bullish",
          strength: (cfg.threshold_low - m1.rsi) / cfg.threshold_low,
          detail: `RSI 1m=${m1.rsi.toFixed(1)} 5m=${m5.rsi.toFixed(1)} — oversold`,
          timestamp: now,
        });
        this.lastFired.set("RSI_EXTREME", now);
      } else if (m1.rsi > cfg.threshold_high && m5.rsi > 60) {
        events.push({
          type: "RSI_EXTREME",
          direction: "bearish",
          strength: (m1.rsi - cfg.threshold_high) / (100 - cfg.threshold_high),
          detail: `RSI 1m=${m1.rsi.toFixed(1)} 5m=${m5.rsi.toFixed(1)} — overbought`,
          timestamp: now,
        });
        this.lastFired.set("RSI_EXTREME", now);
      }
    }

    // EMA Cross on 5m
    if (this.canFire("EMA_CROSS", now) && this.prevEma12_5m !== null) {
      const wasBullish = this.prevEma12_5m > this.prevEma26_5m!;
      const isBullish = m5.ema12 > m5.ema26;
      if (wasBullish !== isBullish) {
        events.push({
          type: "EMA_CROSS",
          direction: isBullish ? "bullish" : "bearish",
          strength: 0.7,
          detail: `EMA12 ${isBullish ? "crossed above" : "crossed below"} EMA26 on 5m`,
          timestamp: now,
        });
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
        events.push({
          type: "VOLUME_SPIKE",
          direction,
          strength: Math.min(1, ratio / 5),
          detail: `Volume ${ratio.toFixed(1)}x average`,
          timestamp: now,
        });
        this.lastFired.set("VOLUME_SPIKE", now);
      }
    }

    // BB Breakout
    if (this.canFire("BB_BREAKOUT", now)) {
      if (m1.price <= m1.bb.lower) {
        events.push({
          type: "BB_BREAKOUT",
          direction: "bullish",
          strength: 0.6,
          detail: `Price at lower Bollinger Band`,
          timestamp: now,
        });
        this.lastFired.set("BB_BREAKOUT", now);
      } else if (m1.price >= m1.bb.upper) {
        events.push({
          type: "BB_BREAKOUT",
          direction: "bearish",
          strength: 0.6,
          detail: `Price at upper Bollinger Band`,
          timestamp: now,
        });
        this.lastFired.set("BB_BREAKOUT", now);
      }
    }

    return events;
  }

  detectFundingFlip(
    prevRate: number,
    currentRate: number
  ): DetectedEvent | null {
    const now = Date.now();
    if (!this.canFire("FUNDING_FLIP", now)) return null;
    if (
      (prevRate >= 0 && currentRate < 0) ||
      (prevRate < 0 && currentRate >= 0)
    ) {
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

  detectLiquidationWave(
    longLiqs: number,
    shortLiqs: number
  ): DetectedEvent | null {
    const now = Date.now();
    if (!this.canFire("LIQUIDATION_WAVE", now)) return null;
    const threshold = CONFIG.events.LIQUIDATION_WAVE.threshold_usd;
    if (longLiqs > threshold) {
      this.lastFired.set("LIQUIDATION_WAVE", now);
      return {
        type: "LIQUIDATION_WAVE",
        direction: "bullish",
        strength: Math.min(1, longLiqs / (threshold * 3)),
        detail: `Long liquidations $${(longLiqs / 1e6).toFixed(1)}M — capitulation`,
        timestamp: now,
      };
    }
    if (shortLiqs > threshold) {
      this.lastFired.set("LIQUIDATION_WAVE", now);
      return {
        type: "LIQUIDATION_WAVE",
        direction: "bearish",
        strength: Math.min(1, shortLiqs / (threshold * 3)),
        detail: `Short liquidations $${(shortLiqs / 1e6).toFixed(1)}M`,
        timestamp: now,
      };
    }
    return null;
  }

  private canFire(type: EventType, now: number): boolean {
    const last = this.lastFired.get(type) ?? 0;
    const cooldown = CONFIG.events[type].cooldown_ms;
    return now - last >= cooldown;
  }
}
