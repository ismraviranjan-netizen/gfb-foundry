"""Lab 11 · The Gurgaon dock as a queue: which lever brings dwell back, and is it worth its cost?

Week 12 at Gurgaon (GGN): the dock served 15 trucks, its normal number, but the average truck spent
9.5 h between gate-in and gate-out against 3.0 h in weeks 4-11 (the dock table). 20 of 80 orders went
out late, 2,025 cases, which the triage prices at half a case's contribution: INR 64,354 at stake
(foundry.agents.rca_exception.triage.estimate_impact). Vikram Sethi signs dock changes; the root-cause
agent offers reschedule_dock_slots and extend_shift. This lab puts a number on each.

The data has a week's average per DC, not a gate log, so the dock is a model with these ASSUMPTIONS
(each is a constant below; vary it and watch the answer):
  OPEN, CLOSE   the dock works 08:00-18:00 every day; an unload not finished at 18:00 resumes at 08:00
  CREWS         two inbound crews; a crew unloads one truck at a time
  WINDOW        plant trucks reach the gate between 07:00 and 15:00, any time equally likely; the
                number a day is Poisson with the dock table's weekly mean (weeks 1-11) / 7
  CV            an unload time is gamma with a coefficient of variation of 0.4
  CREW_HOUR     INR 900 per crew-hour for overtime or contract labour (a crew of four and a share of a
                supervisor); vary 600-1,500
  COMPLIANCE    with booked slots, 80% of trucks arrive within half an hour of their slot; vary 50-100%
  the money     the share of cases shipped late moves in a straight line with the average dwell, through
                the two points the data gives: weeks 4-11 (3.0 h, 1.4% late) and week 12 (9.5 h, 21.5%)
Two numbers are fitted, not assumed: the mean unload time that gives the normal 3.0 h, and the one that
gives week 12's 9.5 h. The data cannot say why the dock slowed (a crew short, a forklift down, a yard
dug up); the model only says how slow it must have been, and what each lever does to a dock that slow.
"""
from __future__ import annotations

import math

import numpy as np

from foundry.data.generate import canonical_store
from foundry.profile import load_profile
from foundry.agents.rca_exception.triage import estimate_impact

DC, WEEK = "GGN", 12
OPEN, CLOSE = 8.0, 18.0
CREWS = 2
WINDOW = (7.0, 15.0)
CV = 0.4
CREW_HOUR = 900.0
COMPLIANCE = 0.8
TARGET_H = 6.0                      # the pilot's line: dwell back under twice the baseline
N_DAYS, WARM = 3500, 28
LEVERS = [
    # name, crews' closing hours, booked slots
    ("do nothing", (18.0, 18.0), False),
    ("re-slot arrivals", (18.0, 18.0), True),
    ("one crew to 22:00", (22.0, 18.0), False),
    ("re-slot + one crew 22:00", (22.0, 18.0), True),
    ("both crews to 22:00", (22.0, 22.0), False),
    ("third crew, 08:00-18:00", (18.0, 18.0, 18.0), False),
]


def facts() -> dict:
    """What the dock and orders tables say about Gurgaon: weeks 4-11 against week 12."""
    s, p = canonical_store(), load_profile("gfb")
    dock = {r["week"]: r for r in s.dock if r["dc"] == DC}
    base_w = range(WEEK - 8, WEEK)
    imp = {w: estimate_impact(s, p, w, DC, None) for w in list(base_w) + [WEEK]}
    shipped = {w: sum(r["shipped"] for r in s.rows("orders", dc=DC, week=w)) for w in imp}
    late_base = sum(imp[w]["cases_late"] for w in base_w) / sum(shipped[w] for w in base_w)
    return {"trucks_week": float(np.mean([dock[w]["trucks_served"] for w in range(1, WEEK)])),
            "trucks_w12": dock[WEEK]["trucks_served"], "dwell_base": float(np.mean([dock[w]["avg_dwell_h"] for w in base_w])),
            "dwell_w12": float(dock[WEEK]["avg_dwell_h"]), "late_base": late_base,
            "late_w12": imp[WEEK]["cases_late"] / shipped[WEEK], "shipped_w12": shipped[WEEK],
            "cases_late_w12": imp[WEEK]["cases_late"], "at_stake_w12": imp[WEEK]["impact_inr"],
            "contrib": imp[WEEK]["contribution_per_case"]}


