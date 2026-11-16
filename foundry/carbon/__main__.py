"""The Green Ledger's command line. Every output is deterministic (no ids, no clocks), so the book can run it.

    python3 -m foundry.carbon lanes                     every lane: km, rupees, kg per full truck, rail where quoted
    python3 -m foundry.carbon vehicles                  road classes: payload, kg per truck-km (and the day-one number)
    python3 -m foundry.carbon hubs                      each DC as an ISO 14083 hub, WTW (and the day-one TTW-only number)
    python3 -m foundry.carbon transfer --from KOL --to CHN --sku MN-1L --cases 3720 [--mode road|rail|air] [--method ...] [--vehicle ...]
    python3 -m foundry.carbon methods --from KOL --to CHN --sku MN-1L --cases 3720
    python3 -m foundry.carbon compare --week 1 --from KOL --to CHN --sku MN-1L --cases 3720
    python3 -m foundry.carbon expedite --week 1 --from NSK --to CHN --sku MN-1L --cases 2000
    python3 -m foundry.carbon card --week 1             the approver card for each plan the Monday run makes
    python3 -m foundry.carbon column --week 12          the console's carbon column for every plan of the storyline
    python3 -m foundry.carbon inventory [--with-supplier-figures]
    python3 -m foundry.carbon spend                     cat 1 by supplier: activity-based against spend-based
    python3 -m foundry.carbon supplier docs | score <doc_id> | review <supplier> [--gullible|--take-lowest] | sweep
    python3 -m foundry.carbon sources
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def _store(week: int, kind: str | None = None):
    from foundry.data.generate import canonical_store
    from foundry.store import Store
    kind = kind or ("position" if week == 1 else "canonical")
    return Store.from_position() if kind == "position" else canonical_store()


def _scratch() -> Path:
    d = Path(tempfile.mkdtemp(prefix="foundry-carbon-"))
    os.environ.setdefault("FOUNDRY_RUNS", str(d / "runs.jsonl"))
    return d


def _out(lines: list[str]) -> None:
    print("\n".join(lines))


def cmd_lanes(a, p):
    from foundry.carbon.logistics import lane_table
    rows = lane_table(_store(1), p)
    if a.json:
        return print(json.dumps(rows, indent=1))
    out = [f"{'lane':9} {'kind':11} {'km':>6} {'days':>4} {'INR/case':>8} {'kg/full truck':>13} {'kg/case':>7} {'shadow/truck':>12} {'of freight':>10}  rail (km, days, INR/case, kg, saving)"]
    for r in rows:
        rail = (f"{r['rail']['km']:,} km, {r['rail']['days']} d, {r['rail']['cost_per_case']}, {r['rail']['kg_per_full_truck_equiv']:,} kg, -{r['rail']['saving_pct']:.0%}"
                if r["rail"] else "-")
        out.append(f"{r['from_dc'] + '-' + r['to_dc']:9} {r['kind']:11} {r['km']:6,.0f} {r['days']:4} {r['cost_per_case']:8} {r['kg_per_full_truck']:13,} "
                   f"{r['kg_per_case_ref']:7} {r['shadow_inr_per_truck']:12,} {r['shadow_share_of_freight']:10.1%}  {rail}")
    out.append(f"full truck = 1,100 cases of {rows[0]['reference_sku']}; road factor [{rows[0]['source']}], distances [{rows[0]['km_source_id']}] "
               f"(approximate, illustrative); shadow price shown, not applied")
    _out(out)


def cmd_vehicles(a, p):
    from foundry.carbon.logistics import vehicle_table
    rows = vehicle_table(_store(1), p)
    if a.json:
        return print(json.dumps(rows, indent=1))
    out = [f"{'vehicle class':24} {'payload t':>9} {'WTW kg/tkm':>10} {'load':>5} {'kg/truck-km':>11} {'day-one (15 t)':>14} {'cases/truck':>11} {'g/case/100 km':>13}"]
    for r in rows:
        out.append(f"{r['vehicle']:24} {r['payload_t']:9g} {r['wtw_kg_per_tkm']:10} {r['load']:5.1%} {r['kg_per_vkm']:11.4f} {r['kg_per_vkm_one_payload']:14.4f} "
                   f"{r['cases_per_vehicle']:11,} {r['g_per_case_100km']:13,.1f}")
    out.append(f"factors [{rows[0]['source']}] SFC India v1.0; payloads [ILL] illustrative (profile payload_t_by_vehicle); cases per truck for the reference SKU")
    _out(out)


def cmd_hubs(a, p):
    from foundry.carbon.logistics import hub_table
    rows = hub_table(_store(1), p)
    if a.json:
        return print(json.dumps(rows, indent=1))
    out = [f"{'DC':4} {'kWh':>9} {'diesel l':>8} {'t handled':>9} {'grid kg':>9} {'diesel TTW':>10} {'diesel WTT':>10} {'kg WTW':>9} {'kg/t WTW':>8} {'day-one TTW-only':>16}"]
    for r in rows:
        out.append(f"{r['dc']:4} {r['kwh']:9,} {r['diesel_l']:8,} {r['tonnes_per_year']:9,} {r['electricity_kg']:9,} {r['diesel_ttw_kg']:10,} {r['diesel_wtt_kg']:10,} "
                   f"{r['kg_per_year']:9,} {r['kg_per_tonne']:8} {r['kg_per_tonne_ttw_only']:16}")
    out.append(f"grid [{rows[0]['grid_source']}]: {rows[0]['grid_label']}")
    out.append(f"diesel [{rows[0]['diesel_source']}]; site energy [GFB] illustrative; hub boundary WTW (ISO 14083); the grid factor is generation CO2 only")
    _out(out)


def _transfer(a):
    return {"from_dc": getattr(a, "from"), "to_dc": a.to, "sku": a.sku, "cases": a.cases}


def cmd_transfer(a, p):
    from foundry.carbon.logistics import transfer_footprint
    fp = transfer_footprint(_store(1), p, _transfer(a), mode=a.mode, method=a.method, vehicle=a.vehicle)
    if a.json or "error" in fp:
        return print(json.dumps(fp, indent=1))
    out = [f"{fp['cases']:,} cases of {fp['sku']} {fp['from_dc']}->{fp['to_dc']} by {fp['mode']}: {fp['tonnes']:,} t over {fp['km']:,.0f} km = {fp['tkm']:,} tkm",
           f"  {fp['kg_co2e']:,} kg CO2e WTW = {fp['ttw_kg']:,} TTW + {fp['wtt_kg']:,} WTT   ({fp['g_per_case']:,} g a case, {fp['factor_kg_per_tkm']} kg/tkm)",
           f"  method {fp['method']}: {fp['factor_label']}  [{fp.get('source')}]"]
    if fp["mode"] == "rail":
        out.append(f"  rail {fp['rail_kg']:,} kg + drayage {fp['drayage_kg']:,} kg ({fp['trucks']} trucks of {fp['cases_per_vehicle']:,} cases at each end)")
    elif fp.get("trucks"):
        out.append(f"  {fp['trucks']} trucks of {fp['cases_per_vehicle']:,} cases ({fp.get('vehicle')}), load {fp['load_factor']:.1%}; the default intensity would say {fp['kg_default_intensity']:,} kg")
    out.append(f"  shadow price INR {fp['shadow_price_inr_per_t']:,.0f}/t: INR {fp['shadow_cost_inr']:,} (shown, not applied)")
    out += [f"  note: {n}" for n in fp["notes"]]
    _out(out)


def cmd_methods(a, p):
    from foundry.carbon.logistics import methods_table
    rows = methods_table(_store(1), p, _transfer(a), vehicle=a.vehicle)
    if a.json:
        return print(json.dumps(rows, indent=1))
    out = [f"{a.cases:,} cases of {a.sku} {getattr(a, 'from')}->{a.to}, three ways"]
    for r in rows:
        out.append(f"  {r['method']:9} {r['kg_co2e']:6,} kg = {r['ttw_kg']:6,} TTW + {r['wtt_kg']:5,} WTT  {r['g_per_case']:5,} g/case  {r['factor_label']}")
    _out(out)


def _options_table(rows: list[dict]) -> list[str]:
    out = [f"  {'option':34} {'days':>4} {'trucks':>6} {'freight':>8} {'holding':>7} {'margin saved':>12} {'net INR':>8} {'kg CO2e':>7} {'shadow':>6} {'net after shadow':>16}"]
    for o in rows:
        if "error" in o:
            out.append(f"  {o['label']:34} {o['error']}")
            continue
        out.append(f"  {o['label']:34} {o['lane_days']:4} {o['trucks']:6} {o['lane_cost']:8,} {o['holding_cost']:7,} {o['margin_saved']:12,} {o['net_inr']:8,} "
                   f"{o['kg_co2e']:7,} {o['shadow_cost_inr']:6,} {o['net_after_shadow_inr']:16,}")
    return out


def cmd_compare(a, p):
    from foundry.carbon.decisions import compare_modes, right_size
    s = _store(a.week, a.store)
    t = _transfer(a)
    cm, rs = compare_modes(s, p, a.week, t), right_size(s, p, a.week, t)
    if a.json:
        return print(json.dumps({"modes": cm, "right_size": rs}, indent=1))
    out = [f"MODES  {a.cases:,} cases of {a.sku} {t['from_dc']}->{t['to_dc']}, week {a.week} (shadow INR {cm['shadow_price_inr_per_t']:,.0f}/t, shown, not applied)"]
    out += _options_table(cm["options"])
    out.append(f"  -> {cm['note']}")
    out.append(f"RIGHT-SIZE  trucks of {rs['truck_cases']:,} cases")
    out += _options_table(rs["options"])
    out.append(f"  -> {rs['note']}")
    _out(out)


def cmd_expedite(a, p):
    from foundry.carbon.decisions import expedite_options
    s = _store(a.week, a.store)
    ex = expedite_options(s, p, a.week, a.sku, getattr(a, "from"), a.to, a.cases)
    if a.json or not ex["options"]:
        return print(json.dumps(ex, indent=1))
    out = [f"EXPEDITE  {a.cases:,} cases of {a.sku} {ex['from']}->{ex['to_dc']}, week {a.week} (quotes [{ex['source']}] illustrative; shadow on kg added, shown, not applied)",
           f"  {'option':18} {'vehicle':18} {'trucks':>6} {'lands day':>9} {'freight':>8} {'margin saved':>12} {'net INR':>8} {'kg reported':>11} {'kg added':>8} {'net after shadow':>16}"]
    for o in ex["options"]:
        lands = "-" if o["lands_end_of_day"] is None else o["lands_end_of_day"]
        out.append(f"  {o['label']:18} {o['vehicle'] or '-':18} {o['trucks']:6} {lands:>9} {o['freight_inr']:8,} {o['margin_saved']:12,} {o['net_inr']:8,} "
                   f"{o['kg_co2e']:11,} {o['kg_added']:8,} {o['net_after_shadow_inr']:16,}")
    out.append(f"  -> best on the money: {ex['best_on_money']}; least carbon added: {ex['least_added_carbon']}")
    out.append(f"  -> {ex['note']}")
    _out(out)


def _monday(week: int, p):
    from foundry.orchestrator import TowerRuntime
    rt = TowerRuntime(_store(week), p, folder=_scratch() / "tower")
    rt.monday(week)
    return rt


def cmd_card(a, p):
    from foundry.carbon.decisions import approver_card, render_card
    rt = _monday(a.week, p)
    cards = [approver_card(rt.store, p, rec) for rec in rt.plans.values() if any(
        s["code"] in ("inter_dc_transfer", "expedite_alternate_carrier") for s in rec["steps"])]
    if a.json:
        return print(json.dumps(cards, indent=1, default=str))
    out = []
    for c in cards:
        out += render_card(c) + [""]
    _out(out[:-1] if out else ["no plan with a freight step this Monday"])


def cmd_column(a, p):
    from foundry.carbon.column import render, week_column
    from foundry.orchestrator.scenarios import STORYLINES
    story = a.storyline or {1: "f11_monday", 12: "week12"}.get(a.week)
    if story:
        rt, _ = STORYLINES[story][1](_scratch() / "tower")
    else:
        rt = _monday(a.week, p)
    wc = week_column(rt, a.week)
    if a.json:
        return print(json.dumps(wc, indent=1, default=str))
    out = [f"CARBON COLUMN  week {a.week}" + (f", storyline {story}" if story else ", Monday run") +
           f": {len(wc['plans'])} plans; {wc['applied_kg']:,} kg CO2e on applied plans, {wc['waiting_kg']:,} kg waiting for a signature, "
           f"{wc['never_sent_kg']:,} kg on plans superseded or rejected (never sent, never added)"]
    out += render(wc["plans"])
    _out(out)


def cmd_inventory(a, p):
    from foundry.carbon import docs as D, quality as Q
    from foundry.carbon.inventory import inventory, render
    s = _store(1)
    approved = {}
    if a.with_supplier_figures:
        from foundry.carbon.tools import best_score, supplier_rows
        for r in supplier_rows(s, p):
            b = best_score(p, r["supplier"])
            if b["outcome"] in ("accept", "accept_with_caveat"):
                approved[b["material"]] = {"kg_per_kg": b["value_kg_per_kg"], "doc_id": b["doc_id"],
                                           "label": f"supplier figure, {b['outcome']} (as if the data lead approved it)"}
    inv = inventory(s, p, approved)
    if a.json:
        return print(json.dumps(inv, indent=1))
    out = render(inv)
    if approved:
        base = inventory(s, p)
        out.insert(0, f"WITH {len(approved)} SUPPLIER FIGURES (as if the data lead approved the agent's proposals): "
                      f"{inv['total_t']:,.0f} t against {base['total_t']:,.0f} t at the averages ({inv['total_t'] - base['total_t']:+,.1f} t)")
    _out(out)


def cmd_spend(a, p):
    from foundry.carbon.inventory import spend_check
    rows = spend_check(_store(1), p)
    if a.json:
        return print(json.dumps(rows, indent=1))
    out = [f"{'supplier':20} {'materials':34} {'spend INR':>12} {'t activity':>10} {'t spend':>8} {'spend/activity':>14}"]
    for r in rows:
        out.append(f"{r['supplier']:20} {', '.join(r['materials'])[:34]:34} {r['spend_inr']:12,} {r['t_activity']:10,.1f} {r['t_spend']:8,.1f} {r['ratio_spend_to_activity']:14}")
    tot_a, tot_s = sum(r["t_activity"] for r in rows), sum(r["t_spend"] for r in rows)
    out.append(f"{'total':20} {'':34} {sum(r['spend_inr'] for r in rows):12,} {tot_a:10,.1f} {tot_s:8,.1f} {tot_s / tot_a:14.2f}")
    out.append("both columns are ILLUSTRATIVE: averages and EEIO factors are placeholders in the profile; spend-based is a screen, not a figure to report")
    _out(out)


def cmd_supplier(a, p):
    from foundry.carbon import docs as D, quality as Q
    if a.action == "docs":
        rows = [Q.score(D.read(d["doc_id"]), p) for d in D.index()]
        if a.json:
            return print(json.dumps(rows, indent=1))
        out = [f"{'document':24} {'supplier':19} {'material':15} {'kg/kg':>6} {'x avg':>5} {'DQR':>4} {'primary':>7} {'valid until':>11} {'rung':4} outcome"]
        for s in rows:
            out.append(f"{s['doc_id']:24} {s['supplier']:19} {s['material']:15} {s['value_kg_per_kg'] if s['value_kg_per_kg'] is not None else '-':>6} "
                       f"{s['ratio_to_average'] if s['ratio_to_average'] is not None else '-':>5} {s['dqr'] if s['dqr'] is not None else '-':>4} "
                       f"{format(s['primary_share'], '.0%') if s['primary_share'] is not None else '-':>7} {s['valid_until'] or '-':>11} L{s['ladder']:<3} {s['outcome']}")
        out.append(f"checked as of {rows[0]['as_of']}; DQR scale 1 best to 5 worst (GFB's rule); documents are made up for GFB's made-up suppliers")
        return _out(out)
    if a.action == "score":
        s = Q.score(D.read(a.target), p)
        if a.json:
            return print(json.dumps(s, indent=1))
        out = [f"{s['doc_id']} ({s['supplier']}, {s['material']}): {s['outcome']}, {s['ladder_label']}",
               f"  value {s['value_kg_per_kg']} kg CO2e/kg against the average {s['average_kg_per_kg']} ({s['ratio_to_average']}x); "
               f"valid until {s['valid_until'] or '- (cannot be dated)'}"]
        out += [f"  {c['id']:4} {c['name']:30} {c['status']:5} {c['why']}" for c in s["checks"]]
        out += [f"  ask: {x}" for x in s["asks"]]
        out += [f"  flagged line: {x}" for x in s["flags"]]
        return _out(out)
    from foundry.approval import ProposalStore
    from foundry.carbon import agent
    from foundry.carbon.fake import FakeSupplierClient
    from foundry.levers import Levers
    scratch = _scratch()
    levers = Levers(scratch / "panel.json", scratch / "audit.jsonl")
    if a.disable:
        levers.set(disabled_tools=a.disable.split(","))
    props = ProposalStore(scratch / "proposals.jsonl")
    task = {"mode": "sweep"} if a.action == "sweep" else {"mode": "review", "supplier": a.target}
    r = agent.run(task, _store(1), p, FakeSupplierClient(gullible=a.gullible, take_lowest=a.take_lowest), levers, props)
    if a.json:
        return print(json.dumps({"stop_reason": r.stop_reason, "review": r.review, "trace": r.tool_calls}, indent=1, default=str))
    brain = "gullible" if a.gullible else ("take-lowest" if a.take_lowest else "honest")
    out = [f"SUPPLIER AGENT  {task}  (scripted brain: {brain}; propose-only; scratch proposal store)",
           f"  stop: {r.stop_reason} after {r.turns} tool calls"]
    for t in r.tool_calls:
        args = {k: v for k, v in (t.get("input") or {}).items() if k != "rationale"}
        shown = json.dumps(args)[:70] if t.get("allowed") else "(refused before it ran)"
        state = "allowed" if t.get("allowed") else f"REFUSED by the levers: {t.get('reason')}"
        out.append(f"   {t['turn']:>2}. {t['tool']:22} {shown:70} {state}")
    rv = r.review or {}
    for x in rv.get("reviewed", []):
        out.append(f"  reviewed {x['supplier']:19} {x.get('doc_id') or '-':24} {x['outcome']:19} {x.get('value_kg_per_kg') if x.get('value_kg_per_kg') is not None else '-'}")
    for u in rv.get("factor_updates", []):
        out.append(f"  factor update {'PROPOSED to the data lead' if u.get('proposal_id') else 'CLAIMED, never written'}: {u['material']} -> {u['new_kg_per_kg']} ({u['doc_id']})")
    for q in rv.get("requests", []):
        out.append(f"  ask {q['supplier']}: {'; '.join(q['asks'])}")
    for x in rv.get("refused", []):
        out.append(f"  refused: {x}")
    for fl in rv.get("flags", []):
        out.append(f"  flagged {fl['doc_id']}: {len(fl['lines'])} line(s) addressed to an AI system, not followed")
    out.append(f"  summary: {rv.get('summary', '')}")
    rows = props.all_latest()
    out.append(f"  proposals written: {len(rows)} " + ", ".join(f"{x['kind']} -> {', '.join(sorted({y['approver'] for y in x['actions']}))}" for x in rows))
    _out(out)


def cmd_sources(a, p):
    from foundry.carbon.sources import CHECKED, table
    rows = table()
    if a.json:
        return print(json.dumps(rows, indent=1))
    out = [f"Sources (re-checked {CHECKED}); ILL = illustrative GFB number, GFB = GFB's own activity data"]
    for r in rows:
        out.append(f"  {r['id']:4} {r['date']:17} {'verified' if r['verified'] else 'NOT VERIFIED':12} {r['short']}")
    _out(out)


def main(argv=None) -> int:
    from foundry.profile import load_profile
    ap = argparse.ArgumentParser(prog="python3 -m foundry.carbon", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default="gfb")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, move=False, week=False):
        sp = sub.add_parser(name)
        sp.add_argument("--json", action="store_true")
        if move:
            sp.add_argument("--from", required=True)
            sp.add_argument("--to", required=True)
            sp.add_argument("--sku", required=True)
            sp.add_argument("--cases", type=int, required=True)
        if week:
            sp.add_argument("--week", type=int, default=1)
            sp.add_argument("--store", choices=["position", "canonical"], default=None)
        sp.set_defaults(fn=fn)
        return sp

    add("lanes", cmd_lanes)
    add("vehicles", cmd_vehicles)
    add("hubs", cmd_hubs)
    t = add("transfer", cmd_transfer, move=True)
    t.add_argument("--mode", choices=["road", "rail", "air"], default=None)
    t.add_argument("--method", choices=["vehicle", "intensity", "fuel"], default=None)
    t.add_argument("--vehicle", default=None)
    m = add("methods", cmd_methods, move=True)
    m.add_argument("--vehicle", default=None)
    add("compare", cmd_compare, move=True, week=True)
    add("expedite", cmd_expedite, move=True, week=True)
    c = add("card", cmd_card)
    c.add_argument("--week", type=int, default=1)
    c = add("column", cmd_column)
    c.add_argument("--week", type=int, default=12)
    c.add_argument("--storyline", default=None, choices=["f11_monday", "week12", "one_sender"])
    i = add("inventory", cmd_inventory)
    i.add_argument("--with-supplier-figures", action="store_true")
    add("spend", cmd_spend)
    s = add("supplier", cmd_supplier)
    s.add_argument("action", choices=["docs", "score", "review", "sweep"])
    s.add_argument("target", nargs="?", default=None, help="a doc id (score) or a supplier id (review)")
    s.add_argument("--gullible", action="store_true")
    s.add_argument("--take-lowest", action="store_true")
    s.add_argument("--disable", default=None, help="comma-separated tools to switch off on the panel")
    add("sources", cmd_sources)
    a = ap.parse_args(argv)
    if a.cmd == "supplier" and a.action in ("score", "review") and not a.target:
        ap.error(f"supplier {a.action} needs a target")
    a.fn(a, load_profile(a.profile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
