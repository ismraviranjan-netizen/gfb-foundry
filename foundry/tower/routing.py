"""The deterministic part of the tower: who to call, how to merge, who signs.

ROUTES   (root cause, contributing) -> the specialists worth asking after the diagnosis
MERGE    one plan from several findings: one action per approver per week, ordered by the money,
         a specialist's priced verdict beating the diagnosis's generic step for the same action
"""
from __future__ import annotations

SPECIALISTS = ("rca", "rebalancing", "sensing")

# which specialists a cause earns, beyond the RCA agent that every breach gets first
ROUTES = {
    "TRANSPORT_DELAY": ["rebalancing"],          # a late lane with a long DC next door: price the transfer
    "SUPPLY_MISS": ["rebalancing"],              # the plant shipped short: is there stock elsewhere
    "DEMAND_SPIKE": ["rebalancing", "sensing"],  # sell-out ran above plan: move stock, re-sense the weeks ahead
    "INVENTORY_IMBALANCE": ["rebalancing"],
    "DOCK_CONGESTION": [],                       # the dock is the dock; no specialist moves stock to fix it
    "DATA_STALE": [],                            # nothing moves on a bad number
    "NO_CLEAR_CAUSE": [],
    "PROJECTED_SHORTAGE": ["rebalancing"],       # The Control Room: the radar sees a stockout coming; price a move before it lands
}

# actions the diagnosis proposes generically that a specialist prices; the specialist's verdict wins
PRICED_BY = {"inter_dc_transfer": "rebalancing", "adjust_forecast": "sensing"}


def route(root_cause: str, contributing: list[str], week: int, closed_weeks: int, min_weeks_for_sensing: int = 12,
          extra: dict[str, list[str]] | None = None) -> list[str]:
    """extra: a company's plug-in routes (Plugins.routes); they win over the core table for their cause."""
    calls = list((extra or {}).get(root_cause, ROUTES.get(root_cause, [])))
    if "INVENTORY_IMBALANCE" in (contributing or []) and "rebalancing" not in calls:
        calls.append("rebalancing")
    if "sensing" in calls and closed_weeks < min_weeks_for_sensing:
        calls.remove("sensing")                    # the sensing agent would only say "series too short"
    return calls


