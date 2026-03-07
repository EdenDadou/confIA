import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { CONFIG } from "../config/default.js";

export interface TradeRecord {
  id: string;
  direction: "LONG" | "SHORT";
  entry_price: number;
  exit_price?: number;
  stake: number;
  leverage: number;
  conviction: number;
  events: string[];
  opened_at: number;
  closed_at?: number;
  pnl_pct?: number;
  pnl_usd?: number;
  exit_reason?: string;
  won?: boolean;
}

export interface JournalStats {
  total: number;
  wins: number;
  losses: number;
  winRate: number;
  totalPnlPct: number;
  totalPnlUsd: number;
  avgPnlPct: number;
  avgWinPct: number;
  avgLossPct: number;
  bestTrade: number;
  worstTrade: number;
  avgDurationS: number;
}

export class TradeJournal {
  private trades: TradeRecord[] = [];

  constructor(private persist = true) {
    if (persist) this.load();
  }

  recordOpen(trade: Omit<TradeRecord, "id" | "opened_at">): string {
    const id = `trade_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    this.trades.push({
      ...trade,
      id,
      opened_at: Date.now(),
    });
    if (this.persist) this.save();
    return id;
  }

  recordClose(
    id: string,
    exitPrice: number,
    exitReason: string,
  ): TradeRecord | undefined {
    const trade = this.trades.find((t) => t.id === id);
    if (!trade) return undefined;

    trade.exit_price = exitPrice;
    trade.closed_at = Date.now();
    trade.exit_reason = exitReason;

    const direction = trade.direction === "LONG" ? 1 : -1;
    trade.pnl_pct =
      direction *
      ((exitPrice - trade.entry_price) / trade.entry_price) *
      trade.leverage *
      100;
    trade.pnl_usd = (trade.pnl_pct / 100) * trade.stake;
    trade.won = trade.pnl_pct > 0;

    if (this.persist) this.save();
    return trade;
  }

  getOpen(): TradeRecord[] {
    return this.trades.filter((t) => t.closed_at === undefined);
  }

  getClosed(): TradeRecord[] {
    return this.trades.filter((t) => t.closed_at !== undefined);
  }

  getRecent(n: number): TradeRecord[] {
    return this.trades.slice(-n);
  }

  getById(id: string): TradeRecord | undefined {
    return this.trades.find((t) => t.id === id);
  }

  stats(): JournalStats {
    const closed = this.getClosed();
    const wins = closed.filter((t) => t.won === true);
    const losses = closed.filter((t) => t.won === false);

    const totalPnlPct = closed.reduce((s, t) => s + (t.pnl_pct ?? 0), 0);
    const totalPnlUsd = closed.reduce((s, t) => s + (t.pnl_usd ?? 0), 0);

    const avgWinPct =
      wins.length > 0
        ? wins.reduce((s, t) => s + (t.pnl_pct ?? 0), 0) / wins.length
        : 0;
    const avgLossPct =
      losses.length > 0
        ? losses.reduce((s, t) => s + (t.pnl_pct ?? 0), 0) / losses.length
        : 0;

    const pnls = closed.map((t) => t.pnl_pct ?? 0);
    const durations = closed
      .filter((t) => t.closed_at && t.opened_at)
      .map((t) => (t.closed_at! - t.opened_at) / 1000);

    return {
      total: closed.length,
      wins: wins.length,
      losses: losses.length,
      winRate: closed.length > 0 ? wins.length / closed.length : 0,
      totalPnlPct,
      totalPnlUsd,
      avgPnlPct: closed.length > 0 ? totalPnlPct / closed.length : 0,
      avgWinPct,
      avgLossPct,
      bestTrade: pnls.length > 0 ? Math.max(...pnls) : 0,
      worstTrade: pnls.length > 0 ? Math.min(...pnls) : 0,
      avgDurationS:
        durations.length > 0
          ? durations.reduce((a, b) => a + b, 0) / durations.length
          : 0,
    };
  }

  totalTrades(): number {
    return this.trades.length;
  }

  save(): void {
    try {
      const filePath = CONFIG.data.trades_file;
      mkdirSync(dirname(filePath), { recursive: true });
      writeFileSync(filePath, JSON.stringify(this.trades, null, 2));
    } catch {
      // Silently fail — non-critical
    }
  }

  load(): void {
    try {
      const raw = readFileSync(CONFIG.data.trades_file, "utf-8");
      this.trades = JSON.parse(raw);
    } catch {
      this.trades = [];
    }
  }
}
