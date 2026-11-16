"""Logistics emissions in the shape of ISO 14083: activity (tonne-km) times an intensity, well to wheel.

Three ways to count one road move, from the coarsest to the most specific:
  intensity   tonne-km x the default kg CO2e per tonne-km (the default already assumes a load and
              an empty-running share; it cannot see your trucks)            -> reporting
  vehicle     trucks x km x kg per loaded truck-km (the same default, turned back into a truck of
              that class: its own payload) -> decisions: it sees the half-empty fourth truck
  fuel        litres (trucks x km / km per litre, grossed up for empty running) x kg per litre
              -> when the carrier sends fuel data, the best of the three
Well to wheel (WTW) = tank to wheel (TTW, burnt in the engine) + well to tank (WTT, producing the fuel).

Hubs (a DC's energy) are the other half of an ISO 14083 transport chain: kg CO2e per tonne handled.
Fixed 8 Nov 2026 (day-one defect 3): hub_table counted site diesel tank-to-wheel only; ISO 14083 hub
emissions are well to wheel, so the diesel's well-to-tank part is now in (electricity stays at the
grid factor, which is generation CO2 only: said in the label).
"""
from __future__ import annotations

from foundry.carbon.factors import Factors, load, trucks_for
from foundry.profile import Profile
from foundry.store import Store


def road_kg(f: Factors, km: float, tonnes: float, trucks: int, method: str, vehicle: str | None = None) -> dict:
    """kg CO2e for a road leg by one of the three methods, with the TTW / WTT split. `vehicle` is the
    class every method uses (its intensity, its payload, its fuel economy)."""
    r = f.road(vehicle)
    tkm = tonnes * km
    if method == "intensity":
        wtw = tkm * r["wtw"]
        ttw = tkm * r["ttw"]
        label = r.get("label", "")
    elif method == "fuel":
        kmpl, kmpl_src = f.km_per_l(r["vehicle"])
        d = f.diesel()
        # empty = the share of all km driven empty, so each loaded km carries e / (1 - e) empty km
        litres = trucks * km / kmpl / (1 - r["empty"])
        ttw, wtw = litres * d["ttw"], litres * d["wtw"]
        label = (f"fuel: {litres:,.0f} l at {kmpl} km/l ({kmpl_src}), {r['empty']:.0%} empty running; "
                 f"{d.get('label', '')}")
    else:  # vehicle
        per_vkm = f.kg_per_vehicle_km(r["vehicle"])
        wtw = trucks * km * per_vkm
        ttw = wtw * r["ttw"] / r["wtw"]
        label = (f"vehicle: {trucks} x {r['vehicle']} ({r['payload_t']:g} t) x {km:,.0f} km x {per_vkm:.4f} kg per truck-km "
                 f"({r.get('label', '')})")
    return {"kg": wtw, "ttw_kg": ttw, "wtt_kg": wtw - ttw, "tkm": tkm, "label": label, "vehicle": r["vehicle"],
            "source": r.get("source")}


