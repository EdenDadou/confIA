// ===== BTC SNIPER — Jarvis HUD Dashboard =====

// --- Chart setup ---
let mainChart, candleSeries, ema12Series, ema26Series, bbUpperSeries, bbLowerSeries;
let rsiChart, rsiSeries;
let lastCandleCount = 0;

function initCharts() {
  const mainEl = document.getElementById("chart-main");
  const rsiEl = document.getElementById("chart-rsi");
  if (!mainEl || !rsiEl) return;

  const chartOptions = {
    layout: {
      background: { color: "#0d0d14" },
      textColor: "#7a8599",
      fontFamily: "'Share Tech Mono', monospace",
      fontSize: 10,
    },
    grid: {
      vertLines: { color: "rgba(26, 26, 46, 0.5)" },
      horzLines: { color: "rgba(26, 26, 46, 0.5)" },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "rgba(0, 240, 255, 0.3)", width: 1, style: LightweightCharts.LineStyle.Dashed },
      horzLine: { color: "rgba(0, 240, 255, 0.3)", width: 1, style: LightweightCharts.LineStyle.Dashed },
    },
    timeScale: {
      timeVisible: true,
      secondsVisible: false,
      borderColor: "#1a1a2e",
    },
    rightPriceScale: {
      borderColor: "#1a1a2e",
    },
  };

  mainChart = LightweightCharts.createChart(mainEl, chartOptions);

  candleSeries = mainChart.addCandlestickSeries({
    upColor: "#00ff88",
    downColor: "#ff3366",
    borderUpColor: "#00ff88",
    borderDownColor: "#ff3366",
    wickUpColor: "#00ff88",
    wickDownColor: "#ff3366",
  });

  ema12Series = mainChart.addLineSeries({
    color: "#00f0ff",
    lineWidth: 1,
    title: "EMA12",
    priceLineVisible: false,
    lastValueVisible: false,
  });

  ema26Series = mainChart.addLineSeries({
    color: "#4466ff",
    lineWidth: 1,
    title: "EMA26",
    priceLineVisible: false,
    lastValueVisible: false,
  });

  bbUpperSeries = mainChart.addLineSeries({
    color: "#ffaa00",
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    title: "BB Upper",
    priceLineVisible: false,
    lastValueVisible: false,
  });

  bbLowerSeries = mainChart.addLineSeries({
    color: "#ffaa00",
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    title: "BB Lower",
    priceLineVisible: false,
    lastValueVisible: false,
  });

  // RSI sub-chart
  rsiChart = LightweightCharts.createChart(rsiEl, {
    ...chartOptions,
    rightPriceScale: {
      borderColor: "#1a1a2e",
      scaleMargins: { top: 0.1, bottom: 0.1 },
    },
  });

  rsiSeries = rsiChart.addLineSeries({
    color: "#ffaa00",
    lineWidth: 1,
    title: "RSI",
    priceLineVisible: false,
    lastValueVisible: true,
  });

  // RSI reference lines
  const rsiOverbought = rsiChart.addLineSeries({
    color: "rgba(255, 51, 102, 0.3)",
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dotted,
    priceLineVisible: false,
    lastValueVisible: false,
  });
  const rsiOversold = rsiChart.addLineSeries({
    color: "rgba(0, 255, 136, 0.3)",
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dotted,
    priceLineVisible: false,
    lastValueVisible: false,
  });

  // Placeholder data for RSI lines (will be set dynamically)
  rsiOverbought._levelValue = 70;
  rsiOversold._levelValue = 30;

  // Sync crosshair
  mainChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (range) rsiChart.timeScale().setVisibleLogicalRange(range);
  });
  rsiChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (range) mainChart.timeScale().setVisibleLogicalRange(range);
  });

  // Handle resize
  const resizeObserver = new ResizeObserver(() => {
    const mainRect = mainEl.getBoundingClientRect();
    mainChart.applyOptions({ width: mainRect.width, height: mainRect.height });
    const rsiRect = rsiEl.getBoundingClientRect();
    rsiChart.applyOptions({ width: rsiRect.width, height: rsiRect.height });
  });
  resizeObserver.observe(mainEl);
  resizeObserver.observe(rsiEl);
}

