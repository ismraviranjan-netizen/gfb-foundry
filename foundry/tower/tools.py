"""The master agent's tools. It can list breaches, call three specialists, merge their findings,
route the approvals and submit the plan. It has no tool that moves stock, changes a forecast or
books a truck: those are the specialists' write tools, and the tower never sees them.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from foundry.notify.coworker import code as cur
from foundry.approval import ProposalStore
from foundry.levers import Levers, register_tools
from foundry.profile import Profile
from foundry.store import Store
from foundry.tower import routing
from foundry.agents.rca_exception import agent as rca_agent, kpis, triage as rca_triage, diagnosis as rca_diag
from foundry.agents.rebalancing import agent as rbl_agent, logic as rbl_logic
from foundry.agents.sensing import agent as sense_agent
from foundry.risk import projection as risk

TOOL_SPECS = [
    {"toolSpec": {"name": "list_breaches", "description": "The week's KPI deviations, worst first (the monitor). Use in sweep mode to pick the breach to work.",
                  "inputSchema": {"json": {"type": "object", "properties": {"week": {"type": "integer"}}, "required": ["week"]}}}},
    {"toolSpec": {"name": "call_rca", "description": "Run the root cause & exception specialist on one breach. Returns its diagnosis, impact, priority, actions with approvers, and the specialists worth calling next (route).",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": ["string", "null"]}, "week": {"type": "integer"}, "kpi": {"type": "string"}}, "required": ["dc", "week", "kpi"]}}}},
    {"toolSpec": {"name": "call_rebalancing", "description": "Run the inventory rebalancing specialist for one SKU into one receiving DC. Returns the priced transfer or the reason none pays, with its approvals.",
                  "inputSchema": {"json": {"type": "object", "properties": {"sku": {"type": "string"}, "week": {"type": "integer"}, "to_dc": {"type": "string"}}, "required": ["sku", "week", "to_dc"]}}}},
    {"toolSpec": {"name": "call_sensing", "description": "Run the demand sensing specialist for one DC and SKU from this week. Returns adjust or silent with the numbers.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": "string"}, "origin_week": {"type": "integer"}}, "required": ["dc", "sku", "origin_week"]}}}},
    {"toolSpec": {"name": "radar", "description": "The risk radar for one DC and SKU: the day-by-day projection of the balance over the next two weeks, the first short day, the cases and money at risk, RED/AMBER/GREEN/GREY. In pre_empt mode this is the first call and it stands in for the diagnosis.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": "string"}, "week": {"type": "integer"}}, "required": ["dc", "sku", "week"]}}}},
    {"toolSpec": {"name": "what_if", "description": "The same projection with a transfer of N cases landing at the end of day D: the money at risk before and after.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": "string"}, "week": {"type": "integer"}, "cases": {"type": "number"}, "lane_days": {"type": "integer"}}, "required": ["dc", "sku", "week", "cases", "lane_days"]}}}},
    {"toolSpec": {"name": "merge_plan", "description": "Merge the findings gathered so far for this breach into one plan: one action per approver, a specialist's priced verdict replacing the diagnosis's generic step, dropped steps listed with the reason.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": ["string", "null"]}, "week": {"type": "integer"}}, "required": ["dc", "week"]}}}},
    {"toolSpec": {"name": "route_approvals", "description": "Write the plan as one tower proposal with every approver named, the P1 clock first. Propose-only.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": ["string", "null"]}, "week": {"type": "integer"}, "rationale": {"type": "string"}}, "required": ["dc", "week", "rationale"]}}}},
    {"toolSpec": {"name": "submit_plan", "description": "Finish: the plan for the VP's screen.",
                  "inputSchema": {"json": {"type": "object", "properties": {
                      "dc": {"type": "string"}, "sku": {"type": ["string", "null"]}, "week": {"type": "integer"}, "kpi": {"type": "string"},
                      "root_cause": {"type": "string"}, "priority": {"type": "string"}, "specialists_called": {"type": "array", "items": {"type": "string"}},
                      "plan": {"type": "array", "items": {"type": "object"}}, "dropped": {"type": "array", "items": {"type": "object"}},
                      "approvals": {"type": "array", "items": {"type": "object"}}, "total_impact_inr": {"type": "number"},
                      "proposal_id": {"type": ["string", "null"]}, "summary": {"type": "string"}},
                      "required": ["dc", "week", "kpi", "root_cause", "priority", "specialists_called", "plan", "approvals", "total_impact_inr", "summary"]}}}},
]
TOOL_NAMES = [t["toolSpec"]["name"] for t in TOOL_SPECS]
register_tools(read={"list_breaches", "call_rca", "call_rebalancing", "call_sensing", "merge_plan", "submit_plan", "radar", "what_if"},
               write={"route_approvals"}, execute={"execute_plan"})


class Dispatcher:
    """Runs the specialists with their own brains (a scripted one per specialist offline, the one
    Bedrock client live) and caches what they said for the merge."""

    def __init__(self, store: Store, profile: Profile, proposals: ProposalStore, levers: Levers, run_id: str,
                 specialist_client: Callable[[str], Any], budget_usd: float = 0.25):
        self.store, self.profile, self.proposals, self.levers, self.run_id = store, profile, proposals, levers, run_id
        self.client_for = specialist_client
        self.budget_usd = budget_usd
        self.findings: dict[str, dict] = {}        # specialist -> last result
        self.meters: dict[str, dict] = {}
        self.called: list[str] = []
        self.disabled: list[str] = []

    def run(self, name: str, inp: dict[str, Any]) -> dict:
        s, p = self.store, self.profile
        if name == "list_breaches":
            devs = kpis.deviations(s, p, int(inp["week"]))
            return {"week": int(inp["week"]), "count": len(devs), "breaches": [d.as_dict() for d in devs[:12]]}
        if name == "call_rca":
            task = {"mode": "diagnose", "dc": inp["dc"], "sku": inp.get("sku"), "week": int(inp["week"]), "kpi": inp.get("kpi", "OTIF")}
            r = rca_agent.run(task, s, p, self.client_for("rca"), self.levers, self.proposals, budget_usd=self.budget_usd)
            self.meters["rca"] = r.meter
            self.called.append("rca")
            if r.stop_reason != "submitted" or not r.diagnosis:
                return {"error": f"rca specialist stopped: {r.stop_reason}", "stop_reason": r.stop_reason}
            d = r.diagnosis
            # the approvers and texts come from the deterministic triage, not from the model's submit
            diag = rca_diag.gather(s, p, int(inp["week"]), inp["dc"], inp.get("sku"), inp.get("kpi", "OTIF"))
            tri = rca_triage.triage(s, p, diag)
            d["actions_detail"] = [a.__dict__ for a in tri.actions]
            d["impact_inr"] = tri.impact_inr
            d["sla_hours"] = tri.sla_hours
            d["route"] = routing.route(d["root_cause"], d.get("contributing", []), int(inp["week"]), s.weeks, extra=p.plugins.routes)
            d["proposal_id"] = r.proposal_id
            self.findings["rca"] = d
            return {**d, "meter": r.meter}
        if name == "call_rebalancing":
            task = {"mode": "transfer", "sku": inp["sku"], "week": int(inp["week"]), "to_dc": inp["to_dc"]}
            r = rbl_agent.run(task, s, p, self.client_for("rebalancing"), self.levers, self.proposals, budget_usd=self.budget_usd)
            self.meters["rebalancing"] = r.meter
            self.called.append("rebalancing")
            if r.stop_reason != "submitted":
                return {"error": f"rebalancing specialist stopped: {r.stop_reason}", "stop_reason": r.stop_reason}
            plan = rbl_logic.plan_transfer(s, p, int(inp["week"]), inp["sku"], inp["to_dc"])
            t = plan.get("transfer")
            out = {"transfer": t, "reason": plan.get("reason"), "alternative": plan.get("alternative"),
                   "approvals": (t or {}).get("approvals", []), "proposal_id": r.proposal_id, "submitted": r.plan, "meter": r.meter}
            self.findings["rebalancing"] = out
            return out
        if name == "call_sensing":
            task = {"mode": "sense", "dc": inp["dc"], "sku": inp["sku"], "origin_week": int(inp["origin_week"]), "horizon": 2}
            r = sense_agent.run(task, s, p, self.client_for("sensing"), self.levers, self.proposals, budget_usd=self.budget_usd)
            self.meters["sensing"] = r.meter
            self.called.append("sensing")
            if r.stop_reason != "submitted" or not r.sensing:
                return {"error": f"sensing specialist stopped: {r.stop_reason}", "stop_reason": r.stop_reason}
            out = dict(r.sensing)
            out["proposal_id"] = r.proposal_id
            row = self.proposals.get(r.proposal_id) if r.proposal_id else None
            out["approvals"] = (row or {}).get("actions", [])
            out["reasons"] = out.get("reasons") or []
            out["meter"] = r.meter
            self.findings["sensing"] = out
            return out
        if name == "radar":
            pr = risk.project(s, p, int(inp["week"]), inp["dc"], inp["sku"])
            out = pr.as_dict(with_days=True)
            self.findings["radar"] = out
            if "rca" not in self.findings:
                # pre-empt: no KPI has moved yet; the projection is the diagnosis
                hours = pr.hours_to_impact
                late = [a for a in pr.arrivals if a["source"].startswith(("open receipt", "transfer")) and a["day"] >= (pr.first_short_day or 99)]
                if late and pr.risk == "RED" and risk.rescue(s, p, int(inp["week"]), inp["dc"], inp["sku"])["helps"] is False:
                    late = []     # a load is late, but no rush could land it sooner (finding F1): do not offer one
                acts = [{"code": "inter_dc_transfer", "approver": p.approver_for("inter_dc_transfer", inp["dc"]),
                         "text": "Move cases from a long DC before the projected stockout lands"}]
                if late:      # a load is on the road but lands after the shelf is empty: rescue it first
                    acts.append({"code": "expedite_alternate_carrier", "approver": p.approver_for("expedite_alternate_carrier", inp["dc"]),
                                 "text": f"Rescue the late load ({late[0]['source']}, lands day {late[0]['day']}) with a relief truck or an alternate carrier"})
                else:
                    acts.append({"code": "expedite_plant_run", "approver": p.approver_for("expedite_plant_run", inp["dc"]),
                                 "text": "Ask the plant to bring the next run forward"})
                self.findings["rca"] = {"dc": inp["dc"], "sku": inp["sku"], "week": int(inp["week"]), "kpi": "RISK",
                                        "root_cause": "PROJECTED_SHORTAGE" if pr.risk == "RED" else "NO_PROJECTED_SHORTAGE",
                                        "confidence": 1.0, "contributing": [], "priority": pr.priority, "sla_hours": pr.sla_hours,
                                        "impact_inr": pr.money_at_risk, "actions": [a["code"] for a in acts] if pr.risk == "RED" else [],
                                        "actions_detail": acts if pr.risk == "RED" else [],
                                        "route": routing.route("PROJECTED_SHORTAGE", [], int(inp["week"]), s.weeks) if pr.risk == "RED" else [],
                                        "evidence": [pr.reason], "hours_to_impact": hours, "proposal_id": None,
                                        "summary": f"{inp['dc']} {inp['sku']}: {pr.reason}"}
            return out
        if name == "what_if":
            return risk.what_if(s, p, int(inp["week"]), inp["dc"], inp["sku"], float(inp["cases"]), int(inp["lane_days"]))
        if name == "merge_plan":
            if "rca" not in self.findings:
                return {"error": "call_rca first; there is nothing to merge"}
            merged = routing.merge(self.findings["rca"], self.findings.get("rebalancing"), self.findings.get("sensing"), self.disabled)
            # a priced transfer is checked against the radar: what is still at risk if the truck leaves?
            t_step = next((st for st in merged["plan"] if st["code"] == "inter_dc_transfer" and st.get("cases")), None)
            dc_sku_ok = inp.get("sku") and s.rows("inventory", week=int(inp["week"]), dc=inp["dc"], sku=inp.get("sku"))
            if t_step and dc_sku_ok:
                wi = risk.what_if(s, p, int(inp["week"]), inp["dc"], inp["sku"], float(t_step["cases"]), int(t_step.get("lands_end_of_day") or t_step["lane_days"]),
                                  source=f"transfer {t_step.get('from_dc')}->{inp['dc']}")
                merged["projection"] = {k: wi[k] for k in ("before", "after", "money_avoided", "cases", "lands_end_of_day")}
                closes = wi["before"]["money_at_risk"] > 0 and wi["after"]["money_at_risk"] <= 0.1 * wi["before"]["money_at_risk"]
                if closes and any(st["code"] == "expedite_plant_run" for st in merged["plan"]) and self.findings["rca"].get("kpi") == "RISK":
                    merged["plan"] = [st for st in merged["plan"] if st["code"] != "expedite_plant_run"]
                    merged["dropped"].append({"code": "expedite_plant_run", "by": "radar",
                                              "reason": f"the transfer closes the gap: {cur()} {wi['after']['money_at_risk']:,} left at risk of {cur()} {wi['before']['money_at_risk']:,}"})
            # a rush only earns its place if it lands the late cases before they would land anyway (finding F1)
            if dc_sku_ok and any(st["code"] == "expedite_alternate_carrier" for st in merged["plan"]):
                extra = ([{"day": int(t_step.get("lands_end_of_day") or t_step["lane_days"]), "cases": float(t_step["cases"]),
                           "source": f"transfer {t_step.get('from_dc')}->{inp['dc']}"}] if t_step else None)
                rs = risk.rescue(s, p, int(inp["week"]), inp["dc"], inp["sku"], extra_arrivals=extra)
                merged["rescue"] = rs
                if rs["helps"] is False:
                    merged["plan"] = [st for st in merged["plan"] if st["code"] != "expedite_alternate_carrier"]
                    merged["dropped"].append({"code": "expedite_alternate_carrier", "by": "radar", "reason": rs["why"]})
                    for i, st in enumerate(merged["plan"], 1):
                        st["n"] = i
            for step in merged["plan"]:
                if not step.get("approver"):
                    step["approver"] = p.approver_for(step["code"], inp["dc"]) if step["code"] in p.approvers else "unassigned"
            d = self.findings["rca"]
            merged.update({"dc": inp["dc"], "sku": inp.get("sku"), "week": int(inp["week"]), "root_cause": d["root_cause"], "priority": d["priority"],
                           "specialists_called": list(dict.fromkeys(self.called)), "total_impact_inr": d.get("impact_inr", 0),
                           "approvals": routing.approvals(merged["plan"], d["priority"], d.get("sla_hours", 72))})
            self.findings["merged"] = merged
            return merged
        if name == "route_approvals":
            m = self.findings.get("merged")
            if not m:
                return {"error": "merge_plan first"}
            actions = [{"code": st["code"], "text": st.get("text", st["code"]), "approver": st.get("approver", "unassigned"), "source": st.get("source")} for st in m["plan"]]
            row = self.proposals.propose(inp["dc"], inp.get("sku"), int(inp["week"]), m["root_cause"], actions, inp["rationale"], self.run_id,
                                         kind="plan", payload={"plan": m["plan"], "dropped": m["dropped"], "specialists": m["specialists_called"],
                                                               "specialist_proposals": [f.get("proposal_id") for k, f in self.findings.items() if k != "merged" and f.get("proposal_id")]},
                                         agent="tower")
            m["proposal_id"] = row["proposal_id"]
            return {"proposal_id": row["proposal_id"], "status": row["status"], "approvals": m["approvals"],
                    "approvers": sorted({a["approver"] for a in actions})}
        if name == "submit_plan":
            return {"accepted": True}
        return {"error": f"unknown tool {name}"}

    def total_meter(self) -> dict:
        tot = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "cost_inr": 0.0}
        for m in self.meters.values():
            for k in tot:
                tot[k] += m.get(k, 0)
        return {k: round(v, 4) if isinstance(v, float) else v for k, v in tot.items()}
