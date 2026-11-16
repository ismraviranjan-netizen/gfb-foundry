"""The queue mode's scripted brain: list -> next 3 -> escalate the overdue -> write the digest.

skip_escalation=True is the model that stopped reading the clock: it never escalates and reports
zero breaches. The eval traps exist to catch exactly that.
"""
from __future__ import annotations

from foundry.scripted import ScriptedClient, result_of


class FakeQueueClient(ScriptedClient):
    def __init__(self, fail_at_turn: int | None = None, skip_escalation: bool = False, escalate_early: str | None = None, n: int = 3):
        super().__init__(fail_at_turn)
        self.skip_escalation = skip_escalation
        self.escalate_early = escalate_early      # an item id to escalate whatever the clock says (the refusal trap)
        self.n = n

    def next_call(self, task: dict, results: list[dict], seen: list[str]) -> tuple[str, dict]:
        week = int(task["week"])
        if "list_queue" not in seen:
            return "list_queue", {"week": week}
        listing = result_of(results, "list_queue")
        if "next_items" not in seen:
            return "next_items", {"week": week, "n": self.n}
        nxt = result_of(results, "next_items")
        now = float(listing.get("now_hours", task.get("now_hours", 0)))
        if self.escalate_early and "escalate" not in seen:
            return "escalate", {"item_id": self.escalate_early, "reason": "the VP asked"}
        if not self.skip_escalation:
            tried = {r["content"].get("item_id") or r["content"].get("error", "") for r in results if r["name"] == "escalate"}
            for row in listing["items"]:
                overdue = row.get("overdue_hours", 0) > 0 and row["state"] == "PROPOSED" and not row["escalated"]
                already = any(row["id"] in t for t in tried)
                if overdue and not already:
                    return "escalate", {"item_id": row["id"], "reason": f"SLA passed {row['overdue_hours']} h ago with no answer"}
        summary = listing["summary"]
        breaches = 0 if self.skip_escalation else summary["breach_count"]
        open_rows = [r for r in listing["items"] if r["state"] in ("NEW", "DIAGNOSED", "PROPOSED")]
        lines = []
        for r in open_rows:
            who = (r["approvers"] or ["nobody"])[0]
            clock = f"overdue {r['overdue_hours']} h" if r.get("overdue_hours", 0) > 0 else f"due at {r['due_at']:.0f} h"
            lines.append(f"{r['id']} {r['priority']} {r['dc']} {r['sku'] or 'all SKUs'}: {r['root_cause'].replace('_', ' ').lower()}, "
                         f"{r['impact_inr']:,} at stake, {clock}, waiting on {who}")
        top = open_rows[0] if open_rows else None
        headline = (f"{top['dc']} {top['sku'] or ''} {top['root_cause'].replace('_', ' ').lower()} is the worst open item, "
                    f"{top['impact_inr']:,} at stake, waiting on {(top['approvers'] or ['nobody'])[0]}") if top else "Quiet week: nothing open."
        return "write_digest", {
            "week": week, "now_hours": now, "headline": headline,
            "p1_count": summary["open_by_priority"]["P1"], "p2_count": summary["open_by_priority"]["P2"], "p3_count": summary["open_by_priority"]["P3"],
            "breaches": breaches, "money_at_stake_inr": summary["money_at_stake_inr"], "lines": lines,
        }
