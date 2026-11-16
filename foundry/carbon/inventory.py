"""GFB's emissions inventory by scope and category, from the profile's activity data.

Every line carries the activity, the factor, the factor's label and a source id (S1..S25 from the
book's Sources appendix, ILL for an illustrative GFB number, GFB for GFB's own activity data), and the
basis it was computed on (fuel, electricity, average data, supplier-specific, distance-based). The
inventory is a year: annual cases from the network (value / cost per case), split by DC share.

Scope 1   fuel GFB burns at its own sites (the plant's boiler, the DCs' gensets and forklifts): TTW
Scope 2   electricity GFB buys, location-based at the grid factor (generation CO2 only)
Scope 3   cat 1  materials, packaging and the co-packer's service (cradle to gate)
          cat 3  the well-to-tank part of the diesel burnt in scope 1 (fuel- and energy-related)
          cat 4  freight GFB pays for: plant to DC and DC to distributor, WTW intensities
          cat 9  freight someone else pays for after GFB's gate: the distributor's last mile
          cat 12 the packaging after use
          the rest: screened in the profile (scope3_screening), printed, never decided here
Who pays decides the category (4 or 9), not the direction of the truck.
"""
from __future__ import annotations

from foundry.carbon.factors import Factors, load
from foundry.carbon.sources import GFB, ILL, verified
from foundry.profile import Profile
from foundry.store import Store

CATEGORY_NAMES = {1: "purchased goods and services", 2: "capital goods", 3: "fuel- and energy-related activities",
                  4: "upstream transportation and distribution", 5: "waste generated in operations", 6: "business travel",
                  7: "employee commuting", 8: "upstream leased assets", 9: "downstream transportation and distribution",
                  10: "processing of sold products", 11: "use of sold products", 12: "end-of-life treatment of sold products",
                  13: "downstream leased assets", 14: "franchises", 15: "investments"}


def annual_cases(store: Store) -> dict[tuple[str, str], float]:
    """(sku, dc) -> cases a year: the network's annual value / cost per case, split by the DC's share."""
    net = store.net
    return {(sku, dc): net.annual_cases(sku) * net.share(sku, dc) for sku in net.parts for dc in net.dcs if net.share(sku, dc) > 0}


def _line(scope: int, cat: int | None, name: str, activity: float, unit: str, factor: float, funit: str, flabel: str,
          source: str, basis: str, activity_source: str = GFB) -> dict:
    t = activity * factor / 1000.0
    return {"scope": scope, "category": cat, "name": name, "activity": round(activity, 1), "activity_unit": unit,
            "activity_source": activity_source, "factor": factor, "factor_unit": funit, "factor_label": flabel,
            "source": source, "verified": verified(source), "basis": basis, "t_co2e": round(t, 1)}


def material_kg(store: Store, f: Factors, cases: dict | None = None) -> dict[str, float]:
    """kg of each material bought in a year: annual cases x the bill of materials."""
    cases = cases or annual_cases(store)
    by_sku: dict[str, float] = {}
    for (sku, _), c in cases.items():
        by_sku[sku] = by_sku.get(sku, 0.0) + c
    out: dict[str, float] = {}
    for sku, bom in (f.raw.get("bom") or {}).items():
        for m, kg in bom.items():
            out[m] = out.get(m, 0.0) + by_sku.get(sku, 0.0) * float(kg)
    return out


