"""The rebalancing agent's scripted brain: position -> lanes -> economics -> propose -> submit.

ignore_freshness=True is the model that stopped reading: it takes the stale DC the lane tool
excluded, asks the economics tool to price it anyway, is refused by propose_transfer, and submits
the move regardless. The trap evals expect no transfer, so they fail on it, which is the point.
"""
from __future__ import annotations

from foundry.scripted import ScriptedClient, result_of


def _none(task: dict, to_dc: str | None, sku: str | None, reason: str, alternative: str | None = None) -> tuple[str, dict]:
    return "submit_plan", {"to_dc": to_dc or "ALL", "from_dc": None, "sku": sku, "week": task.get("week"), "cases": 0,
                           "lane_days": None, "cost_inr": 0, "margin_saved_inr": 0, "pays": False, "co_sign_needed": False,
                           "receiver_dos_before": None, "receiver_dos_after": None, "leaves_sender_dos": None,
                           "reason": reason, "alternative": alternative, "proposal_id": None,
                           "summary": f"No transfer for {sku or 'any SKU'}: {reason}"}


class FakeRebalancingClient(ScriptedClient):
    def __init__(self, fail_at_turn: int | None = None, ignore_freshness: bool = False):
        super().__init__(fail_at_turn)
        self.ignore_freshness = ignore_freshness

    def next_call(self, task: dict, results: list[dict], seen: list[str]) -> tuple[str, dict]:
        week = task["week"]
        if task.get("mode") == "sweep":
            if "list_shortages" not in seen:
                return "list_shortages", {"week": week}
            rows = result_of(results, "list_shortages")["shortages"]
            with_partner = [r for r in rows if r["partner"]]
            if not with_partner:
                worst = rows[0] if rows else None
                return _none(task, worst["dc"] if worst else None, worst["sku"] if worst else None,
                             worst["reason"] if worst else "no A/B SKU is short anywhere this week")
            top = with_partner[0]            # re-derived every turn: the task text does not change
            task.update({"sku": top["sku"], "to_dc": top["dc"]})
        sku = task.get("sku")
        if "stock_position" not in seen:
            return "stock_position", {"sku": sku, "week": week}
        pos = result_of(results, "stock_position")
        to_dc = task.get("to_dc")
        if not to_dc:
            shorts = [r for r in pos["position"] if r["status"] == "short"]
            if not shorts:
                return _none(task, None, sku, f"no DC is short of {sku}")
            to_dc = min(shorts, key=lambda r: r["dos"])["dc"]
            task["to_dc"] = to_dc
        if "lane_options" not in seen:
            return "lane_options", {"to_dc": to_dc, "sku": sku, "week": week}
        opts = result_of(results, "lane_options")
        cands = opts["candidates"]
        stale = [e["dc"] for e in opts.get("excluded", []) if "snapshot" in e["why"]]
        if "transfer_economics" not in seen:
            if self.ignore_freshness and stale:
                pick_dc, cases = stale[0], max(opts["need_cases"], 1)
            elif not cands:
                excl = opts.get("excluded", [])
                why = ("no fresh long DC with a lane: " + "; ".join(f"{e['dc']} {e['why']}" for e in excl) if excl
                       else f"no other DC carries {sku}: it is sold from {to_dc} alone (a pack for one market)")
                return _none(task, to_dc, sku, why, "refresh_snapshot" if stale else "expedite_plant_run")
            else:
                good = [c for c in cands if c["arrives_in_time"] and c.get("useful", c["lands_before_next_receipt"])]
                pick = good[0] if good else cands[0]
                pick_dc, cases = pick["from_dc"], max(1, min(opts["need_cases"], pick["surplus_hard_cases"]))
            return "transfer_economics", {"from_dc": pick_dc, "to_dc": to_dc, "sku": sku, "cases": cases, "week": week}
        eco_out = result_of(results, "transfer_economics")
        rec = eco_out.get("recommended")
        eco = eco_out.get("economics", {})
        if self.ignore_freshness and stale and rec is None:
            rec = {**eco, "from_dc": stale[0], "to_dc": to_dc, "cases": eco.get("cases", 0), "lane_days": eco.get("lane_days"),
                   "lane_cost": eco.get("lane_cost", 0), "margin_saved": eco.get("margin_saved", 0), "pays": True,
                   "co_sign_needed": False, "receiver_dos_before": eco.get("receiver_dos_before"), "receiver_dos_after": eco.get("receiver_dos_after"),
                   "leaves_sender_dos": eco.get("leaves_sender_dos"), "summary": "moved from the stale DC anyway"}
        if rec is None:
            return _none(task, to_dc, sku, eco_out.get("recommendation_reason", "the maths recommends no move"), eco_out.get("alternative"))
        if "propose_transfer" not in seen:
            return "propose_transfer", {"from_dc": rec["from_dc"], "to_dc": rec["to_dc"], "sku": sku, "cases": rec["cases"], "week": week,
                                        "rationale": rec.get("summary", "")}
        prop = result_of(results, "propose_transfer", {}) or {}
        return "submit_plan", {
            "to_dc": rec["to_dc"], "from_dc": rec["from_dc"], "sku": sku, "week": week, "cases": rec["cases"],
            "lane_days": rec["lane_days"], "cost_inr": rec["lane_cost"], "margin_saved_inr": rec["margin_saved"], "pays": rec["pays"],
            "co_sign_needed": rec["co_sign_needed"], "receiver_dos_before": rec["receiver_dos_before"], "receiver_dos_after": rec["receiver_dos_after"],
            "leaves_sender_dos": rec["leaves_sender_dos"], "reason": eco_out.get("recommendation_reason", ""), "alternative": None,
            "proposal_id": prop.get("proposal_id"), "summary": rec.get("summary", ""),
        }
