"""The console's carbon column: kg CO2e for every step of a tower plan, and for the plan, with the method
that produced each number. Read-only: the column is shown next to the money and changes nothing.

Every step of the plan gets a row, with a status that says what the number is:
  computed       a freight move with cases on it (inter_dc_transfer): the footprint by the profile's
                 decision method (vehicle: the trucks this move sends)
  estimated      a freight move whose cases the step does not carry (expedite_alternate_carrier): the
                 slipped cases from the store's late shipments, on the carrier quote the step asks for
  with_transfer  book_truck, co_sign_transfer: counted once, on the transfer step they belong to
  plant_energy   expedite_plant_run, raise_production_lot: plant energy sits in the inventory (scopes 1-2)
  no_freight     a step that moves nothing (inform_customers, a dock slot, a forecast): 0 kg
  not_estimated  a freight step the data cannot price (a relief truck whose km depend on where the
                 load stopped): no number, and the reason, never a silent zero

Fixed 8 Nov 2026 (day-one defect 1): the old carbon_column read the SKU from each step, but the tower's
inter_dc_transfer step carries no SKU (it is on the plan), so the one step that moves cases was
skipped and the plan showed no carbon at all. The SKU now comes from the step or, failing that, the plan;
a step that still has none is reported `not_estimated`, not dropped.
"""
from __future__ import annotations

from foundry.carbon.factors import load, trucks_for
from foundry.carbon.logistics import road_kg, transfer_footprint
from foundry.profile import Profile
from foundry.store import Store

COMPANIONS = ("book_truck", "co_sign_transfer")
PLANT_STEPS = ("expedite_plant_run", "raise_production_lot")
EXPEDITE_PREFERENCE = ("express_now", "dedicated_now")      # the step says "an alternate carrier on an express lane"


def plan_label(plan: dict) -> str:
    """A stable name for a plan (plan ids carry a random suffix): DC, SKU, version, status."""
    return f"{plan.get('dc', '?')} {plan.get('sku') or 'all SKUs'} v{plan.get('version', 1)} {plan.get('status', '')}".strip()


def slipped_cases(store: Store, profile: Profile, week: int, dc: str, sku: str) -> dict | None:
    """The cases on late trucks into this DC for this SKU in this week, on the lane with most of them."""
    late = float(profile.diagnosis.get("late_truck_days", 1.0))
    by_lane: dict[str, float] = {}
    for r in store.shipments:
        if r["week"] == week and r["lane_to"] == dc and r["sku"] == sku and float(r.get("late_days") or 0) >= late:
            by_lane[r["lane_from"]] = by_lane.get(r["lane_from"], 0.0) + float(r["cases"])
    if not by_lane:
        return None
    src = max(sorted(by_lane), key=lambda k: by_lane[k])
    return {"from": src, "cases": int(round(by_lane[src])), "basis": f"store shipments week {week}, late >= {late:g} day"}


