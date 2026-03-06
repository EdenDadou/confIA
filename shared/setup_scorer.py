"""
Setup Quality Scorer — Only trade when conditions are strong.
Replaces blind 60s-interval trading with intelligent entry detection.
"""


class SetupScorer:
    def __init__(self, min_quality: float = 0.55, min_wait_seconds: int = 120):
        self.min_quality = min_quality
        self.min_wait_seconds = min_wait_seconds

    def evaluate(self, tech: dict, sent: dict, chain: dict, flow: dict,
                 seconds_since_last_trade: float = float("inf")) -> dict:
        # Cooldown check
        if seconds_since_last_trade < self.min_wait_seconds:
            return {"should_trade": False, "quality": 0, "direction": None,
                    "wait_reason": "cooldown"}

        # Direction consensus
        dirs = []
        for agent in [tech, sent, chain, flow]:
            d = agent.get("direction", "neutral")
            if d in ("up", "LONG"):
                dirs.append("up")
            elif d in ("down", "SHORT"):
                dirs.append("down")
            else:
                dirs.append("neutral")

        up_count = dirs.count("up")
        down_count = dirs.count("down")

        # Determine direction
        if up_count > down_count:
            direction = "LONG"
            aligned_count = up_count
        elif down_count > up_count:
            direction = "SHORT"
            aligned_count = down_count
        else:
            # Tied — follow technical
            td = tech.get("direction", "neutral")
            direction = "LONG" if td in ("up", "LONG") else "SHORT"
            aligned_count = max(up_count, down_count)

        # Score components (0-1 each)
        # 1. Agreement strength (0-1): how many agents agree
        agreement = aligned_count / 4.0

        # 2. Conviction strength: how far scores are from 50
        scores = [int(tech.get("score", 50)), int(sent.get("score", 50)),
                  int(chain.get("score", 50)), int(flow.get("score", 50))]
        if direction == "LONG":
            conviction = sum(max(0, s - 50) for s in scores) / (50.0 * 4)
        else:
            conviction = sum(max(0, 50 - s) for s in scores) / (50.0 * 4)

        # 3. Average confidence from agents
        confidences = [float(a.get("confidence", 0.5)) for a in [tech, sent, chain, flow]]
        avg_confidence = sum(confidences) / len(confidences)

        # Weighted quality score
        quality = round(
            0.40 * agreement +
            0.35 * conviction +
            0.25 * avg_confidence,
            4
        )

        should_trade = quality >= self.min_quality and aligned_count >= 2

        return {
            "should_trade": should_trade,
            "quality": quality,
            "direction": direction,
            "agreement": aligned_count,
            "conviction": round(conviction, 4),
            "avg_confidence": round(avg_confidence, 4),
            "wait_reason": None,
        }
