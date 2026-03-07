import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { CONFIG, type EventType } from "../config/default.js";

export interface SetupRecord {
  events: EventType[];
  indicators_1m: Record<string, number>;
  indicators_5m: Record<string, number>;
  direction: "LONG" | "SHORT";
  conviction: number;
  timestamp?: number;
  outcome?: {
    won: boolean;
    pnl_pct: number;
    duration_s: number;
    exit_reason: string;
  };
}

export interface EventStats {
  total: number;
  wins: number;
  losses: number;
  winRate: number;
  avgPnl: number;
  avgDuration: number;
}

export class PatternMemory {
  private setups: SetupRecord[] = [];

  constructor(private persist = true) {
    if (persist) this.load();
  }

  record(setup: Omit<SetupRecord, "timestamp" | "outcome">): void {
    this.setups.push({
      ...setup,
      timestamp: Date.now(),
    });
    if (this.persist) this.save();
  }

  recordOutcome(
    index: number,
    outcome: NonNullable<SetupRecord["outcome"]>,
  ): void {
    if (index >= 0 && index < this.setups.length) {
      this.setups[index].outcome = outcome;
      if (this.persist) this.save();
    }
  }

  findSimilar(
    events: EventType[],
    indicators_1m: Record<string, number>,
    limit: number,
  ): SetupRecord[] {
    const scored = this.setups
      .filter((s) => s.outcome !== undefined)
      .map((s) => {
        // Score based on event overlap
        const eventOverlap = events.filter((e) => s.events.includes(e)).length;
        const eventScore = events.length > 0 ? eventOverlap / events.length : 0;

        // Score based on indicator similarity (RSI distance)
        let indicatorScore = 0;
        const keys = Object.keys(indicators_1m);
        if (keys.length > 0) {
          const distances = keys.map((k) => {
            const v1 = indicators_1m[k] ?? 0;
            const v2 = s.indicators_1m[k] ?? 0;
            return Math.abs(v1 - v2);
          });
          const avgDist =
            distances.reduce((a, b) => a + b, 0) / distances.length;
          indicatorScore = Math.max(0, 1 - avgDist / 100);
        }

        const score = eventScore * 0.6 + indicatorScore * 0.4;
        return { setup: s, score };
      })
      .filter((s) => s.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);

    return scored.map((s) => s.setup);
  }

  eventStats(eventType: EventType): EventStats {
    const matching = this.setups.filter(
      (s) => s.events.includes(eventType) && s.outcome !== undefined,
    );
    const wins = matching.filter((s) => s.outcome!.won).length;
    const losses = matching.length - wins;
    const avgPnl =
      matching.length > 0
        ? matching.reduce((sum, s) => sum + s.outcome!.pnl_pct, 0) /
          matching.length
        : 0;
    const avgDuration =
      matching.length > 0
        ? matching.reduce((sum, s) => sum + s.outcome!.duration_s, 0) /
          matching.length
        : 0;

    return {
      total: matching.length,
      wins,
      losses,
      winRate: matching.length > 0 ? wins / matching.length : 0,
      avgPnl,
      avgDuration,
    };
  }

  allEventStats(): Record<string, EventStats> {
    const allEvents = new Set<EventType>();
    for (const s of this.setups) {
      for (const e of s.events) {
        allEvents.add(e);
      }
    }
    const result: Record<string, EventStats> = {};
    for (const e of allEvents) {
      result[e] = this.eventStats(e);
    }
    return result;
  }

  totalSetups(): number {
    return this.setups.length;
  }

  getAll(): SetupRecord[] {
    return [...this.setups];
  }

  save(): void {
    try {
      const filePath = CONFIG.data.patterns_file;
      mkdirSync(dirname(filePath), { recursive: true });
      writeFileSync(filePath, JSON.stringify(this.setups, null, 2));
    } catch {
      // Silently fail — non-critical
    }
  }

  load(): void {
    try {
      const raw = readFileSync(CONFIG.data.patterns_file, "utf-8");
      this.setups = JSON.parse(raw);
    } catch {
      this.setups = [];
    }
  }
}
