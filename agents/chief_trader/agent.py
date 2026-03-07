import asyncio
import json
import time
from typing import Optional
import anthropic
from shared.redis_client import RedisClient
from shared.kelly import bet_size

STREAM = "decisions:chief"
DEFAULT_COOLDOWN = 60  # seconds between trades

SYSTEM_PROMPT = """You are Amir Hassan, Head of Trading at a Dubai-based crypto fund.
22 years of experience across forex, commodities, and crypto derivatives.
You are the final decision maker. You are disciplined, emotionless, and only trade when the team agrees.

You receive analysis from 4 expert agents:
- Kenji Tanaka (Technical): RSI, MACD, Bollinger, momentum, divergences
- Sarah Mitchell (Sentiment): Fear & Greed contrarian analysis
- Viktor Petrov (On-Chain): Funding rate, liquidations, smart money flows
- Diego Ramirez (Order Flow): Order book pressure, bid/ask imbalance

Your job: synthesize their views, resolve conflicts, and decide LONG or SHORT.

RESPOND ONLY WITH THIS JSON (no markdown, no extra text):
{
  "score": <int 0-100>,
  "direction": "<LONG|SHORT>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<2-3 sentences synthesizing the team's analysis and your decision>"
}

Rules:
- You MUST pick LONG or SHORT. Never neutral. When signals conflict, follow the majority.
- Score >60 = LONG conviction, <40 = SHORT conviction, 40-60 = weak signal (still pick a side)
- If 3+ agents agree on direction, confidence should be >0.7
- If agents are split 2-2, confidence should be <0.55 and follow the Technical agent
- Always mention which agents agree and which disagree in your reasoning"""


