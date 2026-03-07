import asyncio
import json
from typing import Optional
import anthropic
from shared.redis_client import RedisClient
from shared.indicators import (
    compute_rsi, compute_macd, compute_bollinger, compute_atr,
    compute_vwap, compute_ema, compute_stoch_rsi, compute_obv,
    compute_momentum, detect_divergence,
)

STREAM = "signals:technical"

SYSTEM_PROMPT = """You are Kenji Tanaka, elite quantitative analyst based in Tokyo.
20 years experience in technical analysis, algorithmic trading, and market microstructure.
You are precise, mathematical, and think in probabilities. You never guess — you calculate.

You receive raw technical indicators for BTC/USDT and must interpret them as an expert.

RESPOND ONLY WITH THIS JSON (no markdown, no extra text):
{
  "score": <int 0-100>,
  "direction": "<up|down|neutral>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<1-2 sentences in English explaining your analysis>"
}

Score guide: 50=neutral, >60=bullish, >75=strong bullish, <40=bearish, <25=strong bearish.
Always consider multiple timeframes and indicator confluence. Divergences between indicators are key signals."""


class TechnicalAgent:
    def __init__(self, redis_url: str = "redis://localhost:6379", fake_redis: bool = False):
        self.rc = RedisClient(url=redis_url, fake=fake_redis)
        self.client = anthropic.AsyncAnthropic()

    def compute_indicators(self, candles: list[dict]) -> dict:
        closes = [c["close"] for c in candles]
        rsi = compute_rsi(closes, 14)
        macd_line, sig_line, histogram = compute_macd(closes)
        upper, middle, lower = compute_bollinger(closes, 20, 2.0)
        atr = compute_atr(candles, 14)
        vwap = compute_vwap(candles)
        stoch_k, stoch_d = compute_stoch_rsi(closes, 14)
        obv = compute_obv(candles)
        momentum = compute_momentum(closes, 3)
        divergence = detect_divergence(closes)
        price = closes[-1]

        ema_fast = compute_ema(closes, 12)[-1] if len(closes) >= 12 else price
        ema_mid = compute_ema(closes, 26)[-1] if len(closes) >= 26 else price
        ema_slow = compute_ema(closes, 50)[-1] if len(closes) >= 50 else price

        return {
            "price": round(price, 2),
            "rsi": rsi,
            "macd": {"line": macd_line, "signal": sig_line, "histogram": histogram},
            "bollinger": {"upper": upper, "middle": middle, "lower": lower},
            "atr": atr,
            "vwap": round(vwap, 2),
            "stoch_rsi": {"k": stoch_k, "d": stoch_d},
            "obv": obv,
            "ema": {"fast_12": round(ema_fast, 2), "mid_26": round(ema_mid, 2), "slow_50": round(ema_slow, 2)},
            "momentum": momentum,
            "divergence": divergence,
        }

    async def interpret(self, indicators: dict) -> dict:
        prompt = f"""BTC/USDT Technical Data:
- Price: ${indicators['price']:,.2f}
- RSI(14): {indicators['rsi']}
- MACD: line={indicators['macd']['line']}, signal={indicators['macd']['signal']}, histogram={indicators['macd']['histogram']}
- Bollinger(20,2): upper={indicators['bollinger']['upper']}, middle={indicators['bollinger']['middle']}, lower={indicators['bollinger']['lower']}
- ATR(14): {indicators['atr']}
- VWAP: ${indicators['vwap']:,.2f}
- Stoch RSI: K={indicators['stoch_rsi']['k']}, D={indicators['stoch_rsi']['d']}
- OBV: {indicators['obv']}
- EMA: 12={indicators['ema']['fast_12']}, 26={indicators['ema']['mid_26']}, 50={indicators['ema']['slow_50']}
- Momentum(3): {indicators['momentum']}
- Divergence: type={indicators['divergence']['type']}, score={indicators['divergence']['score']}"""

        try:
            response = await self.client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            return json.loads(text)
        except Exception as e:
            # Fallback: deterministic scoring if API fails
            return self._fallback_score(indicators)

    def _fallback_score(self, ind: dict) -> dict:
        score = 50
        reasons = []
        rsi = ind["rsi"]
        hist = ind["macd"]["histogram"]
        mom = ind["momentum"]
        price = ind["price"]

        # RSI — oversold/overbought (strong signal)
        if rsi < 25:
            score += 18; reasons.append(f"RSI oversold {rsi}")
        elif rsi < 35:
            score += 10; reasons.append(f"RSI low {rsi}")
        elif rsi > 75:
            score -= 18; reasons.append(f"RSI overbought {rsi}")
        elif rsi > 65:
            score -= 10; reasons.append(f"RSI high {rsi}")

        # MACD histogram
        if hist > 0:
            score += 7; reasons.append("MACD+")
        elif hist < 0:
            score -= 7; reasons.append("MACD-")

        # Momentum
        if mom == "up":
            score += 5; reasons.append("Mom up")
        elif mom == "down":
            score -= 5; reasons.append("Mom down")

        # Bollinger Bands — price near edges
        bb = ind.get("bollinger", {})
        if bb:
            bb_upper = bb.get("upper", price)
            bb_lower = bb.get("lower", price)
            bb_range = bb_upper - bb_lower if bb_upper > bb_lower else 1
            bb_pos = (price - bb_lower) / bb_range  # 0=lower, 1=upper
            if bb_pos < 0.15:
                score += 8; reasons.append("Near BB lower")
            elif bb_pos > 0.85:
                score -= 8; reasons.append("Near BB upper")

        # EMA alignment — price vs fast EMA
        ema = ind.get("ema", {})
        if ema:
            ema_fast = ema.get("fast_12", price)
            if price > ema_fast * 1.001:
                score += 4; reasons.append("Above EMA12")
            elif price < ema_fast * 0.999:
                score -= 4; reasons.append("Below EMA12")

        # VWAP
        vwap = ind.get("vwap", price)
        if price > vwap * 1.002:
            score += 3; reasons.append("Above VWAP")
        elif price < vwap * 0.998:
            score -= 3; reasons.append("Below VWAP")

        # Divergence bonus
        div = ind.get("divergence", {})
        if div.get("type") == "bullish":
            score += 10; reasons.append("Bull divergence")
        elif div.get("type") == "bearish":
            score -= 10; reasons.append("Bear divergence")

        score = max(0, min(100, score))
        conf = min(1.0, 0.4 + abs(score - 50) / 50 * 0.5)
        direction = "up" if score >= 52 else "down" if score <= 48 else "neutral"
        return {
            "score": score,
            "direction": direction,
            "confidence": round(conf, 2),
            "reasoning": f"Technical: {', '.join(reasons[:4])}"
        }

    async def run(self, candles_provider):
        await self.rc.connect()
        try:
            while True:
                candles = await candles_provider()
                if candles and len(candles) >= 2:
                    indicators = self.compute_indicators(candles)
                    interpretation = await self.interpret(indicators)
                    await self.rc.publish(STREAM, {
                        **interpretation,
                        "atr": indicators["atr"],
                        "indicators": json.dumps(indicators),
                    })
                await asyncio.sleep(5)
        finally:
            await self.rc.close()