def merge(diagnosis: dict, rebalancing: dict | None, sensing: dict | None, disabled: list[str] | None = None) -> dict:
    """Build the plan. diagnosis: the RCA submit (root_cause, actions[], impact_inr, priority, sla_hours,
    proposal_id, approvers by code if given). rebalancing: the specialist's plan (transfer or None,
    reason, proposal_id, approvals[]). sensing: the specialist's result (decision, adjustments, proposal_id)."""
    disabled = disabled or []
    steps: list[dict] = []
    dropped: list[dict] = []
    rca_actions = diagnosis.get("actions_detail") or [{"code": c, "approver": None, "text": c} for c in diagnosis.get("actions", [])]
    for a in rca_actions:
        code = a["code"]
        owner = PRICED_BY.get(code)
        if owner == "rebalancing":
            if "rebalancing" in disabled:
                steps.append({**a, "source": "rca", "note": "rebalancing specialist disabled; transfer unpriced, approve with care"})
                continue
            if rebalancing is None:
                steps.append({**a, "source": "rca", "note": "specialist not consulted"})
                continue
            t = rebalancing.get("transfer")
            if t:
                steps.append({"code": "inter_dc_transfer", "source": "rebalancing", "approver": _approver(rebalancing, "inter_dc_transfer") or a.get("approver"),
                              "text": t.get("summary") or a.get("text"), "cases": t.get("cases"), "from_dc": t.get("from_dc"), "to_dc": t.get("to_dc"),
                              "lane_days": t.get("lane_days"), "lands_end_of_day": t.get("lands_end_of_day") or t.get("lane_days"),
                              "cost_inr": t.get("lane_cost"), "margin_saved_inr": t.get("margin_saved"),
                              "proposal_id": rebalancing.get("proposal_id")})
                for extra in _companions(rebalancing):
                    ap = _approver(rebalancing, extra)
                    if ap:
                        steps.append({"code": extra, "source": "rebalancing", "approver": ap, "text": _text(rebalancing, extra), "proposal_id": rebalancing.get("proposal_id")})
            else:
                dropped.append({"code": code, "by": "rebalancing", "reason": rebalancing.get("reason", "no transfer pays")})
                alt = rebalancing.get("alternative")
                if alt and alt not in [s["code"] for s in steps] and alt not in [x["code"] for x in rca_actions]:
                    steps.append({"code": alt, "source": "rebalancing", "approver": None, "text": f"{alt} (the specialist's alternative to a transfer)"})
            continue
        steps.append({**a, "source": "rca"})
    priced = any(PRICED_BY.get(a["code"]) == "rebalancing" for a in rca_actions)
    if rebalancing is not None and not priced and "rebalancing" not in disabled:
        # the diagnosis asked for no transfer, but the specialist was consulted: its verdict must show (8 Nov)
        t = rebalancing.get("transfer")
        if t:
            steps.append({"code": "inter_dc_transfer", "source": "rebalancing", "approver": _approver(rebalancing, "inter_dc_transfer"),
                          "text": t.get("summary"), "cases": t.get("cases"), "from_dc": t.get("from_dc"), "to_dc": t.get("to_dc"),
                          "lane_days": t.get("lane_days"), "lands_end_of_day": t.get("lands_end_of_day") or t.get("lane_days"),
                          "cost_inr": t.get("lane_cost"), "margin_saved_inr": t.get("margin_saved"), "proposal_id": rebalancing.get("proposal_id")})
            for extra in _companions(rebalancing):
                ap = _approver(rebalancing, extra)
                if ap:
                    steps.append({"code": extra, "source": "rebalancing", "approver": ap, "text": _text(rebalancing, extra), "proposal_id": rebalancing.get("proposal_id")})
        else:
            dropped.append({"code": "inter_dc_transfer", "by": "rebalancing", "reason": "considered, not proposed: " + (rebalancing.get("reason") or "no transfer pays")})
    if sensing and sensing.get("decision") == "adjust":
        steps.append({"code": "adjust_forecast", "source": "sensing", "approver": _approver(sensing, "adjust_forecast"),
                      "text": sensing.get("summary", "adjust the short-horizon forecast"), "adjustments": sensing.get("adjustment_pct"),
                      "proposal_id": sensing.get("proposal_id")})
        cs = _approver(sensing, "co_sign_forecast")
        if cs:
            steps.append({"code": "co_sign_forecast", "source": "sensing", "approver": cs, "text": "co-sign the forecast adjustment", "proposal_id": sensing.get("proposal_id")})
    elif sensing and sensing.get("decision") == "silent":
        dropped.append({"code": "adjust_forecast", "by": "sensing", "reason": "; ".join(sensing.get("reasons", [])[:1]) or "the blend did not beat the consensus"})
    # one step per approver and action: keep the first (highest-ranked) step for each (approver, code) pair
    seen = set()
    plan = []
    for s in steps:
        key = (s.get("approver"), s["code"])
        if key in seen:
            continue
        seen.add(key)
        plan.append(s)
    return {"plan": plan, "dropped": dropped}


def _companions(res: dict) -> list[str]:
    """The steps a priced transfer brings with it, in the specialist's order: the co-sign, any paperwork a
    plug-in lane rule adds (an excise document), the truck. GFB's are co_sign_transfer and book_truck."""
    codes = [a.get("code") for a in (res.get("approvals") or []) if a.get("code") not in (None, "inter_dc_transfer")]
    return codes or ["co_sign_transfer", "book_truck"]


def _approver(res: dict, code: str) -> str | None:
    for a in res.get("approvals", []) or res.get("actions_detail", []) or []:
        if a.get("code") == code:
            return a.get("approver")
    return None


def _text(res: dict, code: str) -> str:
    for a in res.get("approvals", []) or []:
        if a.get("code") == code:
            return a.get("text", code)
    return code


def approvals(plan: list[dict], priority: str, sla_hours: int) -> list[dict]:
    """Who signs what, the P1 clock first. Steps with no approver (an alternative the specialist
    named) are routed to the step's default role by the caller before this runs."""
    out = []
    for i, s in enumerate(plan, 1):
        out.append({"step": i, "code": s["code"], "approver": s.get("approver") or "unassigned", "sla_hours": sla_hours,
                    "priority": priority, "proposal_id": s.get("proposal_id")})
    return out
