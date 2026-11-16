"""The chat's tools. All read-only: the dispatcher holds a TowerRuntime and only reads it."""
from __future__ import annotations

from typing import Any

from foundry.levers import register_tools
from foundry.notify.coworker import clock_label, inr, model_cost
from foundry.notify.outbox import name_of

TOOL_SPECS = [
    {"toolSpec": {"name": "tower_state", "description": "The tower right now: live plans (id, DC, SKU, cause, priority, status, who it waits on, due), the radar counts and the money at stake.",
                  "inputSchema": {"json": {"type": "object", "properties": {}}}}},
    {"toolSpec": {"name": "explain_plan", "description": "One plan in full: steps and signatures, what was dropped and why, the radar before and after, reconciler notes, its history and the chain of events that caused it.",
                  "inputSchema": {"json": {"type": "object", "properties": {"plan_id": {"type": "string"}}, "required": ["plan_id"]}}}},
    {"toolSpec": {"name": "find_plan", "description": "The latest plan for a DC and SKU (sku may be null for a DC-wide plan), live or not.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": ["string", "null"]}}, "required": ["dc"]}}}},
    {"toolSpec": {"name": "radar_cell", "description": "The radar's projection (its horizon: 14 days for GFB, as set in the profile) for one DC and SKU: risk class, first short day, cases and money at risk, arrivals, and the reason.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": "string"}, "sku": {"type": "string"}, "week": {"type": "integer"}}, "required": ["dc", "sku"]}}}},
    {"toolSpec": {"name": "whats_changed", "description": "The journal for one DC and SKU (or the whole tower): the events, in order, and what each handler noted.",
                  "inputSchema": {"json": {"type": "object", "properties": {"dc": {"type": ["string", "null"]}, "sku": {"type": ["string", "null"]}, "limit": {"type": "integer"}}}}}},
    {"toolSpec": {"name": "ask_data", "description": "Hand a data question (OTIF, fill rate, days of supply, late trucks, dock dwell, forecast error, any table) to the Talk to Data agent; returns its answer and the SQL it ran.",
                  "inputSchema": {"json": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}}},
    {"toolSpec": {"name": "answer", "description": "Finish: the reply, and what it cites.",
                  "inputSchema": {"json": {"type": "object", "properties": {"text": {"type": "string"}, "cited": {"type": "array", "items": {"type": "string"}}},
                                           "required": ["text", "cited"]}}}},
]
register_tools(read={"tower_state", "explain_plan", "find_plan", "radar_cell", "whats_changed", "ask_data", "answer"})


