"""Tool contracts for the queue mode and the dispatcher that runs them against a persisted queue.

The model has four doors and a finish: list the queue, read the next items, read one item,
escalate an overdue item (refused before its SLA has passed), and write the digest. It cannot
decide an item, apply one, or close one: those are people's doors (queue.decide / queue.apply).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from foundry.levers import register_tools
from foundry.profile import Profile
from foundry.store import Store
from foundry.agents.rca_exception import queue as Q

TOOL_SPECS = [
    {"toolSpec": {"name": "list_queue", "description": "The week's exception queue in order: id, DC, SKU, cause, priority, money at stake, due time, state, overdue hours at the current time.",
                  "inputSchema": {"json": {"type": "object", "properties": {"week": {"type": "integer"}}, "required": ["week"]}}}},
    {"toolSpec": {"name": "next_items", "description": "The next n open exceptions in full: evidence, actions with approvers, SLA clock, history.",
                  "inputSchema": {"json": {"type": "object", "properties": {"week": {"type": "integer"}, "n": {"type": "integer"}}, "required": ["week"]}}}},
    {"toolSpec": {"name": "item_detail", "description": "One exception in full by id.",
                  "inputSchema": {"json": {"type": "object", "properties": {"item_id": {"type": "string"}}, "required": ["item_id"]}}}},
    {"toolSpec": {"name": "escalate", "description": "Escalate one overdue exception to the escalation role from the profile. Refused if its SLA has not passed.",
                  "inputSchema": {"json": {"type": "object", "properties": {"item_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["item_id", "reason"]}}}},
    {"toolSpec": {"name": "write_digest", "description": "Finish: the Monday digest for the VP, numbers from the tools, words from you.",
                  "inputSchema": {"json": {"type": "object", "properties": {"week": {"type": "integer"}, "now_hours": {"type": "number"}, "headline": {"type": "string"},
                                                                            "p1_count": {"type": "integer"}, "p2_count": {"type": "integer"}, "p3_count": {"type": "integer"},
                                                                            "breaches": {"type": "integer"}, "money_at_stake_inr": {"type": "integer"},
                                                                            "lines": {"type": "array", "items": {"type": "string"}}},
                                           "required": ["week", "now_hours", "headline", "p1_count", "p2_count", "p3_count", "breaches", "money_at_stake_inr", "lines"]}}}},
]
TOOL_NAMES = [t["toolSpec"]["name"] for t in TOOL_SPECS]
register_tools(read={"list_queue", "next_items", "item_detail", "write_digest"}, write={"escalate"}, execute={"close_without_approval"})


class Dispatcher:
    """Holds the week's queue (loaded from runs/queue-w{week}.json or built), runs the tools, saves."""

    def __init__(self, store: Store, profile: Profile, now_hours: float = 0.0, folder: str | Path | None = None,
                 proposals=None, queue: Q.Queue | None = None, persist: bool = True):
        self.store, self.profile, self.now_hours, self.folder = store, profile, float(now_hours), folder
        self.proposals, self.persist = proposals, persist
        self._queue = queue
        self.escalations: list[str] = []

    def queue(self, week: int) -> Q.Queue:
        if self._queue is None or self._queue.week != week:
            if self.persist:
                self._queue = Q.load_or_build(self.store, self.profile, week, self.folder, self.proposals)
            else:
                self._queue = Q.build_queue(self.store, self.profile, week, proposals=self.proposals)
        return self._queue

    def _save(self) -> None:
        if self.persist and self._queue is not None:
            Q.to_json(self._queue, Q.queue_path(self._queue.week, self.folder))

    def run(self, name: str, inp: dict[str, Any]) -> dict:
        if name == "list_queue":
            q = self.queue(int(inp["week"]))
            return {"week": q.week, "now_hours": self.now_hours, "exceptions": len(q.items), "built_from": q.built_from,
                    "items": q.table(self.now_hours), "summary": Q.digest(q, self.now_hours)}
        if name == "next_items":
            q = self.queue(int(inp["week"]))
            n = int(inp.get("n", 3))
            return {"week": q.week, "now_hours": self.now_hours,
                    "items": [{**it.as_dict(), "overdue_hours": it.overdue_hours(self.now_hours)} for it in q.open_items()[:n]]}
        if name == "item_detail":
            if self._queue is None:
                return {"error": "list the queue first"}
            try:
                it = self._queue.get(inp["item_id"])
            except KeyError:
                return {"error": f"no exception {inp['item_id']}"}
            return {**it.as_dict(), "overdue_hours": it.overdue_hours(self.now_hours)}
        if name == "escalate":
            if self._queue is None:
                return {"error": "list the queue first"}
            try:
                out = Q.escalate(self._queue, self.profile, inp["item_id"], self.now_hours, inp.get("reason", ""))
            except KeyError:
                return {"error": f"no exception {inp['item_id']}"}
            if "error" not in out:
                self.escalations.append(inp["item_id"])
                self._save()
            return out
        if name == "write_digest":
            return {"accepted": True}
        return {"error": f"unknown tool {name}"}
