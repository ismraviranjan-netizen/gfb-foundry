"""Tool contracts (Converse toolSpec) and the dispatcher for the rebalancing agent.

Five doors: list_shortages (sweep), stock_position, lane_options, transfer_economics (read);
propose_transfer (write: a proposal with named approvers); submit_plan (finish). The execute tool
create_sto exists only so propose-only mode has something to refuse.
"""
from __future__ import annotations

from typing import Any

from foundry.approval import ProposalStore
from foundry.levers import register_tools
from foundry.profile import Profile
from foundry.store import Store
from foundry.agents.rebalancing import logic

TOOL_SPECS = [
    {"toolSpec": {"name": "list_shortages", "description": "Sweep: every A/B SKU with a DC below the short line this week, worst first, with whether a fresh long partner exists and what a transfer would save.",
                  "inputSchema": {"json": {"type": "object", "properties": {"week": {"type": "integer"}}, "required": ["week"]}}}},
    {"toolSpec": {"name": "stock_position", "description": "Where one SKU sits: per DC on hand, open receipt, snapshot age and freshness, days of supply, reorder point, short/long/ok, days to the next receipt.",
                  "inputSchema": {"json": {"type": "object", "properties": {"sku": {"type": "string"}, "week": {"type": "integer"}}, "required": ["sku", "week"]}}}},
    {"toolSpec": {"name": "lane_options", "description": "Candidate senders for one receiving DC: fresh long DCs with a lane, what each can spare (without and with a co-sign), transit days, cost per case, whether it lands in time and before the next receipt. Also lists who was excluded and why.",
                  "inputSchema": {"json": {"type": "object", "properties": {"to_dc": {"type": "string"}, "sku": {"type": "string"}, "week": {"type": "integer"}}, "required": ["to_dc", "sku", "week"]}}}},
    {"toolSpec": {"name": "transfer_economics", "description": "Price one move: lane cost, holding cost, days of shortage avoided, margin saved, whether it pays, what it leaves the sender with, whether the sender must co-sign. Also returns the planned transfer the maths recommends for this SKU and receiver.",
                  "inputSchema": {"json": {"type": "object", "properties": {"from_dc": {"type": "string"}, "to_dc": {"type": "string"}, "sku": {"type": "string"}, "cases": {"type": "integer"}, "week": {"type": "integer"}}, "required": ["from_dc", "to_dc", "sku", "cases", "week"]}}}},
    {"toolSpec": {"name": "propose_transfer", "description": "Write the transfer proposal for the named approvers (receiving DC manager; sending DC manager co-signs below ROP; logistics lead books the truck). Propose-only: nothing moves until they approve. Refuses a stale sender or receiver.",
                  "inputSchema": {"json": {"type": "object", "properties": {"from_dc": {"type": "string"}, "to_dc": {"type": "string"}, "sku": {"type": "string"}, "cases": {"type": "integer"}, "week": {"type": "integer"}, "rationale": {"type": "string"}}, "required": ["from_dc", "to_dc", "sku", "cases", "week", "rationale"]}}}},
    {"toolSpec": {"name": "submit_plan", "description": "Finish: the structured rebalancing plan for the planner (or a no-transfer plan with the reason).",
                  "inputSchema": {"json": {"type": "object", "properties": {"to_dc": {"type": "string"}, "sku": {"type": "string"}, "cases": {"type": "integer"}, "summary": {"type": "string"}}, "required": ["to_dc", "sku", "week", "cases", "pays", "summary"]}}}},
]
TOOL_NAMES = [t["toolSpec"]["name"] for t in TOOL_SPECS]
register_tools(read={"list_shortages", "stock_position", "lane_options", "transfer_economics", "submit_plan"},
               write={"propose_transfer"}, execute={"create_sto"})


