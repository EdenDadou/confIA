import type { Candle } from "../engine/indicators.js";
import type { TFIndicators } from "../engine/event-detector.js";
import { computeRSI, computeEMA, computeMACD, computeBollinger, computeATR, computeVolumeSMA } from "../engine/indicators.js";

export class TechnicalAgent {
  analyze(candles: Candle[]): TFIndicators {
    const closes = candles.map((c) => c.close);
    const volumes = candles.map((c) => c.volume);
    const price = closes[closes.length - 1] ?? 0;
    const rsiArr = computeRSI(closes, 14);
    const rsi = rsiArr.length > 0 ? rsiArr[rsiArr.length - 1] : 50;
    const ema12Arr = computeEMA(closes, 12);
    const ema12 = ema12Arr[ema12Arr.length - 1] ?? price;
    const ema26Arr = computeEMA(closes, 26);
    const ema26 = ema26Arr[ema26Arr.length - 1] ?? price;
    const macd = computeMACD(closes);
    const macdHist = macd.histogram[macd.histogram.length - 1] ?? 0;
    const bbArr = computeBollinger(closes, 20, 2);
    const bb = bbArr.length > 0 ? bbArr[bbArr.length - 1] : { upper: price + 1, middle: price, lower: price - 1 };
    const volumeSma = computeVolumeSMA(volumes, 20);
    const currentVol = volumes[volumes.length - 1] ?? 0;
    return { rsi, ema12, ema26, macd_hist: macdHist, bb, volume: currentVol, volumeSma, price };
  }

  computeATR(candles: Candle[]): number { return computeATR(candles, 14); }
}
