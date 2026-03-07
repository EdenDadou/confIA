"""
Parameter Optimizer — learns from trade outcomes to adjust trading parameters.
Tracks which agents predicted correctly and adjusts their weights.
"""


class ParameterOptimizer:
    def __init__(self):
        self._outcomes: list[dict] = []
        self._params = {
            "min_quality": 0.40,
            "stop_multiplier": 0.8,
            "agent_weights": {"tech": 0.35, "sent": 0.20, "chain": 0.20, "flow": 0.25},
            "max_stake_pct": 0.25,
            "min_agreement": 2,
        }
        self._alpha = 0.1  # learning rate

    def record_outcome(self, quality: float, direction: str, won: bool,
                       pnl: float, score: int, agent_agreement: int,
                       stop_multiplier: float, agent_directions: dict = None):
        self._outcomes.append({
            "quality": quality, "direction": direction, "won": won,
            "pnl": pnl, "score": score, "agreement": agent_agreement,
            "stop_mult": stop_multiplier,
            "agent_dirs": agent_directions or {},
        })
        self._update_params()

    def _update_params(self):
        if len(self._outcomes) < 5:
            return
        recent = self._outcomes[-20:]  # last 20 trades
        wins = [o for o in recent if o["won"]]
        losses = [o for o in recent if not o["won"]]
        win_rate = len(wins) / len(recent)

        # ── 1. Adjust min_quality: losing too much -> be more selective ──
        if win_rate < 0.45:
            self._params["min_quality"] = min(0.55,
                self._params["min_quality"] + self._alpha * 0.05)
        elif win_rate > 0.60:
            self._params["min_quality"] = max(0.30,
                self._params["min_quality"] - self._alpha * 0.03)

        # ── 2. Adjust stop multiplier based on outcomes ──
        # If winning trades had higher PnL when stops were wider, widen
        avg_win_pnl = sum(o["pnl"] for o in wins) / len(wins) if wins else 0
        avg_loss_pnl = abs(sum(o["pnl"] for o in losses) / len(losses)) if losses else 0
        if avg_loss_pnl > 0 and avg_win_pnl / max(avg_loss_pnl, 0.01) < 1.2:
            # Risk/reward too low — tighten stops to cut losses faster
            self._params["stop_multiplier"] = max(0.5,
                self._params["stop_multiplier"] - self._alpha * 0.05)
        elif win_rate > 0.50 and avg_win_pnl > avg_loss_pnl * 1.5:
            # Good R/R — widen stops to let winners run more
            self._params["stop_multiplier"] = min(1.5,
                self._params["stop_multiplier"] + self._alpha * 0.05)

        # ── 3. Adjust max stake: losing streak -> reduce risk ──
        if win_rate < 0.40:
            self._params["max_stake_pct"] = max(0.10,
                self._params["max_stake_pct"] - self._alpha * 0.02)
        elif win_rate > 0.55:
            self._params["max_stake_pct"] = min(0.30,
                self._params["max_stake_pct"] + self._alpha * 0.01)

        # ── 4. Learn agent weights — reward agents who predicted correctly ──
        self._update_agent_weights(recent)

    def _update_agent_weights(self, recent: list[dict]):
        """Track each agent's accuracy and adjust weights accordingly."""
        agent_keys = ["tech", "sent", "chain", "flow"]
        agent_correct = {k: 0 for k in agent_keys}
        agent_total = {k: 0 for k in agent_keys}

        for outcome in recent:
            dirs = outcome.get("agent_dirs", {})
            if not dirs:
                continue
            trade_dir = outcome["direction"]
            won = outcome["won"]
            for key in agent_keys:
                agent_dir = dirs.get(key, "neutral")
                if agent_dir == "neutral":
                    continue
                agent_total[key] += 1
                # Agent was "correct" if: agreed with direction AND trade won,
                # OR disagreed AND trade lost
                agreed = (
                    (trade_dir == "LONG" and agent_dir in ("up", "LONG")) or
                    (trade_dir == "SHORT" and agent_dir in ("down", "SHORT"))
                )
                if (agreed and won) or (not agreed and not won):
                    agent_correct[key] += 1

        # Only adjust if we have enough data (at least 8 trades with agent data)
        total_with_data = sum(1 for o in recent if o.get("agent_dirs"))
        if total_with_data < 8:
            return

        # Compute accuracy per agent
        accuracies = {}
        for key in agent_keys:
            if agent_total[key] >= 5:
                accuracies[key] = agent_correct[key] / agent_total[key]
            else:
                accuracies[key] = 0.5  # not enough data, assume neutral

        # Convert to weights — better accuracy = higher weight
        total_acc = sum(accuracies.values())
        if total_acc > 0:
            new_weights = {k: round(v / total_acc, 3) for k, v in accuracies.items()}
            # Blend toward new weights slowly (alpha)
            for k in agent_keys:
                old = self._params["agent_weights"].get(k, 0.25)
                self._params["agent_weights"][k] = round(
                    old * (1 - self._alpha) + new_weights[k] * self._alpha, 4
                )
            # Normalize to sum to 1.0
            w_sum = sum(self._params["agent_weights"].values())
            if w_sum > 0:
                self._params["agent_weights"] = {
                    k: round(v / w_sum, 4)
                    for k, v in self._params["agent_weights"].items()
                }

    def get_params(self) -> dict:
        return dict(self._params)

    def export_state(self) -> dict:
        return {"params": dict(self._params), "outcomes": list(self._outcomes)}

    def load_state(self, data: dict):
        self._params = data.get("params", self._params)
        self._outcomes = data.get("outcomes", [])
