"""Carbon next to the money, for the person who signs.

The rule: the money decides, as before; carbon is a visible column with its own price (the
profile's shadow price), never a silent override. What the tower may do is *offer* an option the
money alone would not have surfaced (fill the trucks you send anyway; rail when the days do not
matter; wait for the next run instead of an express truck) and say what carbon would have to cost to
change the answer (the break-even price). The approver card puts all of it on one page: the plan's
money, its carbon, the shadow price shown and not applied, and the offers.
"""
from __future__ import annotations

import dataclasses

from foundry.agents.rebalancing import logic as rbl
from foundry.carbon.column import plan_column, plan_label, slipped_cases
from foundry.carbon.factors import load, trucks_for
from foundry.carbon.logistics import road_kg, transfer_footprint
from foundry.profile import Profile
from foundry.risk import projection as clock
from foundry.store import Store


def _with_lane(store: Store, a: str, b: str, days: int, cost_per_case: float) -> Store:
    """The same store with one lane re-quoted (a rail option): a copy, never a change to the original."""
    net = dataclasses.replace(store.net, lanes={**store.net.lanes, (a, b): (int(days), float(cost_per_case))})
    return dataclasses.replace(store, net=net)


def option(store: Store, profile: Profile, week: int, sku: str, from_dc: str, to_dc: str, cases: int,
           mode: str = "road", label: str | None = None, vehicle: str | None = None) -> dict:
    """One way to make the move: the rebalancing economics and the footprint, side by side."""
    f = load(profile)
    s = store
    if mode == "rail":
        lane = f.rail_lane(from_dc, to_dc)
        if not lane:
            return {"label": label or "rail", "mode": "rail", "error": f"no rail option {from_dc}->{to_dc}"}
        s = _with_lane(store, from_dc, to_dc, lane["days"], lane["cost_per_case"])
    eco = rbl.economics(s, profile, week, sku, from_dc, to_dc, int(cases))
    if "error" in eco:
        return {"label": label or mode, "mode": mode, "error": eco["error"]}
    fp = transfer_footprint(store, profile, {"from_dc": from_dc, "to_dc": to_dc, "sku": sku, "cases": cases,
                                             "lane_days": eco["lane_days"]}, mode=mode, vehicle=vehicle)
    net_inr = eco["margin_saved"] - eco["lane_cost"] - eco["holding_cost"]
    return {"label": label or f"{mode} {int(cases):,} cases", "mode": mode, "cases": int(cases), "trucks": fp.get("trucks"),
            "load_factor": fp.get("load_factor"), "lane_days": eco["lane_days"], "lane_cost": eco["lane_cost"],
            "holding_cost": eco["holding_cost"], "margin_saved": eco["margin_saved"], "days_short_avoided": eco["days_short_avoided"],
            "net_inr": round(net_inr), "pays": eco["pays"], "kg_co2e": fp["kg_co2e"], "shadow_cost_inr": fp["shadow_cost_inr"],
            "net_after_shadow_inr": round(net_inr - fp["shadow_cost_inr"]), "g_per_case": fp["g_per_case"],
            "method": fp["method"], "factor_label": fp["factor_label"], "source": fp.get("source")}


def breakeven_price(a: dict, b: dict, kg_key: str = "kg_co2e") -> float | None:
    """The carbon price (INR per tonne) at which b would match a. a earns more and emits more; else None."""
    d_money = a["net_inr"] - b["net_inr"]
    d_t = (a[kg_key] - b[kg_key]) / 1000.0
    if d_money <= 0 or d_t <= 0:
        return None
    return round(d_money / d_t)