def _expedite_estimate(store: Store, profile: Profile, plan: dict, step: dict, week: int, sku: str | None) -> dict:
    f = load(profile)
    dc = step.get("to_dc") or plan["dc"]
    if not sku:
        return {"status": "not_estimated", "why": "no SKU on the step or the plan (a DC-wide plan): the slipped cases are unknown"}
    slip = slipped_cases(store, profile, week, dc, sku)
    if not slip:
        inflight = [t for t in store.extra.get("transfers_inflight", []) if t.get("to_dc") == dc and t.get("sku") == sku]
        why = ("a relief truck's km depend on where the load stopped; take the carrier's fuel or GPS data" if inflight
               else f"no late shipment of {sku} into {dc} in week {week}: nothing to price")
        return {"status": "not_estimated", "why": why}
    a, cases = slip["from"], slip["cases"]
    quotes = (f.raw.get("expedite") or {}).get(f"{a}-{dc}") or {}
    pick = next((q for q in EXPEDITE_PREFERENCE if q in quotes), None)
    q = quotes.get(pick, {}) if pick else {}
    vehicle = q.get("vehicle", f.default_vehicle)
    km, km_src = f.km(store, a, dc)
    if km is None:
        return {"status": "not_estimated", "why": km_src}
    kgc, _ = f.kg_per_case(store, sku)
    cap = f.cases_per_vehicle(store, sku, vehicle)
    trucks = trucks_for(cases, cap)
    r = road_kg(f, km, cases * kgc / 1000.0, trucks, "vehicle", vehicle)
    quote = f"quote {pick}" if pick else "no quote in the profile: a dedicated default truck"
    short = f"est.: {cases:,} slipped cases {a}->{dc} on {pick or 'a dedicated truck'} ({trucks} x {vehicle}) [{r['source']}]"
    return {"status": "estimated", "short": short, "kg_co2e": round(r["kg"]), "ttw_kg": round(r["ttw_kg"]), "wtt_kg": round(r["wtt_kg"]),
            "method": "vehicle (estimate)", "cases": cases, "from_dc": a, "to_dc": dc, "trucks": trucks, "vehicle": vehicle,
            "label": f"estimate: {cases:,} slipped cases ({slip['basis']}) re-sent {a}->{dc} on {quote}; {r['label']}",
            "source": r["source"], "cost_inr": round(cases * q["cost_per_case"]) if q.get("cost_per_case") else None}


def step_carbon(store: Store, profile: Profile, plan: dict, step: dict, week: int | None = None) -> dict:
    """One step's row in the column."""
    f = load(profile)
    code = step.get("code", "")
    week = int(week if week is not None else plan.get("week") or 1)
    sku = step.get("sku") or plan.get("sku")
    row = {"n": step.get("n"), "code": code, "approver": step.get("approver"), "kg_co2e": None, "method": None, "label": "",
           "source": None, "status": "no_freight"}
    if code == "inter_dc_transfer":
        if not step.get("cases"):
            return {**row, "status": "not_estimated", "label": "the transfer step carries no cases yet"}
        if not sku:
            return {**row, "status": "not_estimated", "label": "no SKU on the step or the plan"}
        to_dc = step.get("to_dc") or plan.get("dc")
        fp = transfer_footprint(store, profile, {"from_dc": step["from_dc"], "to_dc": to_dc, "sku": sku, "cases": step["cases"],
                                                 "lane_days": step.get("lane_days"), "mode": step.get("mode")})
        if "error" in fp:
            return {**row, "status": "not_estimated", "label": fp["error"]}
        short = (f"{fp.get('trucks')} x {fp.get('vehicle')} {step['from_dc']}->{to_dc} {fp['km']:,.0f} km, load {fp.get('load_factor') or 0:.0%}, "
                 f"{fp.get('g_per_case'):,} g/case [{fp.get('source')}]")
        return {**row, "status": "computed", "short": short, "kg_co2e": fp["kg_co2e"], "ttw_kg": fp["ttw_kg"], "wtt_kg": fp["wtt_kg"],
                "method": fp["method"], "label": fp["factor_label"], "source": fp.get("source"), "trucks": fp.get("trucks"),
                "load_factor": fp.get("load_factor"), "g_per_case": fp.get("g_per_case"), "cases": fp["cases"],
                "from_dc": step["from_dc"], "to_dc": to_dc, "shadow_cost_inr": fp["shadow_cost_inr"],
                "cost_inr": step.get("cost_inr"), "margin_saved_inr": step.get("margin_saved_inr")}
    if code == "expedite_alternate_carrier":
        est = _expedite_estimate(store, profile, plan, step, week, sku)
        if est["status"] == "not_estimated":
            return {**row, "status": "not_estimated", "label": est["why"]}
        return {**row, **est, "shadow_cost_inr": round(est["kg_co2e"] / 1000.0 * f.shadow_price)}
    if code in COMPANIONS:
        t = next((s for s in plan.get("steps", []) if s.get("code") == "inter_dc_transfer" and s.get("cases")), None)
        where = f"step {t['n']}" if t and t.get("n") else "the transfer step"
        return {**row, "status": "with_transfer", "kg_co2e": 0, "label": f"counted once, on {where}"}
    if code in PLANT_STEPS:
        return {**row, "status": "plant_energy", "label": "plant energy is in the inventory (scopes 1 and 2), not priced per plan"}
    return {**row, "kg_co2e": 0, "label": "moves nothing"}