// --- GridStack setup ---
function initGrid() {
  GridStack.init({
    cellHeight: 80,
    margin: 4,
    animate: true,
    float: false,
    column: 12,
    draggable: { handle: ".panel-header" },
    resizable: { handles: "se,sw" },
  });
}

// --- Format helpers ---
function formatPrice(v) {
  if (v == null || isNaN(v)) return "--";
  return "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatPnl(v) {
  if (v == null || isNaN(v)) return "--";
  const sign = v >= 0 ? "+" : "";
  return sign + "$" + v.toFixed(2);
}

function formatPct(v) {
  if (v == null || isNaN(v)) return "--";
  return (v * 100).toFixed(1) + "%";
}

function formatPctRaw(v) {
  if (v == null || isNaN(v)) return "--";
  return v.toFixed(1) + "%";
}

function formatDuration(seconds) {
  if (seconds == null) return "--";
  if (seconds < 60) return seconds + "s";
  if (seconds < 3600) return Math.floor(seconds / 60) + "m " + (seconds % 60) + "s";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h + "h " + m + "m";
}

function formatTime(ts) {
  if (!ts) return "--";
  const d = new Date(ts);
  return d.toLocaleTimeString("en-US", { hour12: false });
}

function candleTime(ts) {
  // LightweightCharts expects UTC timestamp in seconds
  // Our candles already store time in seconds
  return ts;
}

// --- Clock ---
function updateClock() {
  const now = new Date();
  document.getElementById("clock").textContent = now.toLocaleTimeString("en-US", { hour12: false });
}
setInterval(updateClock, 1000);
updateClock();

// --- Update functions ---

function updateStatsBar(data) {
  setStatValue("stat-price", formatPrice(data.price), "cyan");
  setStatValue("stat-bankroll", formatPrice(data.bankroll));
  setStatValue("stat-pnl", formatPnl(data.pnl), data.pnl >= 0 ? "positive" : "negative");
  setStatValue("stat-winrate", formatPct(data.stats.winRate));
  setStatValue("stat-trades", String(data.stats.total));
  setStatValue("stat-events", String(data.events_log.length));
}

function setStatValue(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  const old = el.textContent;
  el.textContent = text;
  el.className = "stat-value" + (cls ? " " + cls : "");
  if (old !== text && old !== "--") {
    el.classList.add("value-flash");
    setTimeout(() => el.classList.remove("value-flash"), 500);
  }
}

function updateChart(data) {
  if (!candleSeries || !data.candles || data.candles.length === 0) return;

  const candles = data.candles.map((c) => ({
    time: candleTime(c.time),
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }));

  candleSeries.setData(candles);

  // EMA12
  const ema12 = (data.chart_indicators.ema12 || [])
    .filter((d) => d.time && d.value)
    .map((d) => ({ time: candleTime(d.time), value: d.value }));
  if (ema12.length > 0) ema12Series.setData(ema12);

  // EMA26
  const ema26 = (data.chart_indicators.ema26 || [])
    .filter((d) => d.time && d.value)
    .map((d) => ({ time: candleTime(d.time), value: d.value }));
  if (ema26.length > 0) ema26Series.setData(ema26);

  // Bollinger
  const bbU = (data.chart_indicators.bollinger || [])
    .filter((d) => d.time && d.upper)
    .map((d) => ({ time: candleTime(d.time), value: d.upper }));
  const bbL = (data.chart_indicators.bollinger || [])
    .filter((d) => d.time && d.lower)
    .map((d) => ({ time: candleTime(d.time), value: d.lower }));
  if (bbU.length > 0) bbUpperSeries.setData(bbU);
  if (bbL.length > 0) bbLowerSeries.setData(bbL);

  // RSI
  const rsiData = (data.chart_indicators.rsi || [])
    .filter((d) => d.time && d.value != null)
    .map((d) => ({ time: candleTime(d.time), value: d.value }));
  if (rsiData.length > 0) rsiSeries.setData(rsiData);

  // RSI level lines (70/30)
  if (rsiData.length > 1) {
    const firstT = rsiData[0].time;
    const lastT = rsiData[rsiData.length - 1].time;
    // We set these via priceLine instead to avoid series overhead
  }

  // Auto-scroll on new candle
  if (candles.length !== lastCandleCount) {
    mainChart.timeScale().scrollToRealTime();
    rsiChart.timeScale().scrollToRealTime();
    lastCandleCount = candles.length;
  }
}