def compare_modes(store: Store, profile: Profile, week: int, transfer: dict) -> dict:
    """Road as proposed against rail where the profile has a rail option for the lane."""
    a, b, sku, cases = transfer["from_dc"], transfer["to_dc"], transfer["sku"], int(transfer["cases"])
    road = option(store, profile, week, sku, a, b, cases, "road", "road, as proposed")
    rail = option(store, profile, week, sku, a, b, cases, "rail", "container rail")
    out = {"options": [road, rail], "breakeven_inr_per_t": None, "shadow_price_inr_per_t": load(profile).shadow_price}
    if "error" in road:
        out["note"] = road["error"]
        return out
    if "error" in rail:
        out["note"] = rail["error"]
        return out
    be = breakeven_price(road, rail) if road["net_inr"] >= rail["net_inr"] else None
    out["breakeven_inr_per_t"] = be
    saved_t = (road["kg_co2e"] - rail["kg_co2e"]) / 1000.0
    if road["net_inr"] >= rail["net_inr"]:
        out["note"] = (f"road earns INR {road['net_inr'] - rail['net_inr']:,} more; rail saves {saved_t:.1f} t CO2e and lands "
                       f"{rail['lane_days'] - road['lane_days']} days later; carbon would have to cost INR {be:,} a tonne to flip it"
                       if be else "road earns more and emits no more: road")
    else:
        out["note"] = f"rail earns INR {rail['net_inr'] - road['net_inr']:,} more and saves {saved_t:.1f} t CO2e: offer rail"
    return out


def right_size(store: Store, profile: Profile, week: int, transfer: dict) -> dict:
    """Does the last truck carry its weight? Compare the move as sized with the trucks filled and with
    the part truck dropped. The money still decides; the carbon shows which trucks are buying it."""
    a, b, sku, cases = transfer["from_dc"], transfer["to_dc"], transfer["sku"], int(transfer["cases"])
    tc = store.net.truck_cases
    trucks = trucks_for(cases, tc)
    base = option(store, profile, week, sku, a, b, cases, "road", f"as sized: {cases:,} cases")
    rows = [base]
    if "error" in base:
        return {"options": rows, "best": base["label"], "note": base["error"], "truck_cases": tc}
    if cases % tc:
        cfg = rbl._cfg(profile)
        drop = (trucks - 1) * tc
        if drop >= cfg["min_transfer_cases"]:
            rows.append(option(store, profile, week, sku, a, b, drop, "road", f"drop the part truck: {drop:,} cases"))
        fill = trucks * tc
        pos = {r["dc"]: r for r in rbl.position(store, profile, week, sku)}
        hard = rbl.surplus(pos[a], profile)[1] if a in pos else 0
        if fill <= hard:
            rows.append(option(store, profile, week, sku, a, b, fill, "road", f"fill the last truck: {fill:,} cases"))
    best = max(rows, key=lambda r: (r.get("net_inr", -1e18), -r.get("kg_co2e", 0)))
    note = "the move as sized is the best on the money"
    if best is not base:
        note = (f"{best['label']} saves INR {best['net_inr'] - base['net_inr']:,} and {(base['kg_co2e'] - best['kg_co2e']) / 1000:.1f} t CO2e "
                f"against the move as sized; margin saved {best['margin_saved']:,} vs {base['margin_saved']:,}: offer it, the DC manager decides")
    return {"options": rows, "best": best["label"], "note": note, "truck_cases": tc}