class Dispatcher:
    def __init__(self, store: Store, profile: Profile, proposals: ProposalStore, run_id: str):
        self.store, self.profile, self.proposals, self.run_id = store, profile, proposals, run_id

    def run(self, name: str, inp: dict[str, Any]) -> dict:
        s, p = self.store, self.profile
        if name == "list_shortages":
            rows = logic.list_shortages(s, p, int(inp["week"]))
            return {"week": int(inp["week"]), "count": len(rows), "shortages": rows[:12]}
        if name == "stock_position":
            rows = logic.position(s, p, int(inp["week"]), inp["sku"])
            return {"sku": inp["sku"], "week": int(inp["week"]), "position": rows,
                    "short": [r["dc"] for r in rows if r["status"] == "short"], "long": [r["dc"] for r in rows if r["status"] == "long"],
                    "stale": [r["dc"] for r in rows if not r["fresh"]]}
        if name == "lane_options":
            return logic.lane_options(s, p, int(inp["week"]), inp["sku"], inp["to_dc"])
        if name == "transfer_economics":
            eco = logic.economics(s, p, int(inp["week"]), inp["sku"], inp["from_dc"], inp["to_dc"], int(inp["cases"]))
            plan = logic.plan_transfer(s, p, int(inp["week"]), inp["sku"], inp["to_dc"])
            t = plan.get("transfer")
            return {"economics": eco, "recommended": t, "recommendation_reason": plan["reason"], "alternative": plan.get("alternative")}
        if name == "propose_transfer":
            rows = logic.position(s, p, int(inp["week"]), inp["sku"])
            by = {r["dc"]: r for r in rows}
            snd, rec = by.get(inp["from_dc"]), by.get(inp["to_dc"])
            if not snd or not rec:
                return {"error": f"no position for {inp['from_dc']} or {inp['to_dc']}"}
            if not snd["fresh"]:
                return {"error": f"refused: {inp['from_dc']}'s snapshot is {snd['snapshot_age_h']} h old; stock is never moved out of a DC whose number may be wrong"}
            if not rec["fresh"]:
                return {"error": f"refused: {inp['to_dc']}'s snapshot is {rec['snapshot_age_h']} h old; refresh it first"}
            if not s.net.lane(inp["from_dc"], inp["to_dc"]):
                return {"error": f"refused: no lane {inp['from_dc']}->{inp['to_dc']}"}
            priced = logic.plan_transfer(s, p, int(inp["week"]), inp["sku"], inp["to_dc"]).get("transfer")
            if not priced:
                return {"error": f"refused: the maths recommends no transfer into {inp['to_dc']}; nothing to propose"}
            if priced["from_dc"] != inp["from_dc"] or abs(int(inp["cases"]) - priced["cases"]) > max(1, round(0.01 * priced["cases"])):
                # found by The Agent Desk (8 Nov): 7,000 cases were accepted on F.11; the model may not resize a priced move
                return {"error": f"refused: the priced move is {priced['cases']} cases from {priced['from_dc']}; "
                                 f"{inp['cases']} from {inp['from_dc']} was asked. Propose what the economics tool recommended."}
            eco = logic.economics(s, p, int(inp["week"]), inp["sku"], inp["from_dc"], inp["to_dc"], int(inp["cases"]), rows)
            if eco["leaves_sender_dos"] is not None and eco["leaves_sender_dos"] < float(p.kpis["min_dos_days"]):
                return {"error": f"refused: the move would leave {inp['from_dc']} at {eco['leaves_sender_dos']} days, below the short line"}
            actions = [{"code": "inter_dc_transfer", "text": f"Move {inp['cases']} cases of {inp['sku']} {inp['from_dc']}->{inp['to_dc']} on the {eco['lane_days']}-day lane",
                        "approver": p.approver_for("inter_dc_transfer", inp["to_dc"])}]
            if eco["co_sign_needed"]:
                actions.append({"code": "co_sign_transfer", "text": f"Co-sign: {inp['from_dc']} drops below its reorder point ({eco['leaves_sender_cases']} cases left, ROP {eco['sender_rop']})",
                                "approver": p.approver_for("co_sign_transfer", inp["from_dc"])})
            actions.append({"code": "book_truck", "text": f"Book the truck {inp['from_dc']}->{inp['to_dc']} for {inp['cases']} cases", "approver": p.approver_for("book_truck", inp["to_dc"])})
            row = self.proposals.propose(inp["to_dc"], inp["sku"], int(inp["week"]), "INVENTORY_IMBALANCE", actions, inp["rationale"], self.run_id,
                                         kind="rebalance", payload=eco, agent="rebalancing")
            return {"proposal_id": row["proposal_id"], "status": row["status"], "approvers": sorted({a["approver"] for a in actions}),
                    "co_sign_needed": eco["co_sign_needed"]}
        if name == "submit_plan":
            return {"accepted": True}
        return {"error": f"unknown tool {name}"}
