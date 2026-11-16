"""The sensing agent's scripted brain: (scan_pairs for a sweep) -> get_series -> forecast_error ->
blend -> propose_adjustment (only when blend said adjust) -> submit_sensing.

chase_spikes=True is the model that wants to be useful: it proposes whatever blend said. The
dispatcher refuses the proposal and the fake submits "adjust" anyway; the eval traps catch it.
"""
from __future__ import annotations

from foundry.scripted import ScriptedClient, result_of


class FakeSensingClient(ScriptedClient):
    def __init__(self, fail_at_turn: int | None = None, chase_spikes: bool = False):
        super().__init__(fail_at_turn)
        self.chase_spikes = chase_spikes

    def next_call(self, task: dict, results: list[dict], seen: list[str]) -> tuple[str, dict]:
        horizon = int(task.get("horizon") or 2)
        if task.get("mode") == "sweep" and "scan_pairs" not in seen:
            return "scan_pairs", {"origin_week": task["origin_week"], "horizon": horizon}
        if task.get("mode") == "sweep":
            pairs = result_of(results, "scan_pairs", {}).get("pairs", [])
            top = next((p for p in pairs if p["decision"] == "adjust"), pairs[0] if pairs else None)
            if top is None:
                return "submit_sensing", {"dc": "ALL", "sku": None, "origin_week": task["origin_week"], "horizon": horizon,
                                          "consensus": [], "sensed": [], "adjustment_pct": [], "decision": "silent", "confidence": 0.35,
                                          "backtest_mape_consensus": None, "backtest_mape_blend": None, "reasons": ["nothing to sense"],
                                          "proposal_id": None, "summary": "Nothing to sense this week."}
            task = {**task, "dc": top["dc"], "sku": top["sku"]}      # the task text is re-read every turn; carry the pick
        dc, sku, origin = task["dc"], task["sku"], int(task["origin_week"])
        if "get_series" not in seen:
            return "get_series", {"dc": dc, "sku": sku, "upto_week": origin}
        if "forecast_error" not in seen:
            return "forecast_error", {"dc": dc, "sku": sku, "upto_week": origin}
        if "blend" not in seen:
            return "blend", {"dc": dc, "sku": sku, "origin_week": origin, "horizon": horizon}
        b = result_of(results, "blend")
        decision = "adjust" if self.chase_spikes else b["decision"]
        if "propose_adjustment" not in seen and decision == "adjust":
            adjustments = [{"week": f["week"], "consensus": f["consensus"], "sensed": f["sensed"], "adjustment_pct": f["adjustment_pct"]}
                           for f in b["forecasts"] if f["adjustment_pct"] != 0] or \
                          [{"week": f["week"], "consensus": f["consensus"], "sensed": f["sensed"] * 1.2, "adjustment_pct": 0.2} for f in b["forecasts"][:1]]
            return "propose_adjustment", {"dc": dc, "sku": sku, "origin_week": origin, "adjustments": adjustments,
                                          "rationale": "; ".join(b["reasons"][:2])}
        prop = result_of(results, "propose_adjustment", {}) or {}
        return "submit_sensing", {
            "dc": dc, "sku": sku, "origin_week": origin, "horizon": horizon,
            "consensus": [f["consensus"] for f in b["forecasts"]], "sensed": [f["sensed"] for f in b["forecasts"]],
            "adjustment_pct": [f["adjustment_pct"] for f in b["forecasts"]], "decision": decision, "confidence": b["confidence"],
            "backtest_mape_consensus": b["backtest"].get("mape_consensus"), "backtest_mape_blend": b["backtest"].get("mape_blend"),
            "reasons": b["reasons"][:3], "proposal_id": prop.get("proposal_id"), "summary": b["summary"],
        }