def expedite_options(store: Store, profile: Profile, week: int, sku: str, from_site: str, to_dc: str, cases: int) -> dict:
    """A rush move priced three ways from the profile's carrier quotes (carbon.expedite): wait for the next
    scheduled run, a dedicated truck now, an express (smaller, faster) truck now.

    Two carbon numbers per option, because they answer two questions:
      kg_co2e   what the report will carry: a shared run is allocated by tonne-km at the default load
                (ISO 14083 allocation); a dedicated truck carries its whole trip (vehicle method)
      kg_added  what this choice adds to the world: 0 for a run that goes anyway (the extra fuel its
                weight burns is not modelled), the whole trip for a truck sent for these cases
    The shadow price is shown on kg_added; the money (margin saved by landing earlier, from the shared
    projection, less the freight) decides, and the logistics lead signs."""
    f = load(profile)
    quotes = (f.raw.get("expedite") or {}).get(f"{from_site}-{to_dc}") or {}
    out = {"from": from_site, "to_dc": to_dc, "sku": sku, "cases": int(cases), "week": week, "options": [],
           "shadow_price_inr_per_t": f.shadow_price, "source": f.source("expedite")}
    if not quotes:
        out["note"] = f"no expedite quotes for {from_site}->{to_dc} in the profile"
        return out
    km, km_src = f.km(store, from_site, to_dc)
    if km is None:
        out["note"] = km_src
        return out
    kgc, _ = f.kg_per_case(store, sku)
    tonnes = cases * kgc / 1000.0
    day0 = int(getattr(store, "dispatch_day", 0) or 0)
    for name, q in quotes.items():
        vehicle = q.get("vehicle", f.default_vehicle)
        lands = day0 + int(q.get("wait_days", 0)) + int(q["days"])
        freight = round(cases * float(q["cost_per_case"]))
        wi = clock.what_if(store, profile, week, to_dc, sku, cases, lands, source=f"expedite {name}")
        margin = round(max(0.0, float(wi["money_avoided"])))
        if q.get("shared"):
            r = road_kg(f, km, tonnes, 0, "intensity", vehicle)
            trucks, added = 0, 0.0
            how = f"shared run: allocated by tonne-km at the default load ({r['label']}); no truck added"
        else:
            cap = f.cases_per_vehicle(store, sku, vehicle)
            trucks = trucks_for(cases, cap)
            r = road_kg(f, km, tonnes, trucks, "vehicle", vehicle)
            added = r["kg"]
            how = r["label"]
        net_inr = margin - freight
        out["options"].append({"label": name, "vehicle": vehicle, "trucks": trucks, "lands_end_of_day": lands, "freight_inr": freight,
                               "margin_saved": margin, "net_inr": round(net_inr), "kg_co2e": round(r["kg"]), "kg_added": round(added),
                               "shadow_cost_inr": round(added / 1000.0 * f.shadow_price),
                               "net_after_shadow_inr": round(net_inr - added / 1000.0 * f.shadow_price),
                               "g_per_case": round(1000 * r["kg"] / cases) if cases else None, "method": "intensity (shared)" if q.get("shared") else "vehicle",
                               "factor_label": how, "km": km, "km_source": km_src})
    # doing nothing is an option too: the slipped cases may land before any rush move could
    out["options"].append({"label": "no_rush_move", "vehicle": None, "trucks": 0, "lands_end_of_day": None, "freight_inr": 0,
                           "margin_saved": 0, "net_inr": 0, "kg_co2e": 0, "kg_added": 0, "shadow_cost_inr": 0, "net_after_shadow_inr": 0,
                           "g_per_case": None, "method": None, "factor_label": "nothing sent: the projection as it stands", "km": km,
                           "km_source": km_src})
    opts = out["options"]
    best = max(opts, key=lambda o: (o["net_inr"], -o["kg_added"]))
    cleanest = min(opts, key=lambda o: (o["kg_added"], -o["net_inr"]))
    out["best_on_money"] = best["label"]
    out["least_added_carbon"] = cleanest["label"]
    be = breakeven_price(best, cleanest, "kg_added") if best is not cleanest else None
    out["breakeven_inr_per_t"] = be
    if all(o["margin_saved"] == 0 for o in opts):
        out["note"] = (f"no rush move saves any margin (the projection has no shortage left after the earliest one could land): "
                       f"expediting would add up to {max(o['kg_added'] for o in opts) / 1000:.1f} t CO2e for nothing")
    elif best is cleanest:
        out["note"] = f"{best['label']} is best on the money and adds the least carbon: nothing to trade"
    else:
        out["note"] = (f"{best['label']} earns INR {best['net_inr'] - cleanest['net_inr']:,} more than {cleanest['label']} and adds "
                       f"{(best['kg_added'] - cleanest['kg_added']) / 1000:.1f} t CO2e; "
                       + (f"carbon would have to cost INR {be:,} a tonne to flip it" if be else "no carbon price flips it"))
    return out


