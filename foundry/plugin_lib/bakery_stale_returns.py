"""DSD bakery: stale returns are both a cost and a cause. A route whose returns jump is a route
whose fresh stock went to the wrong stores (or whose shelf life was shortened upstream).

Adds the cause STALE_RETURNS from a returns table (week, dc, sku, delivered, returned).
"""
from __future__ import annotations

from foundry.agents.rca_exception.diagnosis import Evidence, _clamp


def ev_stale_returns(store, profile, week, dc, sku):
    rows = [r for r in store.rows("returns", week=week, dc=dc) if sku is None or r["sku"] == sku]
    delivered = sum(float(r.get("delivered", 0)) for r in rows)
    returned = sum(float(r.get("returned", 0)) for r in rows)
    rate = returned / delivered if delivered else 0.0
    base_rows = [r for r in store.rows("returns", dc=dc) if (sku is None or r["sku"] == sku) and week - 8 <= r["week"] < week]
    bd = sum(float(r.get("delivered", 0)) for r in base_rows)
    br = sum(float(r.get("returned", 0)) for r in base_rows)
    base_rate = br / bd if bd else float(profile.section("bakery").get("normal_return_rate", 0.06))
    thr = float(profile.section("bakery").get("return_rate_factor", 1.8))
    factor = rate / base_rate if base_rate else 0.0
    score = _clamp(0.5 + 0.5 * (factor - thr) / thr) if factor >= thr else (_clamp((factor - 1.2) / (thr - 1.2)) * 0.4 if factor > 1.2 else 0.0)
    return Evidence("STALE_RETURNS", round(score, 2), {"return_rate": round(rate, 3), "baseline_rate": round(base_rate, 3), "factor": round(factor, 2)},
                    f"stale returns at {dc} ran {rate:.1%} against a baseline of {base_rate:.1%} ({factor:.1f}x)" if delivered else f"no returns data at {dc}")


def register(p) -> None:
    p.add_cause("STALE_RETURNS", ev_stale_returns, ["re_cut_route_allocation", "shorten_code_date_window", "inform_customers"],
                {"re_cut_route_allocation": "Re-cut the route's store allocations from last week's sell-through, not the plan",
                 "shorten_code_date_window": "Tighten the code-date window at dispatch so the oldest cases do not go to the slowest stores"})