class ChiefTrader:
    def __init__(self, redis_url: str = "redis://localhost:6379",
                 fake_redis: bool = False, bankroll: float = 100.0,
                 cooldown: int = DEFAULT_COOLDOWN):
        self.rc = RedisClient(url=redis_url, fake=fake_redis)
        self.client = anthropic.AsyncAnthropic()
        self.bankroll = bankroll
        self.initial = bankroll
        self.consecutive_losses = 0
        self.last_trade_ts = 0.0
        self.cooldown = cooldown
        self.trades: list[dict] = []

    def check_circuit_breakers(self) -> Optional[dict]:
        if self.consecutive_losses >= 5:
            return {"action": "BLOCKED", "reason": f"Circuit breaker: {self.consecutive_losses} consecutive losses. Pause 1h."}
        drawdown = (self.initial - self.bankroll) / self.initial if self.initial > 0 else 0
        if drawdown >= 0.15:
            return {"action": "BLOCKED", "reason": f"Circuit breaker: drawdown {drawdown*100:.1f}% exceeds 15% limit."}
        return None

    async def synthesize(self, technical: dict, sentiment: dict,
                         onchain: dict, orderflow: dict) -> dict:
        prompt = f"""Agent Reports:

**Kenji Tanaka (Technical):**
Score: {technical.get('score', 50)}/100 | Direction: {technical.get('direction', 'neutral')}
Confidence: {technical.get('confidence', 0.5)} | ATR: {technical.get('atr', 100)}
Analysis: {technical.get('reasoning', 'No data')}

**Sarah Mitchell (Sentiment):**
Score: {sentiment.get('score', 50)}/100 | Direction: {sentiment.get('direction', 'neutral')}
Confidence: {sentiment.get('confidence', 0.5)} | F&G: {sentiment.get('fear_greed', 50)}
Analysis: {sentiment.get('reasoning', 'No data')}

**Viktor Petrov (On-Chain):**
Score: {onchain.get('score', 50)}/100 | Direction: {onchain.get('direction', 'neutral')}
Confidence: {onchain.get('confidence', 0.5)}
Analysis: {onchain.get('reasoning', 'No data')}

**Diego Ramirez (Order Flow):**
Score: {orderflow.get('score', 50)}/100 | Direction: {orderflow.get('direction', 'neutral')}
Confidence: {orderflow.get('confidence', 0.5)}
Analysis: {orderflow.get('reasoning', 'No data')}

Current bankroll: ${self.bankroll:.2f} | Consecutive losses: {self.consecutive_losses}"""

        try:
            response = await self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            return json.loads(response.content[0].text.strip())
        except Exception:
            return self._fallback_decision(technical, sentiment, onchain, orderflow)

    def _fallback_decision(self, tech: dict, sent: dict, chain: dict, flow: dict) -> dict:
        # Weighted score — technical and sentiment lead
        scores = {
            "tech": int(tech.get("score", 50)),
            "sent": int(sent.get("score", 50)),
            "chain": int(chain.get("score", 50)),
            "flow": int(flow.get("score", 50)),
        }
        score = int(
            0.35 * scores["tech"] +
            0.25 * scores["sent"] +
            0.20 * scores["chain"] +
            0.20 * scores["flow"]
        )
        score = max(0, min(100, score))

        # Count directional votes
        dirs = [tech.get("direction", "neutral"), sent.get("direction", "neutral"),
                chain.get("direction", "neutral"), flow.get("direction", "neutral")]
        up_count = sum(1 for d in dirs if d == "up")
        down_count = sum(1 for d in dirs if d == "down")

        # Determine direction — majority wins, ties go to technical
        if up_count > down_count:
            direction = "LONG"
            # Amplify bullish score — but don't invent conviction
            if score < 55:
                score = 55 + up_count * 3
        elif down_count > up_count:
            direction = "SHORT"
            if score > 45:
                score = 45 - down_count * 3
        else:
            # Tied — follow technical lead (highest weight agent)
            tech_dir = tech.get("direction", "neutral")
            if tech_dir == "up":
                direction = "LONG"
                score = max(score, 55)
            elif tech_dir == "down":
                direction = "SHORT"
                score = min(score, 45)
            else:
                # Technical neutral too — follow sentiment
                sent_dir = sent.get("direction", "neutral")
                direction = "LONG" if sent_dir == "up" else "SHORT"
                score = max(score, 53) if direction == "LONG" else min(score, 47)

        score = max(0, min(100, score))

        # Confidence based on agreement strength
        agreement = max(up_count, down_count)
        if agreement >= 3:
            confidence = 0.78
        elif agreement >= 2:
            confidence = 0.65
        else:
            confidence = 0.55  # still trade, just smaller

        return {
            "score": score,
            "direction": direction,
            "confidence": confidence,
            "reasoning": f"Fallback: {up_count} bullish, {down_count} bearish agents. Weighted score={score}. Confidence={confidence}."
        }

    def compute_trade(self, decision: dict, atr: float) -> dict:
        score = decision["score"]
        direction = decision["direction"]
        confidence = decision.get("confidence", 0.5)

        # Win prob — amplified from score + confidence
        if direction == "LONG":
            win_prob = 0.50 + (score - 50) / 80.0  # steeper curve
        else:
            win_prob = 0.50 + (50 - score) / 80.0
        # Boost from confidence
        win_prob = win_prob * 0.6 + confidence * 0.4
        win_prob = max(0.52, min(0.88, win_prob))  # always slightly above 50%

        stake = bet_size(self.bankroll, win_prob)

        return {
            "action": "TRADE",
            "direction": direction,
            "score": score,
            "win_prob": round(win_prob, 3),
            "confidence": round(confidence, 3),
            "stake": stake,
            "atr": atr,
            "trailing_stop_distance": round(atr * 0.8, 2),
            "reasoning": decision.get("reasoning", ""),
        }

    async def run(self):
        await self.rc.connect()
        try:
            while True:
                now = time.time()
                if now - self.last_trade_ts < self.cooldown:
                    await asyncio.sleep(1)
                    continue

                cb = self.check_circuit_breakers()
                if cb:
                    await self.rc.publish(STREAM, cb)
                    await asyncio.sleep(10)
                    continue

                tech = await self.rc.read_latest("signals:technical") or {"score": 50, "direction": "neutral", "atr": 100}
                sent = await self.rc.read_latest("signals:sentiment") or {"score": 50, "direction": "neutral"}
                chain = await self.rc.read_latest("signals:onchain") or {"score": 50, "direction": "neutral"}
                flow = await self.rc.read_latest("signals:orderflow") or {"score": 50, "direction": "neutral"}

                for d in [tech, sent, chain, flow]:
                    for k in ("score", "fear_greed"):
                        if k in d:
                            try: d[k] = int(d[k])
                            except (ValueError, TypeError): pass
                    for k in ("confidence", "atr", "funding_rate", "long_short_ratio", "ob_ratio"):
                        if k in d:
                            try: d[k] = float(d[k])
                            except (ValueError, TypeError): pass

                decision = await self.synthesize(tech, sent, chain, flow)
                atr = float(tech.get("atr", 100))
                trade = self.compute_trade(decision, atr)
                trade["technical"] = tech
                trade["sentiment"] = sent
                trade["onchain"] = chain
                trade["orderflow"] = flow
                await self.rc.publish(STREAM, trade)
                self.last_trade_ts = now
                await asyncio.sleep(1)
        finally:
            await self.rc.close()
