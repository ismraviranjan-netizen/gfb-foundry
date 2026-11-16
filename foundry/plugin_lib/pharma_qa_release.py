"""Pharma distributor: a QA hold is a cause of its own, and nothing moves before QA releases it.

Adds the cause QA_HOLD (read from a qa_holds table: week, dc, sku, cases_held, reason) and the step
qa_release in front of any playbook that moves stock between sites.
"""
from __future__ import annotations

from foundry.agents.rca_exception.diagnosis import Evidence, _clamp


def ev_qa_hold(store, profile, week, dc, sku):
    rows = [r for r in store.rows("qa_holds", week=week, dc=dc) if sku is None or r["sku"] == sku]
    held = sum(float(r.get("cases_held", 0)) for r in rows)
    planned = sum(float(r.get("planned", 0)) for r in store.rows("receipts", week=week, dc=dc, sku=sku)) if sku else 0.0
    demand = store.net.weekly_mean(sku, dc) if sku else 0.0
    base = max(planned, demand, 1.0)
    share = held / base
    thr = float(profile.section("qa").get("hold_share", 0.10))
    score = _clamp(share / (2 * thr)) if share >= thr else 0.0
    reasons = sorted({r.get("reason", "") for r in rows if r.get("reason")})
    return Evidence("QA_HOLD", round(score, 2), {"cases_held": held, "share_of_week": round(share, 2), "reasons": reasons},
                    f"{held:.0f} cases of {sku or 'stock'} at {dc} on QA hold ({share:.0%} of the week; {', '.join(reasons) or 'no reason recorded'})"
                    if held else f"no QA holds at {dc} this week")


def register(p) -> None:
    p.add_cause("QA_HOLD", ev_qa_hold, ["qa_release", "expedite_qa_retest", "allocate_fair_share", "inform_customers"],
                {"qa_release": "QA releases the held lot (or confirms the rejection) before anything else moves",
                 "expedite_qa_retest": "Bring the retest forward; a released lot ends the shortage faster than a transfer"})
    p.add_step("qa_release", "QA releases the lot before it moves between sites", position="first", when_action="inter_dc_transfer")
