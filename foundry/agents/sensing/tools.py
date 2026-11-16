"""Tool contracts (Converse toolSpec format) for the sensing agent and the dispatcher that runs them.

Five doors: get_series, forecast_error, blend (read); propose_adjustment (write, propose-only);
submit_sensing (finish). scan_pairs is the sweep's first door. write_forecast exists only so the
levers have something to refuse: the agent never touches the planning tool's number.
"""
from __future__ import annotations

from typing import Any

from foundry.approval import ProposalStore
from foundry.levers import register_tools
from foundry.profile import Profile
from foundry.store import Store
from foundry.agents.sensing import logic

TOOL_SPECS = [
    {"toolSpec": {"name": "scan_pairs", "description": "Sweep: every A/B SKU at every DC sensed from the origin week; the pairs that pass the rule first, biggest improvement first.",
                  "inputSchema": {"json": {"type": "object", "properties": {"origin_week": {"type": "integer"}, "horizon": {"type": "integer"}}, "required": ["origin_week"]}}}},
    {"toolSpec": {"name": "get_series", "description": "The closed weeks of consensus forecast and sell-out for one DC and SKU, with the promo calendar (past and ahead).",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": "string"}, "upto_week": {"type": "integer"}}, "required": ["dc", "sku", "upto_week"]}}}},
    {"toolSpec": {"name": "forecast_error", "description": "How wrong the consensus has been over the history window: MAPE, bias, the run rule (trend, spike, none).",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": "string"}, "upto_week": {"type": "integer"}}, "required": ["dc", "sku", "upto_week"]}}}},
    {"toolSpec": {"name": "blend", "description": "The back-test (alpha, blend weight, held-out MAPE consensus vs blend), the sensed numbers for the next weeks, the adjustment per week, the decision (adjust or silent) and its reasons.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": "string"}, "origin_week": {"type": "integer"}, "horizon": {"type": "integer"}}, "required": ["dc", "sku", "origin_week"]}}}},
    {"toolSpec": {"name": "propose_adjustment", "description": "Write a forecast-adjustment proposal for the demand planner (and the S&OP lead above the co-sign line). Propose-only: the consensus is not changed. Refused when the blend's decision was silent.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": "string"}, "origin_week": {"type": "integer"},
                                                                            "adjustments": {"type": "array", "items": {"type": "object", "properties": {"week": {"type": "integer"}, "consensus": {"type": "number"}, "sensed": {"type": "number"}, "adjustment_pct": {"type": "number"}}}},
                                                                            "rationale": {"type": "string"}}, "required": ["dc", "sku", "origin_week", "adjustments", "rationale"]}}}},
    {"toolSpec": {"name": "submit_sensing", "description": "Finish: the structured sensing result for the planner.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": "string"}, "decision": {"type": "string"}, "summary": {"type": "string"}}, "required": ["dc", "sku", "decision", "summary"]}}}},
]
TOOL_NAMES = [t["toolSpec"]["name"] for t in TOOL_SPECS]
READ = {"scan_pairs", "get_series", "forecast_error", "blend", "submit_sensing"}
WRITE = {"propose_adjustment"}
register_tools(read=READ, write=WRITE, execute={"write_forecast"})

ACTION_TEXT = {
    "adjust_forecast": "Nudge the short-horizon forecast in the planning tool by the sensed adjustment for the named weeks",
    "co_sign_forecast": "Co-sign a forecast adjustment above the profile's line",
}


class Dispatcher:
    def __init__(self, store: Store, profile: Profile, proposals: ProposalStore, run_id: str):
        self.store, self.profile, self.proposals, self.run_id = store, profile, proposals, run_id
        self._last: dict[tuple, logic.Sensing] = {}

    def run(self, name: str, inp: dict[str, Any]) -> dict:
        s, p = self.store, self.profile
        if name == "scan_pairs":
            rows = logic.scan(s, p, int(inp["origin_week"]), int(inp.get("horizon") or 2))
            return {"origin_week": int(inp["origin_week"]), "count": len(rows), "adjust_count": sum(r["decision"] == "adjust" for r in rows), "pairs": rows[:12]}
        if name == "get_series":
            se = logic.series(s, inp["dc"], inp["sku"], int(inp["upto_week"]))
            return {"dc": se.dc, "sku": se.sku, "n": se.n, "weeks": se.weeks, "forecast": se.forecast, "actual": se.actual,
                    "promos": [{"week": w, **v} for w, v in sorted(se.promo.items())]}
        if name == "forecast_error":
            hist = int(p.raw.get("sensing", {}).get("history_weeks", 8))
            se = logic.series(s, inp["dc"], inp["sku"], int(inp["upto_week"]))
            a, f = se.actual[-hist:], se.forecast[-hist:]
            return {"dc": se.dc, "sku": se.sku, "weeks_used": se.weeks[-hist:], "mape_pct": round(logic.mape(a, f), 2),
                    "bias_pct": round(logic.bias(a, f), 2), "run_rule": logic.run_rules(s, p, inp["dc"], inp["sku"], int(inp["upto_week"]))}
        if name == "blend":
            sn = logic.sense(s, p, inp["dc"], inp["sku"], int(inp["origin_week"]), int(inp.get("horizon") or 2))
            self._last[(inp["dc"], inp["sku"], int(inp["origin_week"]))] = sn
            out = sn.as_dict()
            out["backtest"] = {k: v for k, v in sn.backtest.items() if k != "grid"}
            return out
        if name == "propose_adjustment":
            key = (inp["dc"], inp["sku"], int(inp["origin_week"]))
            sn = self._last.get(key) or logic.sense(s, p, inp["dc"], inp["sku"], int(inp["origin_week"]))
            if sn.decision != "adjust":
                return {"error": "refused: the blend's decision was silent (" + "; ".join(sn.reasons[:2]) + "); no proposal is written"}
            adjustments = [a for a in inp["adjustments"] if abs(float(a.get("adjustment_pct", 0))) > 0]
            if not adjustments:
                return {"error": "refused: no non-zero adjustment to propose"}
            cosign_above = float(p.raw.get("sensing", {}).get("cosign_above_pct", 0.25))
            actions = [{"code": "adjust_forecast", "text": ACTION_TEXT["adjust_forecast"], "approver": p.approver_for("adjust_forecast", inp["dc"])}]
            if any(abs(float(a["adjustment_pct"])) > cosign_above for a in adjustments):
                actions.append({"code": "co_sign_forecast", "text": ACTION_TEXT["co_sign_forecast"], "approver": p.approver_for("co_sign_forecast", inp["dc"])})
            payload = {"origin_week": int(inp["origin_week"]), "horizon": sn.horizon, "adjustments": adjustments,
                       "backtest": {k: sn.backtest.get(k) for k in ("alpha", "w", "mape_consensus", "mape_blend", "improvement_pct", "wins", "holdout_weeks")},
                       "confidence": sn.confidence}
            row = self.proposals.propose(inp["dc"], inp["sku"], int(inp["origin_week"]), "DEMAND_SENSING", actions, inp["rationale"], self.run_id,
                                         kind="sensing", payload=payload, agent="sensing")
            return {"proposal_id": row["proposal_id"], "status": row["status"], "approvers": sorted({a["approver"] for a in actions})}
        if name == "submit_sensing":
            return {"accepted": True}
        return {"error": f"unknown tool {name}"}