# ---------------------------------------------------------------------------------- the approver card
def approver_card(store: Store, profile: Profile, plan: dict, offers: bool = True) -> dict:
    """One page for the people who sign a tower plan: each step's money and carbon, the plan's totals, the
    shadow price shown and not applied, and the offers the tower may make. The card never re-ranks the
    plan: `decision_basis` is the money; `shadow_applied` is always False; when the shadow price would
    change the answer the card says so in `carbon_would_change` and offers the option, it does not pick it."""
    f = load(profile)
    col = plan_column(store, profile, plan)
    week = int(plan.get("week") or 1)
    sku = plan.get("sku")
    rows = []
    for st, cr in zip(plan.get("steps", []), col["steps"]):
        est = st.get("cost_inr") is None and cr.get("cost_inr") is not None
        rows.append({"n": st.get("n"), "code": st.get("code"), "approver": st.get("approver"),
                     "cost_inr": st.get("cost_inr") if not est else cr["cost_inr"], "cost_estimated": est,
                     "margin_saved_inr": st.get("margin_saved_inr"), "kg_co2e": cr["kg_co2e"], "carbon_status": cr["status"],
                     "method": cr.get("method"), "label": cr.get("label", "")})
    money = {"cost_inr": sum(int(r["cost_inr"] or 0) for r in rows),
             "cost_estimated_inr": sum(int(r["cost_inr"] or 0) for r in rows if r["cost_estimated"]),
             "margin_saved_inr": sum(int(r["margin_saved_inr"] or 0) for r in rows)}
    net = money["margin_saved_inr"] - money["cost_inr"]
    card = {"plan": plan_label(plan), "dc": plan.get("dc"), "sku": sku, "week": week, "status": plan.get("status"),
            "impact_inr": plan.get("impact_inr"), "steps": rows,
            "money": {**money, "net_inr": net}, "carbon": {"total_kg": col["total_kg"], "computed_kg": col["computed_kg"],
                                                          "estimated_kg": col["estimated_kg"], "not_estimated_steps": col["not_estimated"]},
            "shadow_price_inr_per_t": f.shadow_price, "shadow_cost_inr": col["shadow_cost_inr"],
            "net_if_carbon_were_priced_inr": net - col["shadow_cost_inr"], "shadow_applied": False, "decision_basis": "money",
            "rule": (f"The money decides. Carbon is shown at the shadow price (INR {f.shadow_price:,.0f} a tonne) and is not "
                     f"added to the decision; an option better on both is offered, an option better on carbon alone shows "
                     f"the carbon price that would flip it."),
            "offers": [], "carbon_would_change": [], "signers": sorted({r["approver"] for r in rows if r.get("approver")}),
            "mode_shift_signer": f.approver(profile, "approve_mode_shift")}
    if not (offers and sku):
        return card
    t = next((s for s in plan.get("steps", []) if s.get("code") == "inter_dc_transfer" and s.get("cases")), None)
    if t:
        tr = {"from_dc": t["from_dc"], "to_dc": t.get("to_dc") or plan["dc"], "sku": sku, "cases": int(t["cases"])}
        rs = right_size(store, profile, week, tr)
        base = rs["options"][0]
        for o in rs["options"][1:]:
            if "error" in o:
                continue
            card["offers"].append(_offer(o, base, "right_size", f.shadow_price))
        cm = compare_modes(store, profile, week, tr)
        rail = cm["options"][1]
        if "error" not in rail and "error" not in cm["options"][0]:
            card["offers"].append(_offer(rail, cm["options"][0], "mode_shift", f.shadow_price))
    e = next((s for s in plan.get("steps", []) if s.get("code") == "expedite_alternate_carrier"), None)
    if e:
        slip = slipped_cases(store, profile, week, plan["dc"], sku)
        if slip:
            ex = expedite_options(store, profile, week, sku, slip["from"], plan["dc"], slip["cases"])
            if ex["options"]:
                card["expedite"] = ex
                asked = next((q for q in ("express_now", "dedicated_now") if any(o["label"] == q for o in ex["options"])), None)
                ref = next((o for o in ex["options"] if o["label"] == asked), ex["options"][0])
                for o in ex["options"]:
                    if o is not ref:
                        card["offers"].append(_offer({**o, "kg_co2e": o["kg_added"]}, {**ref, "kg_co2e": ref["kg_added"]}, "expedite", f.shadow_price))
    card["carbon_would_change"] = [o["label"] for o in card["offers"] if o["kind"] == "carbon_only" and o["flips_at_shadow_price"]]
    return card