function updateEventFeed(data) {
  const el = document.getElementById("event-feed");
  if (!el) return;
  const events = data.events_log || [];

  if (events.length === 0) {
    el.innerHTML = '<div class="empty-state">Waiting for events...</div>';
    return;
  }

  let html = "";
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    const time = formatTime(ev.time);
    const actionCls = ev.action === "TRADE" ? " trade" : "";
    let badges = "";
    for (const e of ev.events) {
      // Determine direction hint from event name
      const cls = "neutral";
      badges += `<span class="event-badge ${cls}">${e}</span>`;
    }
    html += `<div class="event-item">
      <span class="event-time">${time}</span>
      <div class="event-badges">${badges}</div>
      <span class="event-action${actionCls}">${ev.action}</span>
    </div>`;
  }
  el.innerHTML = html;
}

function updatePosition(data) {
  const el = document.getElementById("position-panel");
  if (!el) return;
  const pos = data.position;

  if (!pos) {
    el.innerHTML = '<div class="empty-state">No active position</div>';
    return;
  }

  const dirCls = pos.direction === "LONG" ? "long" : "short";
  const pnlCls = pos.unrealized_pnl >= 0 ? "pnl-positive" : "pnl-negative";

  el.innerHTML = `
    <div class="position-grid">
      <div class="pos-direction-badge ${dirCls}">${pos.direction}</div>
      <div class="pos-field">
        <span class="pos-label">Entry</span>
        <span class="pos-value">${formatPrice(pos.entryPrice)}</span>
      </div>
      <div class="pos-field">
        <span class="pos-label">Leverage</span>
        <span class="pos-value">${pos.leverage}x</span>
      </div>
      <div class="pos-field">
        <span class="pos-label">Stop Loss</span>
        <span class="pos-value">${formatPrice(pos.stopLoss)}</span>
      </div>
      <div class="pos-field">
        <span class="pos-label">Stake</span>
        <span class="pos-value">${formatPrice(pos.stake)}</span>
      </div>
      <div class="pos-field">
        <span class="pos-label">Unrealized PnL</span>
        <span class="pos-value ${pnlCls}">${formatPnl(pos.unrealized_pnl)}</span>
      </div>
      <div class="pos-field">
        <span class="pos-label">Duration</span>
        <span class="pos-value">${formatDuration(pos.duration)}</span>
      </div>
    </div>
  `;
}