def _finish(t: float, work: float, close: float) -> float:
    """A crew that works OPEN-close every day starts at real time t (hours from day 0, 00:00) on a job
    of `work` hours: when is it done? The job pauses overnight."""
    while True:
        day = math.floor(t / 24.0)
        h = t - 24.0 * day
        if h < OPEN:
            t, h = 24.0 * day + OPEN, OPEN
        elif h >= close:
            t = 24.0 * (day + 1) + OPEN
            continue
        if work <= close - h + 1e-12:
            return t + work
        work -= close - h
        t = 24.0 * (day + 1) + OPEN


def _next_open(t: float, close: float) -> float:
    day = math.floor(t / 24.0)
    h = t - 24.0 * day
    if h < OPEN:
        return 24.0 * day + OPEN
    if h >= close:
        return 24.0 * (day + 1) + OPEN
    return t


def simulate(unload_h: float, closes=(CLOSE, CLOSE), slots: bool = False, trucks_week: float = 15.25,
             n_days: int = N_DAYS, seed: int = 11, compliance: float = COMPLIANCE) -> dict:
    """Trucks in arrival order; each goes to the crew that can start it first. Dwell = gate-out - gate-in."""
    rng = np.random.default_rng(seed)
    n = rng.poisson(trucks_week / 7.0, size=n_days)
    day = np.repeat(np.arange(n_days), n)
    hour = rng.uniform(*WINDOW, size=len(day))
    if slots:
        # the planner books k slots a day, evenly from opening to the last start that ends by 18:00
        last = max(OPEN + 0.5, CLOSE - unload_h)
        pos = np.concatenate([np.arange(k) for k in n])
        cnt = np.repeat(n, n)
        slot = OPEN + (pos + 0.5) * (last - OPEN) / cnt
        keep = rng.random(len(day)) < compliance
        hour = np.where(keep, slot + rng.normal(0.0, 0.5, len(day)), hour)
    work = rng.gamma(1.0 / CV ** 2, unload_h * CV ** 2, size=len(day))
    arr = day * 24.0 + hour
    order = np.argsort(arr, kind="mergesort")
    arr, day, work = arr[order], day[order], work[order]
    free = np.zeros(len(closes))
    out = np.empty(len(arr))
    for i in range(len(arr)):
        starts = [_next_open(max(arr[i], free[k]), closes[k]) for k in range(len(closes))]
        k = min(range(len(closes)), key=lambda j: (starts[j], -closes[j]))
        free[k] = _finish(starts[k], work[i], closes[k])
        out[i] = free[k]
    dwell = out - arr
    keep = day >= WARM
    dw, dd = dwell[keep], day[keep]
    wk = (dd - WARM) // 7
    weekly = np.bincount(wk, weights=dw) / np.maximum(np.bincount(wk), 1)
    weekly = weekly[np.bincount(wk) > 0]
    hours = sum(c - OPEN for c in closes) * (n_days - WARM)
    return {"mean": float(dw.mean()), "p90": float(np.quantile(dw, 0.9)), "over_target": float(np.mean(dw > TARGET_H)),
            "overnight": float(np.mean(dw > 12.0)), "busy": float(work[keep].sum() / hours),
            "p_week_over": float(np.mean(weekly > TARGET_H)), "weekly": weekly, "n": int(keep.sum())}


def fit_unload(target_h: float, trucks_week: float, n_days: int = N_DAYS, lo: float = 0.5, hi: float = 8.0,
               steps: int = 16) -> float:
    """The mean unload time at which the model's average dwell is target_h (bisection, same seed)."""
    for _ in range(steps):
        m = (lo + hi) / 2.0
        if simulate(m, trucks_week=trucks_week, n_days=n_days)["mean"] > target_h:
            hi = m
        else:
            lo = m
    return (lo + hi) / 2.0


def money(dwell_h: float, f: dict) -> float:
    """INR at stake a week at a given average dwell: the straight line through the two data points."""
    slope = (f["late_w12"] - f["late_base"]) / (f["dwell_w12"] - f["dwell_base"])
    late = max(f["late_base"], f["late_base"] + slope * (dwell_h - f["dwell_base"]))
    return late * f["shipped_w12"] * 0.5 * f["contrib"]


def lever_cost(closes, crew_hour: float = CREW_HOUR) -> float:
    """Crew-hours a week beyond the two normal crews' 08:00-18:00, at crew_hour each."""
    extra = sum(c - OPEN for c in closes) - CREWS * (CLOSE - OPEN)
    return extra * 7 * crew_hour