def transfer_footprint(store: Store, profile: Profile, transfer: dict, mode: str | None = None,
                       method: str | None = None, vehicle: str | None = None) -> dict:
    """One move's footprint. transfer = {from_dc, to_dc, sku, cases, lane_days} (the rebalancing
    agent's Transfer, or a tower plan step with the plan's sku). mode road | rail | air; method vehicle |
    intensity | fuel (road only; default the profile's decision_method); vehicle a class in the
    profile's road factors (default the profile's road.vehicle)."""
    f = load(profile)
    a, b, sku = transfer["from_dc"], transfer["to_dc"], transfer.get("sku")
    if not sku:
        raise ValueError("a footprint needs the SKU: a tower plan step carries it on the plan, not the step")
    cases = float(transfer.get("cases") or 0)
    mode = mode or transfer.get("mode") or "road"
    vehicle = vehicle or transfer.get("vehicle") or f.default_vehicle
    kgc, kgc_src = f.kg_per_case(store, sku)
    tonnes = cases * kgc / 1000.0
    notes = list(f.notes)
    if kgc_src.startswith("estimated"):
        notes.append(f"{sku}: case weight {kgc_src}")
    out = {"from_dc": a, "to_dc": b, "sku": sku, "cases": int(round(cases)), "mode": mode,
           "lane_days": transfer.get("lane_days"), "kg_per_case": kgc, "tonnes": round(tonnes, 3),
           "shadow_price_inr_per_t": f.shadow_price, "defaults_used": f.defaults_used}
    cap = f.cases_per_vehicle(store, sku, vehicle if mode == "road" else None)
    trucks = trucks_for(cases, cap)
    if mode == "rail":
        lane = f.rail_lane(a, b)
        if not lane:
            return {**out, "error": f"no rail option {a}->{b} in the profile", "notes": notes}
        rail = f.rail()
        km = float(lane["km"])
        rail_kg = tonnes * km * rail["wtw"]
        rail_ttw = tonnes * km * rail["ttw"]
        dray = road_kg(f, rail["drayage_km"], tonnes, trucks, "vehicle")
        kg = rail_kg + 2 * dray["kg"]
        ttw = rail_ttw + 2 * dray["ttw_kg"]
        out.update({"km": km, "km_source": "profile rail lanes (rail km)", "tkm": round(tonnes * km), "trucks": trucks,
                    "vehicle": f.default_vehicle, "cases_per_vehicle": cap,
                    "load_factor": round(cases / (trucks * cap), 3) if trucks else 0.0,
                    "method": "intensity (rail) + vehicle (drayage)", "rail_kg": round(rail_kg), "drayage_kg": round(2 * dray["kg"]),
                    "lane_days": lane.get("days", out["lane_days"]), "cost_per_case": lane.get("cost_per_case"),
                    "factor_label": f"{rail['label']}; drayage {rail['drayage_km']:.0f} km each end by road", "source": rail["source"]})
        kg_default = kg
    elif mode == "air":
        km, km_src = f.km(store, a, b)
        air = f.air()
        if km is None:
            return {**out, "error": km_src, "notes": notes}
        kg = tonnes * km * float(air["wtw"])
        ttw = kg * 0.8
        kg_default = kg
        notes.append("air factor is illustrative: never quote it")
        out.update({"km": km, "km_source": km_src, "tkm": round(tonnes * km), "trucks": 0, "load_factor": None,
                    "method": "intensity (air)", "factor_label": air.get("label", ""), "source": f.source("air")})
    else:
        km, km_src = f.km(store, a, b)
        if km is None:
            return {**out, "error": km_src, "notes": notes}
        if km_src.startswith("estimated"):
            notes.append(f"{a}->{b}: distance {km_src}")
        method = method or f.decision_method
        r = road_kg(f, km, tonnes, trucks, method, vehicle)
        kg, ttw = r["kg"], r["ttw_kg"]
        kg_default = road_kg(f, km, tonnes, trucks, "intensity", vehicle)["kg"]
        out.update({"km": km, "km_source": km_src, "tkm": round(r["tkm"]), "trucks": trucks, "vehicle": vehicle,
                    "cases_per_vehicle": cap, "load_factor": round(cases / (trucks * cap), 3) if trucks else 0.0,
                    "method": method, "factor_label": r["label"], "source": r["source"]})
    tkm = tonnes * out["km"]
    out.update({
        "kg_co2e": round(kg), "ttw_kg": round(ttw), "wtt_kg": round(kg - ttw),
        "factor_kg_per_tkm": round(kg / tkm, 4) if tkm else None,
        "kg_default_intensity": round(kg_default),
        "g_per_case": round(1000 * kg / cases) if cases else None,
        "shadow_cost_inr": round(kg / 1000.0 * f.shadow_price),
        "notes": notes,
    })
    return out


def methods_table(store: Store, profile: Profile, transfer: dict, vehicle: str | None = None) -> list[dict]:
    """The same road move counted the three ways, for the book's side-by-side."""
    rows = []
    for m in ("intensity", "vehicle", "fuel"):
        fp = transfer_footprint(store, profile, transfer, mode="road", method=m, vehicle=vehicle)
        rows.append({"method": m, **{k: fp[k] for k in ("kg_co2e", "ttw_kg", "wtt_kg", "trucks", "load_factor", "g_per_case", "factor_label")}})
    return rows