function updateAgents(data) {
  const el = document.getElementById("agents-panel");
  if (!el) return;

  const fg = data.sentiment.fear_greed;
  const fgLabel = data.sentiment.label || "N/A";
  const fgColor = fg < 30 ? "var(--red)" : fg > 70 ? "var(--green)" : "var(--amber)";

  const fr = data.onchain.funding_rate;
  const lsr = data.onchain.long_short_ratio;
  const oi = data.onchain.open_interest;
  const frColor = fr > 0.0005 ? "var(--green)" : fr < -0.0005 ? "var(--red)" : "var(--text-primary)";

  const obr = data.orderflow.ob_ratio;
  const bidPct = Math.round(obr * 100);
  const askPct = 100 - bidPct;
  const spread = data.orderflow.spread;

  el.innerHTML = `
    <div class="agent-section">
      <div class="agent-title">SENTIMENT</div>
      <div class="agent-row">
        <span class="agent-key">Fear & Greed</span>
        <span class="agent-val" style="color:${fgColor}">${fg} (${fgLabel})</span>
      </div>
      <div class="gauge-bar">
        <div class="gauge-needle" style="left:${fg}%"></div>
      </div>
    </div>
    <div class="agent-section">
      <div class="agent-title">ON-CHAIN</div>
      <div class="agent-row">
        <span class="agent-key">Funding Rate</span>
        <span class="agent-val" style="color:${frColor}">${(fr * 100).toFixed(4)}%</span>
      </div>
      <div class="agent-row">
        <span class="agent-key">Long/Short Ratio</span>
        <span class="agent-val">${Number(lsr).toFixed(3)}</span>
      </div>
      <div class="agent-row">
        <span class="agent-key">Open Interest</span>
        <span class="agent-val">${Number(oi).toLocaleString()} BTC</span>
      </div>
    </div>
    <div class="agent-section">
      <div class="agent-title">ORDER FLOW</div>
      <div class="agent-row">
        <span class="agent-key">Bid/Ask Ratio</span>
        <span class="agent-val">${(obr * 100).toFixed(1)}%</span>
      </div>
      <div class="ob-ratio-bar">
        <div class="ob-bid-fill" style="width:${bidPct}%">BID ${bidPct}%</div>
        <div class="ob-ask-fill" style="width:${askPct}%">ASK ${askPct}%</div>
      </div>
      <div class="agent-row">
        <span class="agent-key">Spread</span>
        <span class="agent-val">${formatPrice(spread)}</span>
      </div>
    </div>
  `;
}

function updatePatterns(data) {
  const el = document.getElementById("patterns-panel");
  if (!el) return;

  const stats = data.pattern_stats || {};
  const optimizer = data.optimizer || {};
  const reliability = optimizer.event_reliability || {};
  const keys = Object.keys(stats);

  if (keys.length === 0) {
    el.innerHTML = `<div class="empty-state">No patterns recorded (${data.total_patterns || 0} setups)</div>`;
    return;
  }

  let html = "";
  for (const key of keys) {
    const s = stats[key];
    const rel = reliability[key] ?? 1;
    const wr = s.total > 0 ? s.winRate : 0;
    const wrColor = wr >= 0.55 ? "var(--green)" : wr <= 0.45 ? "var(--red)" : "var(--amber)";
    const relPct = Math.round(rel * 50); // Scale 0-2 to 0-100
    const relColor = rel >= 1.1 ? "var(--green)" : rel <= 0.9 ? "var(--red)" : "var(--amber)";

    html += `<div class="pattern-row">
      <span class="pattern-name">${key}</span>
      <div class="pattern-stats">
        <span class="pattern-count">${s.total} trades</span>
        <span class="pattern-wr" style="color:${wrColor}">${formatPct(wr)}</span>
        <div class="reliability-bar">
          <div class="reliability-fill" style="width:${relPct}%;background:${relColor}"></div>
        </div>
      </div>
    </div>`;
  }
  el.innerHTML = html;
}

function updateOptimizer(data) {
  const el = document.getElementById("optimizer-panel");
  if (!el) return;
  const opt = data.optimizer;
  if (!opt) {
    el.innerHTML = '<div class="empty-state">No optimizer data</div>';
    return;
  }

  let reliabilityHtml = "";
  const rel = opt.event_reliability || {};
  for (const [key, val] of Object.entries(rel)) {
    const v = Number(val);
    const color = v >= 1.1 ? "var(--green)" : v <= 0.9 ? "var(--red)" : "var(--text-primary)";
    reliabilityHtml += `<div class="opt-row">
      <span class="opt-key">${key}</span>
      <span class="opt-val" style="color:${color}">${v.toFixed(3)}</span>
    </div>`;
  }

  el.innerHTML = `
    <div class="opt-row">
      <span class="opt-key">Min Conviction</span>
      <span class="opt-val">${(opt.min_conviction * 100).toFixed(1)}%</span>
    </div>
    <div class="opt-row">
      <span class="opt-key">Stop Multiplier</span>
      <span class="opt-val">${opt.stop_multiplier.toFixed(3)}</span>
    </div>
    <div class="opt-row">
      <span class="opt-key">Max Stake</span>
      <span class="opt-val">${(opt.max_stake_pct * 100).toFixed(1)}%</span>
    </div>
    <div class="opt-section-title">EVENT RELIABILITY</div>
    ${reliabilityHtml}
  `;
}

