"""Tool contracts (Converse toolSpec format) and the dispatcher that runs them against the store.

Every tool returns JSON the model can quote. The dispatcher is where the levers are checked.
"""
from __future__ import annotations

import json
from typing import Any

from foundry.profile import Profile
from foundry.store import Store
from foundry.approval import ProposalStore
from foundry.agents.rca_exception import kpis, diagnosis, triage
from foundry.agents.rca_exception.triage import ACTION_TEXT

TOOL_SPECS = [
    {"toolSpec": {"name": "list_kpi_deviations", "description": "KPIs that moved this week, worst first (OTIF, fill rate, days of supply), with baseline and z.",
                  "inputSchema": {"json": {"type": "object", "properties": {"week": {"type": "integer"}}, "required": ["week"]}}}},
    {"toolSpec": {"name": "gather_evidence", "description": "Run the diagnosis tree for one DC (and optionally one SKU) in one week: six causes scored 0..1 with facts, a primary cause and confidence.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": ["string", "null"]}, "week": {"type": "integer"}, "kpi": {"type": "string"}}, "required": ["dc", "week"]}}}},
    {"toolSpec": {"name": "estimate_impact", "description": "Cases affected, money at stake, hours to impact, priority class, SLA and the playbook with approvers for the diagnosed cause.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": ["string", "null"]}, "week": {"type": "integer"}}, "required": ["dc", "week"]}}}},
    {"toolSpec": {"name": "propose_actions", "description": "Write a proposal for a named approver. Propose-only: nothing is executed.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": ["string", "null"]}, "week": {"type": "integer"}, "root_cause": {"type": "string"},
                                                                            "actions": {"type": "array", "items": {"type": "string"}}, "rationale": {"type": "string"}}, "required": ["dc", "week", "root_cause", "actions", "rationale"]}}}},
    {"toolSpec": {"name": "submit_diagnosis", "description": "Finish: the structured diagnosis for the planner.",
                  "inputSchema": {"json": {"type": "object", "properties": {"root_cause": {"type": "string"}, "priority": {"type": "string"}, "summary": {"type": "string"}}, "required": ["root_cause", "summary"]}}}},
]
TOOL_NAMES = [t["toolSpec"]["name"] for t in TOOL_SPECS]


class Dispatcher:
    def __init__(self, store: Store, profile: Profile, proposals: ProposalStore, run_id: str):
        self.store, self.profile, self.proposals, self.run_id = store, profile, proposals, run_id
        self._last_diag: dict[tuple, diagnosis.Diagnosis] = {}

    def run(self, name: str, inp: dict[str, Any]) -> dict:
        if name == "list_kpi_deviations":
            devs = kpis.deviations(self.store, self.profile, int(inp["week"]))
            return {"week": int(inp["week"]), "count": len(devs), "deviations": [d.as_dict() for d in devs[:12]]}
        if name == "gather_evidence":
            d = diagnosis.gather(self.store, self.profile, int(inp["week"]), inp["dc"], inp.get("sku"), inp.get("kpi") or "OTIF")
            self._last_diag[(inp["dc"], inp.get("sku"), int(inp["week"]))] = d
            return d.as_dict()
        if name == "estimate_impact":
            key = (inp["dc"], inp.get("sku"), int(inp["week"]))
            d = self._last_diag.get(key) or diagnosis.gather(self.store, self.profile, int(inp["week"]), inp["dc"], inp.get("sku"))
            t = triage.triage(self.store, self.profile, d)
            out = t.as_dict()
            out["impact_detail"] = triage.estimate_impact(self.store, self.profile, int(inp["week"]), inp["dc"], inp.get("sku"))
            return out
        if name == "propose_actions":
            known = {**ACTION_TEXT, **self.profile.plugins.action_text()}     # a plug-in cause brings its own actions
            unknown = [a for a in inp["actions"] if a not in known]
            if unknown:
                return {"error": f"unknown action codes {unknown}; use the codes from estimate_impact"}
            actions = [{"code": a, "text": known[a], "approver": self.profile.approver_for(a, inp["dc"])} for a in inp["actions"]]
            row = self.proposals.propose(inp["dc"], inp.get("sku"), int(inp["week"]), inp["root_cause"], actions, inp["rationale"], self.run_id)
            return {"proposal_id": row["proposal_id"], "status": row["status"], "approvers": sorted({a["approver"] for a in actions})}
        if name == "submit_diagnosis":
            return {"accepted": True}
        return {"error": f"unknown tool {name}"}


def tool_result_block(tool_use_id: str, payload: dict) -> dict:
    return {"toolResult": {"toolUseId": tool_use_id, "content": [{"json": payload}]}}