def vehicle_table(store: Store, profile: Profile, sku: str | None = None) -> list[dict]:
    """Every road class in the profile: payload, kg per loaded truck-km, cases of the reference SKU per
    vehicle, kg per case-km. `kg_per_vkm_one_payload` is what day-one code charged (one payload for all)."""
    f = load(profile)
    sku = sku or f.raw.get("reference_sku", next(iter(store.net.parts)))
    p0, _ = f.payload_t(f.default_vehicle)
    rows = []
    for name, fac in ((f.raw.get("road") or {}).get("factors") or {}).items():
        p, src = f.payload_t(name)
        per = f.kg_per_vehicle_km(name)
        cap = f.cases_per_vehicle(store, sku, name)
        rows.append({"vehicle": name, "payload_t": p, "payload_source": src, "wtw_kg_per_tkm": fac["wtw"], "load": fac["load"],
                     "kg_per_vkm": round(per, 4), "kg_per_vkm_one_payload": round(fac["wtw"] * p0 * fac["load"], 4),
                     "cases_per_vehicle": cap, "g_per_case_100km": round(per * 100 * 1000 / cap, 1),
                     "label": fac.get("label", ""), "source": f.source("road")})
    return rows


def lane_table(store: Store, profile: Profile) -> list[dict]:
    """Every lane in the network: distance, days, rupees per case, and what a full truck emits."""
    f = load(profile)
    net = store.net
    ref = f.raw.get("reference_sku", next(iter(net.parts)))
    kgc, _ = f.kg_per_case(store, ref)
    full_t = net.truck_cases * kgc / 1000.0
    per_vkm = f.kg_per_vehicle_km()
    rows = []
    for (a, b), (days, cpc) in net.lanes.items():
        km, src = f.km(store, a, b)
        truck_kg = km * per_vkm if km else None
        row = {"from_dc": a, "to_dc": b, "kind": "plant to DC" if a in net.plants else "DC to DC", "days": days,
               "cost_per_case": cpc, "km": km, "km_source": src,
               "kg_per_tonne": round(km * f.road()["wtw"], 1) if km else None,
               "kg_per_full_truck": round(truck_kg) if truck_kg else None,
               "kg_per_case_ref": round(truck_kg / net.truck_cases, 2) if truck_kg else None,
               "reference_sku": ref, "shadow_inr_per_truck": round(truck_kg / 1000 * f.shadow_price) if truck_kg else None,
               "shadow_share_of_freight": round(truck_kg / 1000 * f.shadow_price / (cpc * net.truck_cases), 3) if truck_kg else None,
               "rail": None, "source": f.source("road"), "km_source_id": f.source("lanes_km")}
        rl = f.rail_lane(a, b)
        if rl:
            rail = f.rail()
            rk = full_t * rl["km"] * rail["wtw"] + 2 * rail["drayage_km"] * per_vkm
            row["rail"] = {"km": rl["km"], "days": rl["days"], "cost_per_case": rl["cost_per_case"],
                           "kg_per_full_truck_equiv": round(rk), "saving_pct": round(1 - rk / truck_kg, 3) if truck_kg else None}
        rows.append(row)
    return rows


def hub_table(store: Store, profile: Profile) -> list[dict]:
    """Each DC's energy as ISO 14083 hub emissions, well to wheel: kg CO2e per tonne handled (attributed,
    average). Site diesel counts TTW + WTT (day-one code counted TTW only: `kg_per_tonne_ttw_only` keeps
    that number so the fix shows); electricity counts at the grid factor (generation CO2 only)."""
    f = load(profile)
    net = store.net
    grid, glabel = f.grid()
    d = f.diesel()
    energy = f.raw.get("energy") or {}
    rows = []
    for dc in net.dcs:
        e = energy.get(dc)
        if not e:
            continue
        tonnes = sum(net.weekly_mean(sku, dc) * 52 * f.kg_per_case(store, sku)[0] / 1000.0 for sku in net.parts)
        kwh, litres = float(e.get("kwh", 0)), float(e.get("diesel_l", 0))
        elec = kwh * grid
        d_ttw, d_wtt = litres * d["ttw"], litres * d["wtt"]
        kg = elec + d_ttw + d_wtt
        old = elec + d_ttw
        rows.append({"dc": dc, "kwh": int(kwh), "diesel_l": int(litres), "tonnes_per_year": round(tonnes),
                     "electricity_kg": round(elec), "diesel_ttw_kg": round(d_ttw), "diesel_wtt_kg": round(d_wtt),
                     "kg_per_year": round(kg), "kg_per_tonne": round(kg / tonnes, 1) if tonnes else None,
                     "kg_per_tonne_ttw_only": round(old / tonnes, 1) if tonnes else None, "boundary": "WTW (ISO 14083 hub)",
                     "grid_label": glabel, "grid_source": f.source("grid"), "diesel_source": d["source"]})
    return rows