function updateTradeLog(data) {
  const el = document.getElementById("tradelog-panel");
  if (!el) return;
  const trades = data.trades || [];

  if (trades.length === 0) {
    el.innerHTML = '<div class="empty-state">No trades recorded</div>';
    return;
  }

  let rows = "";
  // Show most recent first
  for (let i = trades.length - 1; i >= 0; i--) {
    const t = trades[i];
    const dirCls = t.direction === "LONG" ? "dir-long" : "dir-short";
    const pnl = t.pnl_usd;
    const pnlCls = pnl != null ? (pnl >= 0 ? "pnl-positive" : "pnl-negative") : "";
    const pnlText = pnl != null ? formatPnl(pnl) : "OPEN";
    const pnlPct = t.pnl_pct != null ? formatPctRaw(t.pnl_pct) : "--";
    const exitPrice = t.exit_price ? formatPrice(t.exit_price) : "--";
    const reason = t.exit_reason || "--";

    let eventBadges = "";
    if (t.events) {
      for (const e of t.events) {
        eventBadges += `<span class="mini-badge">${e}</span>`;
      }
    }

    rows += `<tr>
      <td>${t.id ? t.id.slice(-8) : "--"}</td>
      <td class="${dirCls}">${t.direction}</td>
      <td>${formatPrice(t.entry_price)}</td>
      <td>${exitPrice}</td>
      <td class="${pnlCls}">${pnlText}</td>
      <td class="${pnlCls}">${pnlPct}</td>
      <td>${t.leverage || "--"}x</td>
      <td>${t.conviction != null ? (t.conviction * 100).toFixed(0) + "%" : "--"}</td>
      <td class="trade-events-cell">${eventBadges}</td>
      <td>${reason}</td>
    </tr>`;
  }

  el.innerHTML = `<table class="tradelog-table">
    <thead><tr>
      <th>ID</th><th>Dir</th><th>Entry</th><th>Exit</th><th>PnL $</th><th>PnL %</th><th>Lev</th><th>Conv</th><th>Events</th><th>Reason</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

// --- Main fetch loop ---
let fetchErrors = 0;

async function fetchData() {
  try {
    const res = await fetch("/api/data");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    fetchErrors = 0;

    updateStatsBar(data);
    updateChart(data);
    updateEventFeed(data);
    updatePosition(data);
    updateAgents(data);
    updatePatterns(data);
    updateOptimizer(data);
    updateTradeLog(data);

    // Update status indicator
    const statusEl = document.getElementById("ws-status");
    if (statusEl) {
      statusEl.querySelector("span:last-child").textContent = "LIVE";
      statusEl.querySelector(".pulse-dot").style.background = "var(--green)";
    }
  } catch (err) {
    fetchErrors++;
    console.error("[DASHBOARD] Fetch error:", err.message);
    const statusEl = document.getElementById("ws-status");
    if (statusEl && fetchErrors > 2) {
      statusEl.querySelector("span:last-child").textContent = "OFFLINE";
      statusEl.querySelector(".pulse-dot").style.background = "var(--red)";
    }
  }
}

// --- Init ---
document.addEventListener("DOMContentLoaded", () => {
  initCharts();
  initGrid();
  fetchData();
  setInterval(fetchData, 2000);
});