class Dispatcher:
    def __init__(self, runtime, sql_client_factory=None):
        self.rt = runtime
        self.sql_client_factory = sql_client_factory
        self.sql_meter: dict = {}

    def run(self, name: str, inp: dict[str, Any]) -> dict:
        rt = self.rt
        if name == "tower_state":
            live = [p for p in rt.plans.values() if p["status"] in ("READY", "PARTIAL", "RECONCILING", "PROPOSED")]
            week = max(rt.radars) if rt.radars else None
            rad = rt.radar_now(week) if week else {}
            return {"clock": clock_label(rt.now), "week": week, "counts": rt.state()["counts"],
                    "money_at_stake": inr(sum(p.get("impact_inr", 0) for p in live)),
                    "live_plans": [{"plan_id": p["plan_id"], "dc": p["dc"], "sku": p["sku"], "cause": p["root_cause"], "priority": p["priority"],
                                    "status": p["status"], "due": clock_label(p["due_at"]), "money": inr(p.get("impact_inr")),
                                    "waiting_on": sorted({name_of(s["approver"]) for s in p["steps"] if s["decision"] is None})} for p in live],
                    "radar_counts": rad.get("counts"), "radar_red": [{"dc": r["dc"], "sku": r["sku"], "money_at_risk": inr(r["money_at_risk"]),
                                                                       "first_short_day": r["first_short_day"]} for r in rad.get("rows", []) if r["risk"] == "RED"],
                    "model_cost_this_week": model_cost(rt.cost_inr())}
        if name in ("explain_plan", "find_plan"):
            if name == "find_plan":
                rows = [p for p in rt.plans.values() if p["dc"] == inp["dc"] and (p["sku"] == inp.get("sku") or not inp.get("sku"))]
                if not rows:
                    return {"error": f"no plan for {inp['dc']} {inp.get('sku') or 'all SKUs'}"}
                rec = rows[-1]
            else:
                rec = rt.plans.get(inp["plan_id"])
                if not rec:
                    return {"error": f"no plan {inp['plan_id']}"}
            w = rec.get("what_if")
            return {"plan_id": rec["plan_id"], "version": rec["version"], "supersedes": rec.get("supersedes"), "superseded_by": rec.get("superseded_by"),
                    "dc": rec["dc"], "sku": rec["sku"], "mode": rec["mode"], "cause": rec["root_cause"], "priority": rec["priority"],
                    "status": rec["status"], "due": clock_label(rec["due_at"]), "money_at_stake": inr(rec.get("impact_inr")),
                    "steps": [{"n": s["n"], "code": s["code"], "approver": name_of(s["approver"]), "decision": s["decision"], "by": s["decided_by"],
                               **({"cases": s["cases"], "from_dc": s.get("from_dc"), "lands_end_of_day": s.get("lands_end_of_day"),
                                   "lane_cost": inr(s.get("cost_inr")), "margin_saved": inr(s.get("margin_saved_inr"))} if s.get("cases") else {})}
                              for s in rec["steps"]],
                    "dropped": rec["dropped"], "reconcile": rec["reconcile"],
                    "radar": {k: (rec.get("radar") or {}).get(k) for k in ("risk", "first_short_day", "cases_short", "money_at_risk", "reason")},
                    "what_if": {"before": inr(w["before"]["money_at_risk"]), "after": inr(w["after"]["money_at_risk"]), "avoided": inr(w["money_avoided"])} if w else None,
                    "history": [f"{clock_label(h['at'])} {h['event']}: {h['note']}" for h in rec["history"]],
                    "caused_by": [f"{r['id']} {r['type']} ({r.get('source')}): {'; '.join(r.get('notes') or [])[:160]}" for r in rt.bus.trace(rec["trigger"])]}
        if name == "radar_cell":
            week = int(inp.get("week") or (max(rt.radars) if rt.radars else 1))
            try:
                v = rt._project(week, inp["dc"], inp["sku"])
            except ValueError as e:
                return {"error": str(e)}
            return {"week": week, "dc": inp["dc"], "sku": inp["sku"], **{k: v[k] for k in ("risk", "first_short_day", "cases_short", "dos0", "reason")},
                    "long_line_days": rt.profile.kpis["max_dos_days"],
                    "money_at_risk": inr(v["money_at_risk"]), "arrivals": v["arrivals"], "notes": v["notes"]}
        if name == "whats_changed":
            dc, sku = inp.get("dc"), inp.get("sku")
            rows = []
            for r in rt.bus.journal:
                if "id" not in r:
                    continue
                txt = " ".join(r.get("notes") or []) + " " + str(r.get("detail"))
                if dc and dc not in txt:
                    continue
                if sku and sku not in txt:
                    continue
                rows.append(f"{r['id']} {clock_label(r['at'])} {r['type']}: {'; '.join(r.get('notes') or [])[:220]}")
            return {"dc": dc, "sku": sku, "events": rows[-int(inp.get("limit") or 12):]}
        if name == "ask_data":
            from foundry.sqlx import agent as sql_agent
            from foundry.sqlx.fake import FakeSqlClient
            client = self.sql_client_factory() if self.sql_client_factory else FakeSqlClient()
            r = sql_agent.run(inp["question"], rt.store, rt.profile, client)
            self.sql_meter = r.meter
            d = r.data or {}
            return {"answer": (r.answer or {}).get("text"), "stop_reason": r.stop_reason, "sql": d.get("sql"), "columns": d.get("columns"),
                    "rows": (d.get("rows") or [])[:12], "meter": r.meter}
        if name == "answer":
            return {"accepted": True}
        return {"error": f"unknown tool {name}"}