def run(n_days: int = N_DAYS) -> dict:
    f = facts()
    m0 = fit_unload(f["dwell_base"], f["trucks_week"], n_days)
    m12 = fit_unload(f["dwell_w12"], f["trucks_w12"], n_days)
    base = simulate(m0, trucks_week=f["trucks_week"], n_days=n_days)
    rows = []
    stake0 = None
    for name, closes, slots in LEVERS:
        r = simulate(m12, closes, slots, trucks_week=f["trucks_w12"], n_days=n_days)
        stake = money(r["mean"], f)
        stake0 = stake if stake0 is None else stake0
        cost = lever_cost(closes)
        rows.append({"lever": name, **{k: v for k, v in r.items() if k != "weekly"}, "at_stake": stake,
                     "cost": cost, "net": stake0 - stake - cost})
    knee = []
    for m in np.arange(1.5, 6.01, 0.5):
        r = simulate(float(m), trucks_week=f["trucks_week"], n_days=1500)
        knee.append({"unload_h": round(float(m), 1), "busy": r["busy"], "dwell": r["mean"], "overnight": r["overnight"]})
    ok = [x for x in rows if x["mean"] <= TARGET_H]
    pick = max(rows[1:], key=lambda x: x["net"])
    # what a late case must cost before the cheapest lever that meets the 6 h line pays for itself
    meet = min(ok, key=lambda x: x["cost"]) if ok else None
    per_case = 0.5 * f["contrib"]
    breakeven = None
    if meet:
        cases_saved = (stake0 - meet["at_stake"]) / per_case
        breakeven = meet["cost"] / cases_saved
    comp = []
    for c in (0.5, 0.8, 1.0):
        rr = simulate(m12, LEVERS[1][1], True, trucks_week=f["trucks_w12"], n_days=n_days, compliance=c)
        comp.append({"compliance": c, "dwell": rr["mean"]})
    return {"facts": f, "unload_base": m0, "unload_w12": m12, "base": {k: v for k, v in base.items() if k != "weekly"},
            "rows": rows, "knee": knee, "meets_target": [x["lever"] for x in ok], "pick": pick, "meet": meet,
            "per_case": per_case, "breakeven": breakeven, "compliance": comp, "n_days": n_days}


def main() -> None:
    r = run()
    f = r["facts"]
    print(f"Gurgaon dock: weeks 4-11 {f['trucks_week']:.1f} trucks a week, dwell {f['dwell_base']:.1f} h, "
          f"{f['late_base']:.1%} of cases late")
    print(f"  week 12: {f['trucks_w12']} trucks, dwell {f['dwell_w12']:.1f} h, {f['cases_late_w12']:,} of "
          f"{f['shipped_w12']:,} cases late ({f['late_w12']:.1%}), INR {f['at_stake_w12']:,} at stake")
    b = r["base"]
    print(f"  fitted unload: {r['unload_base']:.2f} h a truck in a normal week (model dwell {b['mean']:.2f} h, "
          f"crews busy {b['busy']:.0%})")
    print(f"  fitted unload in week 12: {r['unload_w12']:.2f} h ({r['unload_w12'] / r['unload_base']:.2f}x slower)")
    print("  dwell against unload time (normal trucks, two crews 08:00-18:00):")
    for x in r["knee"]:
        print(f"    unload {x['unload_h']:.1f} h: crews busy {x['busy']:4.0%}, dwell {x['dwell']:5.2f} h, "
              f"overnight {x['overnight']:4.0%}")
    print(f"  levers on the week-12 dock ({r['n_days']:,} simulated days each):")
    print(f"  {'lever':25}{'dwell':>6}{'P90':>6}{'>6 h':>6}{'night':>6}{'P(wk>6)':>8}{'stake':>8}{'cost':>8}{'net':>8}")
    for x in r["rows"]:
        print(f"  {x['lever']:25}{x['mean']:6.2f}{x['p90']:6.1f}{x['over_target']:6.2f}{x['overnight']:6.2f}"
              f"{x['p_week_over']:8.2f}{x['at_stake']:8,.0f}{x['cost']:8,.0f}{x['net']:8,.0f}")
    print("  re-slot alone by slot compliance: " +
          ", ".join(f"{x['compliance']:.0%} -> {x['dwell']:.2f} h" for x in r["compliance"]))
    p = r["pick"]
    print(f"Decision: {p['lever']} now: dwell {p['mean']:.1f} h, INR {p['net']:,.0f} a week better, no crew cost.")
    m = r["meet"]
    if m:
        print(f"  Only '{m['lever']}' gets under {TARGET_H:.0f} h ({m['mean']:.1f} h) and it costs INR "
              f"{m['cost']:,.0f} a week:")
        print(f"  worth it only if a late case costs INR {r['breakeven']:.0f} or more "
              f"(GFB's triage: INR {r['per_case']:.2f}).")


if __name__ == "__main__":
    main()
