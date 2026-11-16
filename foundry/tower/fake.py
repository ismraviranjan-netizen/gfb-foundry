"""The tower's scripted brain: the method's order, nothing else. skip_specialists=True ignores the
route and merges the diagnosis alone (the master that stopped listening; the evals catch it)."""
from __future__ import annotations

from foundry.notify.coworker import code as cur
from foundry.scripted import ScriptedClient, result_of


class FakeTowerClient(ScriptedClient):
    def __init__(self, fail_at_turn: int | None = None, skip_specialists: bool = False):
        super().__init__(fail_at_turn)
        self.skip_specialists = skip_specialists

    def next_call(self, task, results, seen):
        week = int(task["week"])
        if task.get("mode") == "sweep" and "list_breaches" not in seen:
            return "list_breaches", {"week": week}
        if task.get("mode") == "sweep" and "call_rca" not in seen:
            br = result_of(results, "list_breaches", {}).get("breaches", [])
            if not br:
                return "submit_plan", {"dc": "ALL", "sku": None, "week": week, "kpi": "OTIF", "root_cause": "NO_CLEAR_CAUSE", "priority": "P3",
                                       "specialists_called": [], "plan": [], "dropped": [], "approvals": [], "total_impact_inr": 0,
                                       "proposal_id": None, "summary": "Quiet week: no KPI moved; nothing to plan."}
            top = br[0]
            task.update({"dc": top["dc"], "sku": top["sku"], "kpi": top["kpi"]})
            return "call_rca", {"dc": top["dc"], "sku": top["sku"], "week": week, "kpi": top["kpi"]}
        if task.get("mode") == "pre_empt" and "radar" not in seen:
            return "radar", {"dc": task["dc"], "sku": task["sku"], "week": week}
        if task.get("mode") == "pre_empt":
            rad = result_of(results, "radar", {})
            if rad.get("risk") != "RED":
                return "submit_plan", {"dc": task["dc"], "sku": task["sku"], "week": week, "kpi": "RISK", "root_cause": "NO_PROJECTED_SHORTAGE",
                                       "priority": "P3", "specialists_called": [], "plan": [], "dropped": [], "approvals": [],
                                       "total_impact_inr": 0, "proposal_id": None,
                                       "summary": f"{task['dc']} {task['sku']}: radar {rad.get('risk')}, {rad.get('reason', '')}; nothing to pre-empt."}
            rca = {"dc": task["dc"], "sku": task["sku"], "kpi": "RISK", "root_cause": "PROJECTED_SHORTAGE", "priority": rad.get("priority", "P2"),
                   "route": ["rebalancing"]}
        else:
            if "call_rca" not in seen:
                return "call_rca", {"dc": task["dc"], "sku": task.get("sku"), "week": week, "kpi": task.get("kpi", "OTIF")}
            rca = result_of(results, "call_rca", {})
        if "error" in rca:
            return "submit_plan", {"dc": task["dc"], "sku": task.get("sku"), "week": week, "kpi": task.get("kpi", "OTIF"), "root_cause": "UNKNOWN",
                                   "priority": "P3", "specialists_called": ["rca"], "plan": [], "dropped": [], "approvals": [], "total_impact_inr": 0,
                                   "proposal_id": None, "summary": f"The root-cause specialist stopped: {rca['error']}."}
        route = [] if self.skip_specialists else rca.get("route", [])
        sku = rca.get("sku") or task.get("sku")
        if "rebalancing" in route and "call_rebalancing" not in seen and sku:
            return "call_rebalancing", {"sku": sku, "week": week, "to_dc": rca["dc"]}
        if "sensing" in route and "call_sensing" not in seen and sku:
            return "call_sensing", {"dc": rca["dc"], "sku": sku, "origin_week": week}
        if "merge_plan" not in seen:
            return "merge_plan", {"dc": rca["dc"], "sku": sku, "week": week}
        merged = result_of(results, "merge_plan", {})
        if "route_approvals" not in seen and merged.get("plan"):
            return "route_approvals", {"dc": rca["dc"], "sku": sku, "week": week,
                                       "rationale": f"{rca['root_cause']} at {rca['dc']}; specialists: {', '.join(merged.get('specialists_called', []))}"}
        ra = result_of(results, "route_approvals", {}) or {}
        dropped = merged.get("dropped", [])
        drop_txt = ("; ".join(f"{d['code']} dropped by {d['by']}: {d['reason']}" for d in dropped)) if dropped else "nothing dropped"
        who = ", ".join(sorted({a["approver"] for a in merged.get("approvals", [])})) or "nobody"
        return "submit_plan", {
            "dc": rca["dc"], "sku": sku, "week": week, "kpi": rca.get("kpi", task.get("kpi", "OTIF")), "root_cause": rca["root_cause"],
            "priority": rca["priority"], "specialists_called": merged.get("specialists_called", ["rca"]), "plan": merged.get("plan", []),
            "dropped": dropped, "approvals": merged.get("approvals", []), "total_impact_inr": merged.get("total_impact_inr", 0),
            "proposal_id": ra.get("proposal_id"),
            "summary": f"{rca['dc']} {sku or ''}: {rca['root_cause']} ({rca['priority']}, {cur()} {merged.get('total_impact_inr', 0):,.0f} at stake). "
                       f"{len(merged.get('plan', []))} steps waiting for {who}. {drop_txt}.",
        }
