"""
Setup Quality Scorer — Only trade when conditions are strong.
Replaces blind 60s-interval trading with intelligent entry detection.
"""


class SetupScorer:
    def __init__(self, min_quality: float = 0.40, min_wait_seconds: int = 60):
        self.min_quality = min_quality
        self.min_wait_seconds = min_wait_seconds

    def evaluate(self, tech: dict, sent: dict, chain: dict, flow: dict,
                 seconds_since_last_trade: float = float("inf")) -> dict:
        # Cooldown check
        if seconds_since_last_trade < self.min_wait_seconds:
            return {"should_trade": False, "quality": 0, "direction": None,
                    "wait_reason": "cooldown"}

        # Direction consensus — neutrals lean toward majority
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
        neutral_count = dirs.count("neutral")

        # Determine direction
        if up_count > down_count:
            direction = "LONG"
            aligned_count = up_count
        elif down_count > up_count:
            direction = "SHORT"
            aligned_count = down_count
        else:
            # Tied — follow technical agent's score
            ts = int(tech.get("score", 50))
            direction = "LONG" if ts >= 50 else "SHORT"
            aligned_count = max(up_count, down_count)

        # Neutrals leaning toward consensus count as half-agreement
        effective_agreement = aligned_count + neutral_count * 0.4

        # Score components (0-1 each)
        # 1. Agreement strength — boosted by neutral lean
        agreement = min(1.0, effective_agreement / 4.0)

        # 2. Conviction strength: how far scores are from 50
        scores = [int(tech.get("score", 50)), int(sent.get("score", 50)),
                  int(chain.get("score", 50)), int(flow.get("score", 50))]
        if direction == "LONG":
            conviction = sum(max(0, s - 45) for s in scores) / (55.0 * 4)
        else:
            conviction = sum(max(0, 55 - s) for s in scores) / (55.0 * 4)
        conviction = min(1.0, conviction)

        # 3. Average confidence from agents
        confidences = [float(a.get("confidence", 0.5)) for a in [tech, sent, chain, flow]]
        avg_confidence = sum(confidences) / len(confidences)

        # Weighted quality score — conviction matters most for a trader with grinta
        quality = round(
            0.30 * agreement +
            0.40 * conviction +
            0.30 * avg_confidence,
            4
        )

        # Trade if quality passes AND at least 2 agents have an opinion (not all neutral)
        should_trade = quality >= self.min_quality and (up_count + down_count) >= 2

        return {
            "should_trade": should_trade,
            "quality": quality,
            "direction": direction,
            "agreement": aligned_count,
            "conviction": round(conviction, 4),
            "avg_confidence": round(avg_confidence, 4),
            "wait_reason": None,
        }
