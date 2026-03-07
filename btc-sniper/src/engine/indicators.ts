export interface Candle {
  time: number; open: number; high: number; low: number; close: number; volume: number;
}

export interface BollingerBand { upper: number; middle: number; lower: number; }
export interface MACDResult { line: number[]; signal: number[]; histogram: number[]; }
export interface DivergenceResult { type: "bullish" | "bearish" | "none"; strength: number; }

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
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const delta = closes[i] - closes[i - 1];
    if (delta > 0) avgGain += delta; else avgLoss += Math.abs(delta);
  }
  avgGain /= period; avgLoss /= period;
  results.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
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

export function computeMACD(closes: number[], fast = 12, slow = 26, signal = 9): MACDResult {
  const emaFast = computeEMA(closes, fast);
  const emaSlow = computeEMA(closes, slow);
  const line = emaFast.map((v, i) => v - emaSlow[i]);
  const signalLine = computeEMA(line, signal);
  const histogram = line.map((v, i) => v - signalLine[i]);
  return { line, signal: signalLine, histogram };
}

export function computeBollinger(closes: number[], period = 20, stdDev = 2): BollingerBand[] {
  const results: BollingerBand[] = [];
  for (let i = period - 1; i < closes.length; i++) {
    const window = closes.slice(i - period + 1, i + 1);
    const sma = window.reduce((a, b) => a + b, 0) / period;
    const variance = window.reduce((sum, v) => sum + (v - sma) ** 2, 0) / period;
    const std = Math.sqrt(variance);
    results.push({ upper: sma + stdDev * std, middle: sma, lower: sma - stdDev * std });
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
  let atr = trs.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = period; i < trs.length; i++) {
    atr = (atr * (period - 1) + trs[i]) / period;
  }
  return atr;
}

export function computeOBV(candles: Candle[]): number[] {
  const obv = [0];
  for (let i = 1; i < candles.length; i++) {
    if (candles[i].close > candles[i - 1].close) obv.push(obv[i - 1] + candles[i].volume);
    else if (candles[i].close < candles[i - 1].close) obv.push(obv[i - 1] - candles[i].volume);
    else obv.push(obv[i - 1]);
  }
  return obv;
}

export function computeVolumeSMA(volumes: number[], period = 20): number {
  if (volumes.length < period) return volumes.reduce((a, b) => a + b, 0) / volumes.length;
  const recent = volumes.slice(-period);
  return recent.reduce((a, b) => a + b, 0) / period;
}

export function detectDivergence(priceLows: number[], rsiLows: number[]): DivergenceResult {
  if (priceLows.length < 2 || rsiLows.length < 2) return { type: "none", strength: 0 };
  const lastPrice = priceLows[priceLows.length - 1], prevPrice = priceLows[priceLows.length - 2];
  const lastRSI = rsiLows[rsiLows.length - 1], prevRSI = rsiLows[rsiLows.length - 2];
  if (lastPrice < prevPrice && lastRSI > prevRSI) {
    return { type: "bullish", strength: Math.min(1, Math.abs(lastRSI - prevRSI) / 20) };
  }
  if (lastPrice > prevPrice && lastRSI < prevRSI) {
    return { type: "bearish", strength: Math.min(1, Math.abs(lastRSI - prevRSI) / 20) };
  }
  return { type: "none", strength: 0 };
}