def inventory(store: Store, profile: Profile, approved: dict | None = None) -> dict:
    """The year's inventory. approved = {material: {"kg_per_kg", "doc_id", "label"}}: supplier figures the
    data lead has approved (the supplier agent only proposes them); everything else uses the average."""
    f = load(profile)
    net = store.net
    approved = approved or {}
    lines: list[dict] = []
    gaps: list[str] = []
    d = f.diesel()
    grid = f.grid_info()
    energy = f.raw.get("energy") or {}

    # --- scope 1 and 2: GFB's own sites -------------------------------------------------------------
    for site, e in energy.items():
        kind = "plant" if site in net.plants else "DC"
        if e.get("diesel_l"):
            lines.append(_line(1, None, f"{site} {kind}: diesel burnt on site", float(e["diesel_l"]), "l", d["ttw"], "kg CO2e/l",
                               f"{d.get('label', '')} TTW (a road-diesel factor standing in for stationary diesel: assumption)",
                               d["source"], "activity: fuel"))
        if e.get("kwh"):
            lines.append(_line(2, None, f"{site} {kind}: grid electricity (location-based)", float(e["kwh"]), "kWh",
                               grid["kg_per_kwh"], "kg CO2/kWh", grid["label"], grid["source"], "activity: electricity"))
    gaps.append("refrigerant leaks (chillers, cold rooms): not in the profile; a food company with cold chain must add them to scope 1")
    gaps.append("market-based scope 2 not computed: no contractual instruments (green tariff, RECs) in the profile; "
                "the GHG Protocol's Scope 2 rules are under revision (S20)")

    # --- scope 3 cat 1: what GFB buys --------------------------------------------------------------
    cases = annual_cases(store)
    mats = f.raw.get("materials") or {}
    for m, kg in sorted(material_kg(store, f, cases).items()):
        if m not in mats:
            gaps.append(f"material {m} has no factor in the profile")
            continue
        if m in approved:
            a = approved[m]
            lines.append(_line(3, 1, f"{m} (supplier figure)", kg, "kg", float(a["kg_per_kg"]), "kg CO2e/kg",
                               a.get("label", "supplier-specific, approved by the data lead"), f"supplier:{a.get('doc_id', '?')}",
                               "supplier-specific"))
        else:
            label = "illustrative average cradle-to-gate factor" + (" (the co-packer's processing energy: a purchased service)"
                                                                     if m.startswith("co_pack") else "")
            lines.append(_line(3, 1, m, kg, "kg", float(mats[m]["kg_per_kg"]), "kg CO2e/kg", label, f.source("materials"), "average-data"))

    # --- cat 3: the fuel's own upstream ------------------------------------------------------------
    litres = sum(float(e.get("diesel_l", 0)) for e in energy.values())
    if litres:
        lines.append(_line(3, 3, "well-to-tank of the diesel burnt on site", litres, "l", d["wtt"], "kg CO2e/l",
                           f"{d.get('label', '')} WTT", d["source"], "activity: fuel"))
    gaps.append("cat 3 electricity: upstream of the grid's fuel and T&D losses not included (the CEA factor is generation CO2 only)")

    # --- cat 4 and 9: freight ----------------------------------------------------------------------
    kgc = {sku: f.kg_per_case(store, sku)[0] for sku in net.parts}
    road = f.road()
    by_lane: dict[tuple[str, str], float] = {}
    for (sku, dc), c in cases.items():
        plant = net.parts[sku][2]
        by_lane[(plant, dc)] = by_lane.get((plant, dc), 0.0) + c * kgc[sku] / 1000.0
    for (a, b), tonnes in sorted(by_lane.items()):
        km, src = f.km(store, a, b)
        if km is None:
            gaps.append(f"primary leg {a}->{b}: {src}")
            continue
        lines.append(_line(3, 4, f"primary {a}->{b} ({tonnes:,.0f} t x {km:,.0f} km)", tonnes * km, "tkm", road["wtw"], "kg CO2e/tkm",
                           road["label"], road["source"], "distance-based (intensity)", activity_source=f.source("lanes_km")))
    tonnes_all = sum(by_lane.values())
    dist = f.raw.get("distribution") or {}
    for leg, spec in dist.items():
        r = f.road(spec["vehicle"])
        cat = 4 if spec.get("paid_by", "GFB") == "GFB" else 9
        lines.append(_line(3, cat, f"{leg.replace('_', ' ')}: {spec['km']} km on {spec['vehicle']}, paid by {spec.get('paid_by', 'GFB')}",
                           tonnes_all * float(spec["km"]), "tkm", r["wtw"], "kg CO2e/tkm", r["label"], r["source"],
                           "distance-based (intensity)", activity_source=f.source("distribution")))
    gaps.append("DC-to-DC transfers are not in the annual figure: the tower's carbon column holds them per plan "
                "(add the applied transfers when a transfer history table exists)")

    # --- cat 12: packaging after use ---------------------------------------------------------------
    eol = f.raw.get("end_of_life_kg_per_kg") or {}
    for m, kg in sorted(material_kg(store, f, cases).items()):
        if m in eol:
            lines.append(_line(3, 12, f"{m} after use", kg, "kg", float(eol[m]), "kg CO2e/kg",
                               "illustrative: India's mix of recycling, landfill and open burning", f.source("end_of_life"), "average-data"))

    # --- totals ------------------------------------------------------------------------------------
    by_scope = {s: round(sum(x["t_co2e"] for x in lines if x["scope"] == s), 1) for s in (1, 2, 3)}
    by_cat = {}
    for x in lines:
        if x["scope"] == 3:
            by_cat[x["category"]] = round(by_cat.get(x["category"], 0.0) + x["t_co2e"], 1)
    total = round(sum(by_scope.values()), 1)
    screening = []
    for c in range(1, 16):
        s = (f.raw.get("scope3_screening") or {}).get(c) or (f.raw.get("scope3_screening") or {}).get(str(c)) or {}
        screening.append({"category": c, "name": CATEGORY_NAMES[c], "status": s.get("status", "not screened"), "why": s.get("why", ""),
                          "t_co2e": by_cat.get(c)})
    cat1 = [x for x in lines if x["category"] == 1]
    cat1_t = sum(x["t_co2e"] for x in cat1) or 1.0
    acc: dict[str, float] = {}
    for x in cat1:
        acc[x["basis"]] = acc.get(x["basis"], 0.0) + x["t_co2e"]
    coverage = {k: round(v / cat1_t, 3) for k, v in acc.items()}
    alt = grid.get("alt") or {}
    s2_alt = None
    if alt.get("kg_per_kwh"):
        kwh = sum(float(e.get("kwh", 0)) for e in energy.values())
        s2_alt = {"t_co2e": round(kwh * float(alt["kg_per_kwh"]) / 1000.0, 1), "kg_per_kwh": alt["kg_per_kwh"], "label": alt.get("label", "")}
    return {"company": profile.company, "year": "one year of the network's annual volumes", "lines": lines, "by_scope": by_scope,
            "by_category": dict(sorted(by_cat.items())), "total_t": total,
            "shares": {s: round(v / total, 3) for s, v in by_scope.items()} if total else {},
            "screening": screening, "cat1_coverage": coverage, "gaps": gaps, "scope2_sensitivity": s2_alt,
            "grid_verified": grid["verified"], "tonnes_shipped": round(tonnes_all), "defaults_used": f.defaults_used,
            "unverified_lines": [x["name"] for x in lines if x["source"].startswith("S") and not x["verified"]]}


