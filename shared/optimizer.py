"""
Parameter Optimizer — learns from trade outcomes to adjust trading parameters.
Simple exponential moving average approach — no heavy ML, just adaptive thresholds.
"""


class ParameterOptimizer:
    def __init__(self):
        self._outcomes: list[dict] = []
        self._params = {
            "min_quality": 0.55,
            "stop_multiplier": 0.8,
            "agent_weights": {"tech": 0.35, "sent": 0.20, "chain": 0.20, "flow": 0.25},
            "max_stake_pct": 0.25,
            "min_agreement": 2,
        }
        self._alpha = 0.1  # learning rate

    def record_outcome(self, quality: float, direction: str, won: bool,
                       pnl: float, score: int, agent_agreement: int,
                       stop_multiplier: float):
        self._outcomes.append({
            "quality": quality, "direction": direction, "won": won,
            "pnl": pnl, "score": score, "agreement": agent_agreement,
            "stop_mult": stop_multiplier,
        })
        self._update_params()

    def _update_params(self):
        if len(self._outcomes) < 5:
            return
        recent = self._outcomes[-20:]  # last 20 trades
        wins = [o for o in recent if o["won"]]
        losses = [o for o in recent if not o["won"]]
        win_rate = len(wins) / len(recent)

        # Adjust min_quality: losing too much -> be more selective
        if win_rate < 0.45:
            self._params["min_quality"] = min(0.75,
                self._params["min_quality"] + self._alpha * 0.05)
        elif win_rate > 0.60:
            self._params["min_quality"] = max(0.40,
                self._params["min_quality"] - self._alpha * 0.03)

        # Adjust stop multiplier: if stops are too tight (many trailing_stop losses)
        stop_losses = [o for o in losses if o.get("close_reason") == "trailing_stop"]
        if len(losses) > 0 and len(stop_losses) / max(len(losses), 1) > 0.6:
            self._params["stop_multiplier"] = min(1.2,
                self._params["stop_multiplier"] + self._alpha * 0.1)
        elif win_rate > 0.55:
            self._params["stop_multiplier"] = max(0.6,
                self._params["stop_multiplier"] - self._alpha * 0.05)

        # Adjust max stake: losing streak -> reduce risk
        if win_rate < 0.40:
            self._params["max_stake_pct"] = max(0.10,
                self._params["max_stake_pct"] - self._alpha * 0.02)
        elif win_rate > 0.55:
            self._params["max_stake_pct"] = min(0.30,
                self._params["max_stake_pct"] + self._alpha * 0.01)

    def get_params(self) -> dict:
        return dict(self._params)

    def export_state(self) -> dict:
        return {"params": dict(self._params), "outcomes": list(self._outcomes)}

    def load_state(self, data: dict):
        self._params = data.get("params", self._params)
        self._outcomes = data.get("outcomes", [])
