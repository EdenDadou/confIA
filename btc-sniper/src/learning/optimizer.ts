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
  private alpha = 0.08;

  constructor(private persist = true) {
    this.params = {
      min_conviction: CONFIG.trading.min_conviction,
      stop_multiplier: CONFIG.trading.stop_multiplier,
      max_stake_pct: CONFIG.trading.max_stake_pct,
      event_reliability: {
        RSI_EXTREME: 1.0,
        EMA_CROSS: 1.0,
        DIVERGENCE: 1.0,
        VOLUME_SPIKE: 1.0,
        BB_BREAKOUT: 1.0,
        FUNDING_FLIP: 1.0,
        LIQUIDATION_WAVE: 1.0,
      },
    };
    if (persist) this.load();
  }

  recordOutcome(won: boolean, pnl: number, events: EventType[]): void {
    this.outcomes.push({ won, pnl, events });
    this.update();
    if (this.persist) this.save();
  }

  private update(): void {
    if (this.outcomes.length < 5) return;
    const recent = this.outcomes.slice(-30);
    const wins = recent.filter((o) => o.won);
    const winRate = wins.length / recent.length;

    // Adjust min_conviction
    if (winRate < 0.45)
      this.params.min_conviction = Math.min(
        0.85,
        this.params.min_conviction + this.alpha * 0.05
      );
    else if (winRate > 0.6)
      this.params.min_conviction = Math.max(
        0.4,
        this.params.min_conviction - this.alpha * 0.03
      );

    // Adjust stop_multiplier based on win/loss ratio
    const avgWin =
      wins.length > 0
        ? wins.reduce((s, o) => s + o.pnl, 0) / wins.length
        : 0;
    const losses = recent.filter((o) => !o.won);
    const avgLoss =
      losses.length > 0
        ? Math.abs(losses.reduce((s, o) => s + o.pnl, 0) / losses.length)
        : 1;
    if (avgLoss > 0 && avgWin / avgLoss < 1.2)
      this.params.stop_multiplier = Math.max(
        0.5,
        this.params.stop_multiplier - this.alpha * 0.05
      );
    else if (avgWin / avgLoss > 2.0)
      this.params.stop_multiplier = Math.min(
        1.5,
        this.params.stop_multiplier + this.alpha * 0.05
      );

    // Adjust max_stake_pct
    if (winRate < 0.4)
      this.params.max_stake_pct = Math.max(
        0.1,
        this.params.max_stake_pct - this.alpha * 0.02
      );
    else if (winRate > 0.55)
      this.params.max_stake_pct = Math.min(
        0.3,
        this.params.max_stake_pct + this.alpha * 0.01
      );

    // Adjust event reliability
    const eventTypes = Object.keys(
      this.params.event_reliability
    ) as EventType[];
    for (const type of eventTypes) {
      const matching = recent.filter((o) => o.events.includes(type));
      if (matching.length < 3) continue;
      const typeWinRate =
        matching.filter((o) => o.won).length / matching.length;
      const current = this.params.event_reliability[type];
      this.params.event_reliability[type] =
        Math.round(
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
    } catch {}
  }

  private save(): void {
    try {
      const dir = dirname(CONFIG.data.optimizer_file);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      writeFileSync(
        CONFIG.data.optimizer_file,
        JSON.stringify(
          { params: this.params, outcomes: this.outcomes },
          null,
          2
        )
      );
    } catch {}
  }
}
