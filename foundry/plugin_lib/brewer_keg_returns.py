"""Global brewer: empty kegs that did not come back are a cause of short supply.

Adds the cause KEG_RETURN_SHORT from a keg_returns table (week, dc, sku, needed, returned).
"""
from __future__ import annotations

from foundry.agents.rca_exception.diagnosis import Evidence, _clamp


def ev_keg_return_short(store, profile, week, dc, sku):
    rows = [r for r in store.rows("keg_returns", week=week, dc=dc) if sku is None or r["sku"] == sku]
    needed = sum(float(r.get("needed", 0)) for r in rows)
    returned = sum(float(r.get("returned", 0)) for r in rows)
    short = (needed - returned) / needed if needed else 0.0
    thr = float(profile.section("kegs").get("return_short_pct", 0.15))
    score = _clamp(short / (2 * thr)) if short >= thr else 0.0
    return Evidence("KEG_RETURN_SHORT", round(score, 2), {"needed": needed, "returned": returned, "short_pct": round(short * 100)},
                    f"{returned:.0f} of {needed:.0f} empty kegs returned at {dc} ({short:.0%} short): the fill plan had nothing to fill"
                    if needed else f"no keg return data at {dc} this week")


def register(p) -> None:
    p.add_cause("KEG_RETURN_SHORT", ev_keg_return_short, ["collect_empties", "allocate_fair_share", "inform_customers"],
                {"collect_empties": "Run an extra empties collection round from the retailers with the largest keg float"})