def _offer(o: dict, base: dict, family: str, shadow: float) -> dict:
    d_money = o["net_inr"] - base["net_inr"]
    d_kg = o["kg_co2e"] - base["kg_co2e"]
    if d_money >= 0 and d_kg <= 0 and (d_money or d_kg):
        kind = "better_on_both"
    elif d_money < 0 and d_kg < 0:
        kind = "carbon_only"
    elif d_money > 0 and d_kg > 0:
        kind = "money_only"
    else:
        kind = "worse" if (d_money < 0 or d_kg > 0) else "same"
    be = round(-d_money / (-d_kg / 1000.0)) if kind == "carbon_only" else None
    return {"family": family, "label": o["label"], "kind": kind, "d_net_inr": round(d_money), "d_kg_co2e": round(d_kg),
            "breakeven_inr_per_t": be, "flips_at_shadow_price": bool(be is not None and be <= shadow),
            "lane_days": o.get("lane_days", o.get("lands_end_of_day"))}


def render_card(card: dict) -> list[str]:
    m, c = card["money"], card["carbon"]
    est = f" (INR {m['cost_estimated_inr']:,} of it estimated from the carrier quote)" if m.get("cost_estimated_inr") else ""
    lines = [f"APPROVER CARD  {card['plan']}  (week {card['week']}; the tower has INR {card['impact_inr'] or 0:,} at stake)",
             f"  money   margin saved INR {m['margin_saved_inr']:,} for INR {m['cost_inr']:,} of freight{est}: net INR {m['net_inr']:,}",
             f"  carbon  {c['total_kg']:,} kg CO2e (computed {c['computed_kg']:,}, estimated {c['estimated_kg']:,})"
             + (f"; steps not estimated: {c['not_estimated_steps']}" if c["not_estimated_steps"] else ""),
             f"  shadow  INR {card['shadow_price_inr_per_t']:,.0f}/t x {c['total_kg'] / 1000:.1f} t = INR {card['shadow_cost_inr']:,} "
             f"SHOWN, NOT APPLIED (net if it were: INR {card['net_if_carbon_were_priced_inr']:,})",
             "  steps"]
    for r in card["steps"]:
        if r.get("cost_inr") is None:
            money = "-"
        else:
            money = f"INR {r['cost_inr']:,} cost" + (" (est.)" if r["cost_estimated"] else f", {r['margin_saved_inr'] or 0:,} saved")
        kg = "-" if r["kg_co2e"] is None else f"{r['kg_co2e']:,} kg"
        lines.append(f"    {r['n']}. {r['code']:27} {money:34} {kg:>10}  [{r['carbon_status']}] signs: {r['approver']}")
    if card["offers"]:
        lines.append("  offers (the tower offers; the signer decides)")
        for o in card["offers"]:
            be = f"; flips at INR {o['breakeven_inr_per_t']:,}/t" if o["breakeven_inr_per_t"] else ""
            lines.append(f"    {o['family']:10} {o['label']:34} {o['d_net_inr']:+,} INR  {o['d_kg_co2e']:+,} kg  {o['kind']}{be}")
    if card.get("expedite"):
        lines.append(f"  expedite: {card['expedite']['note']}")
    if card.get("carbon_would_change"):
        lines.append(f"  at the shadow price carbon would change the answer for: {', '.join(card['carbon_would_change'])} (offered, not applied)")
    lines.append(f"  decision basis: {card['decision_basis']}; shadow applied: {card['shadow_applied']}; a mode shift is signed by {card['mode_shift_signer']}")
    return lines


def carbon_column(store: Store, profile: Profile, plan, sku: str | None = None) -> list[dict]:
    """Kept for the day-one callers: the plan's steps, each with a `carbon` dict. Every step is returned
    (day-one defect 1 skipped the transfer step, whose SKU is on the plan). Prefer column.plan_column."""
    steps = plan.get("steps", []) if isinstance(plan, dict) else plan
    col = plan_column(store, profile, plan if isinstance(plan, dict) else list(steps), sku=sku)
    out = []
    for st, cr in zip(steps, col["steps"]):
        st = dict(st)
        st["carbon"] = {k: cr.get(k) for k in ("kg_co2e", "g_per_case", "shadow_cost_inr", "trucks", "load_factor", "method", "status")}
        out.append(st)
    return out