def spend_check(store: Store, profile: Profile) -> list[dict]:
    """Each supplier's cat 1 tonnes two ways: activity (kg bought x factor) and spend (INR x an EEIO factor).
    The spend factors in the profile are ILLUSTRATIVE placeholders, labelled as such."""
    f = load(profile)
    mats = f.raw.get("materials") or {}
    kg = material_kg(store, f)
    eeio = f.raw.get("eeio_kg_per_inr") or {}
    rows = []
    for sid_, s in (f.raw.get("suppliers") or {}).items():
        own = [m for m, spec in mats.items() if spec.get("supplier") == sid_]
        act = sum(kg.get(m, 0.0) * float(mats[m]["kg_per_kg"]) for m in own) / 1000.0
        spend = float(s.get("spend_inr", 0)) * float(eeio.get(s.get("eeio"), 0.0)) / 1000.0
        rows.append({"supplier": sid_, "name": s.get("name", sid_), "materials": own, "spend_inr": int(s.get("spend_inr", 0)),
                     "eeio_class": s.get("eeio"), "eeio_kg_per_inr": eeio.get(s.get("eeio")), "t_activity": round(act, 1),
                     "t_spend": round(spend, 1), "ratio_spend_to_activity": round(spend / act, 2) if act else None,
                     "spend_source": f.source("eeio"), "activity_source": f.source("materials")})
    rows.sort(key=lambda r: -r["t_activity"])
    return rows


def render(inv: dict) -> list[str]:
    out = [f"{inv['company']}: {inv['total_t']:,.0f} t CO2e a year (scope 1 {inv['by_scope'][1]:,.0f}, scope 2 {inv['by_scope'][2]:,.0f}, "
           f"scope 3 {inv['by_scope'][3]:,.0f})"]
    for s in (1, 2, 3):
        out.append(f"SCOPE {s}: {inv['by_scope'][s]:,.1f} t ({inv['shares'].get(s, 0):.1%})")
        for x in inv["lines"]:
            if x["scope"] != s:
                continue
            cat = f"cat {x['category']:>2} " if x["category"] else ""
            flag = "" if x["verified"] or not x["source"].startswith("S") else " NOT VERIFIED"
            out.append(f"  {cat}{x['name'][:58]:58} {x['activity']:>14,.0f} {x['activity_unit']:4} x {x['factor']:<7g} = {x['t_co2e']:>9,.1f} t  "
                       f"[{x['source']}{flag}] {x['basis']}")
    out.append("SCOPE 3 BY CATEGORY (GFB's screening; the core prints it, never decides it)")
    for c in inv["screening"]:
        t = f"{c['t_co2e']:>9,.1f} t" if c["t_co2e"] is not None else "        - "
        out.append(f"  {c['category']:>2} {c['name'][:40]:40} {c['status']:15} {t}  {c['why'][:70]}")
    out.append("CAT 1 BY DATA BASIS: " + ", ".join(f"{k} {v:.0%}" for k, v in inv["cat1_coverage"].items()))
    if inv.get("scope2_sensitivity"):
        a = inv["scope2_sensitivity"]
        out.append(f"SCOPE 2 SENSITIVITY: at {a['kg_per_kwh']} kg/kWh ({a['label']}) scope 2 would be {a['t_co2e']:,.1f} t")
    out.append("GAPS (said, not hidden):")
    out += [f"  - {g}" for g in inv["gaps"]]
    return out