def plan_column(store: Store, profile: Profile, plan: dict | list, sku: str | None = None, week: int | None = None) -> dict:
    """The column for one tower plan (a plan record with `steps`, or a bare list of steps plus `sku`)."""
    f = load(profile)
    if isinstance(plan, list):
        plan = {"steps": plan, "sku": sku, "dc": next((s.get("to_dc") for s in plan if s.get("to_dc")), None), "week": week}
    rows = [step_carbon(store, profile, plan, s, week) for s in plan.get("steps", [])]
    computed = sum(r["kg_co2e"] or 0 for r in rows if r["status"] == "computed")
    estimated = sum(r["kg_co2e"] or 0 for r in rows if r["status"] == "estimated")
    total = computed + estimated
    return {"plan": plan_label(plan), "plan_id": plan.get("plan_id"), "dc": plan.get("dc"), "sku": plan.get("sku"),
            "week": plan.get("week", week), "status": plan.get("status"), "version": plan.get("version"), "steps": rows,
            "computed_kg": round(computed), "estimated_kg": round(estimated), "total_kg": round(total),
            "shadow_price_inr_per_t": f.shadow_price, "shadow_cost_inr": round(total / 1000.0 * f.shadow_price),
            "shadow_applied": False, "methods": sorted({r["method"] for r in rows if r.get("method")}),
            "not_estimated": [r["n"] for r in rows if r["status"] == "not_estimated"],
            "money": {"cost_inr": sum(int(s.get("cost_inr") or 0) for s in plan.get("steps", [])),
                      "margin_saved_inr": sum(int(s.get("margin_saved_inr") or 0) for s in plan.get("steps", []))}}


def week_column(rt, week: int) -> dict:
    """Every tower plan of a week (in the order the tower made them), as the console would show them."""
    cols = [plan_column(rt.store, rt.profile, p) for p in rt.plans.values() if p.get("week") == week]
    applied = sum(c["total_kg"] for c in cols if c["status"] == "APPLIED")
    never = sum(c["total_kg"] for c in cols if c["status"] in ("SUPERSEDED", "REJECTED", "FAILED"))
    waiting = sum(c["total_kg"] for c in cols) - applied - never
    return {"week": week, "plans": cols, "applied_kg": applied, "waiting_kg": waiting, "never_sent_kg": never,
            "note": "superseded and rejected plans never sent a truck: their kg are shown on the plan, never added to a total"}


def api_view(rt, rest: list[str], query: dict) -> tuple[int, dict]:
    """GET /api/carbon/<plan_id> and GET /api/carbon?week=12 (console hook, read-only)."""
    if rest:
        rec = rt.plans.get(rest[0])
        if not rec:
            return 404, {"error": f"no plan {rest[0]}"}
        return 200, plan_column(rt.store, rt.profile, rec)
    weeks = sorted({p["week"] for p in rt.plans.values()})
    week = int(query.get("week") or (weeks[-1] if weeks else 0))
    return 200, week_column(rt, week)


def render(cols: list[dict]) -> list[str]:
    """The column as text, for the CLI and the book."""
    lines = []
    for c in cols:
        flag = f"; {len(c['not_estimated'])} step(s) not estimated" if c["not_estimated"] else ""
        lines.append(f"{c['plan']}: {c['total_kg']:,} kg CO2e (computed {c['computed_kg']:,}, estimated {c['estimated_kg']:,}){flag}; "
                     f"shadow INR {c['shadow_cost_inr']:,} shown, not applied")
        for r in c["steps"]:
            kg = "     -" if r["kg_co2e"] is None else f"{r['kg_co2e']:6,}"
            lines.append(f"   {r['n'] or '-':>2} {r['code']:27} {kg} kg  {r['status']:13} {(r.get('short') or r['label'])[:92]}")
    return lines
