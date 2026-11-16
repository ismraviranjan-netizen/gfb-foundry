"""Lab 8 · The radar as a probability: P(stockout) per DC x SKU instead of RED / AMBER / GREEN.

The radar (foundry.risk.projection) projects one future: demand = consensus / 7 every day and every truck
on its day. This lab projects 5,000 futures with the same arithmetic (PAB(d) = PAB(d-1) - served +
arrivals; a truck that lands on day d serves from day d+1) and two things sampled:
  demand    each week of the horizon draws its ratio actual / consensus from the 632 ordinary weeks of
            Lab 1's pool: the forecast error this network has actually had (P10 0.793, P90 1.213)
  arrivals  every scheduled arrival is split into trucks of 1,100 cases; each truck is late by the
            lateness the shipments table shows over the twelve canonical weeks (0, 1 or 3 days)
Per row: P(RED) = the chance the shortage reaches the radar's own RED line (a quarter of a day's demand),
the expected and P90 margin lost over 14 days, the median first short day, and the radar's class.
Check first: with every ratio 1 and no lateness the simulation must reproduce the radar on every row.
"""
from __future__ import annotations

import math
from collections import Counter

import numpy as np

from foundry.data.generate import canonical_store
from foundry.profile import load_profile
from foundry.risk import projection as P
from foundry.store import Store
from labs.decision.data import history

N = 5000


def demand_pool(upto: int = 16) -> np.ndarray:
    h = history()
    o = h[(h.week <= upto) & (h.uplift == 0)]
    return (o.actual / o.consensus).to_numpy()


def lateness_table() -> tuple[np.ndarray, np.ndarray]:
    c = Counter(r["late_days"] for r in canonical_store().shipments)
    days = np.array(sorted(c), float)
    p = np.array([c[d] for d in sorted(c)], float)
    return days, p / p.sum()


def simulate(row: P.Projection, net, rng, pool, late_days, late_p, n: int = N, persistent: bool = False,
             deterministic: bool = False) -> dict:
    H, daily = row.horizon_days, row.daily_demand
    weeks = math.ceil(H / 7)
    if deterministic:
        r = np.ones((n, weeks))
    elif persistent:
        r = np.repeat(rng.choice(pool, size=(n, 1)), weeks, axis=1)       # one level for the whole horizon
    else:
        r = rng.choice(pool, size=(n, weeks))
    landing = np.zeros((n, H + 1))
    for a in row.arrivals:
        k = max(1, math.ceil(a["cases"] / net.truck_cases))
        for _ in range(k):
            late = np.zeros(n, int) if deterministic else rng.choice(late_days, size=n, p=late_p).astype(int)
            day = a["day"] + late
            ok = day <= H
            np.add.at(landing, (np.where(ok)[0], day[ok]), a["cases"] / k)
    bal = np.full(n, float(row.start_on_hand))
    short_tot = np.zeros(n)
    first = np.full(n, 99)
    for d in range(1, H + 1):
        dem = daily * r[:, (d - 1) // 7]
        served = np.minimum(bal, dem)
        short = dem - served
        first = np.where((short > 1e-9) & (first == 99), d, first)
        short_tot += short
        bal = bal - served + landing[:, d]
    money = short_tot * net.contribution_per_case(row.sku)
    red = short_tot >= 0.25 * daily
    return {"p_red": float(red.mean()), "p_any": float((short_tot > 1e-9).mean()), "cases": float(short_tot.mean()),
            "e_money": float(money.mean()), "p90_money": float(np.quantile(money, 0.9)), "p10_money": float(np.quantile(money, 0.1)),
            "first_med": int(np.median(first[first < 99])) if (first < 99).any() else None, "money": money}


def run_store(store: Store, week: int, label: str, seed: int = 8, late=None) -> dict:
    p = load_profile("gfb")
    pool = demand_pool()
    late_days, late_p = late or lateness_table()
    rng = np.random.default_rng(seed)
    rad = P.radar(store, p, week)
    rows, checks, grey = [], [], []
    for rr in rad["rows"]:
        pr = P.project(store, p, week, rr["dc"], rr["sku"])
        if pr.risk == "GREY":
            grey.append(f"{pr.dc} {pr.sku}")
            continue
        det = simulate(pr, store.net, rng, pool, late_days, late_p, n=1, deterministic=True)
        checks.append(abs(det["cases"] - pr.cases_short))
        mc = simulate(pr, store.net, rng, pool, late_days, late_p)
        per = simulate(pr, store.net, rng, pool, late_days, late_p, persistent=True)
        rows.append({"dc": pr.dc, "sku": pr.sku, "radar": pr.risk, "radar_inr": pr.money_at_risk, "radar_first": pr.first_short_day,
                     "dos0": pr.dos0, **{k: v for k, v in mc.items() if k != "money"}, "p_red_persistent": per["p_red"],
                     "e_money_persistent": per["e_money"], "_money": mc["money"]})
    rows.sort(key=lambda x: -x["e_money"])
    return {"label": label, "week": week, "rows": rows, "grey": grey, "max_check": max(checks), "n_checked": len(checks),
            "radar_counts": rad["counts"], "radar_money": rad["money_at_risk"]}


def run(seed: int = 8) -> dict:
    f11 = run_store(Store.from_position(), 1, "the F.11 position", seed)
    w12 = run_store(canonical_store(), 12, "week 12", seed)
    days, p = lateness_table()
    monsoon_p = np.array([0.80, 0.10, 0.10]) if len(days) == 3 else p
    f11m = run_store(Store.from_position(), 1, "the F.11 position, monsoon lateness", seed, late=(days, monsoon_p))
    pool = demand_pool()
    # AMBER is a colour, not a probability: Chennai after the F.11 transfer (3,720 cases landing end of day 4)
    s, prof = Store.from_position(), load_profile("gfb")
    after = P.project(s, prof, 1, "CHN", "MN-1L", extra_arrivals=[{"day": 4, "cases": 3720, "source": "transfer KOL->CHN"}])
    rng = np.random.default_rng(seed + 1)
    a = simulate(after, s.net, rng, pool, days, p)
    a.pop("money")
    return {"f11": f11, "w12": w12, "monsoon": f11m, "late_days": days.tolist(), "late_p": [round(x, 4) for x in p.tolist()],
            "pool_n": len(pool), "pool_q": [round(float(np.quantile(pool, q)), 3) for q in (0.1, 0.5, 0.9)], "n": N,
            "after": {"radar": after.risk, "radar_inr": after.money_at_risk, "min_balance": after.min_balance, **a},
            "sum_e_f11": round(sum(x["e_money"] for x in f11["rows"])), "sum_e_w12": round(sum(x["e_money"] for x in w12["rows"]))}


def table(res: dict, top: int = 10) -> None:
    print(f"{res['label']} (week {res['week']}): radar {res['radar_counts']}, RED money INR {res['radar_money']:,}; "
          f"deterministic replay matches the radar on {res['n_checked']} rows (max diff {res['max_check']:.1f} cases)")
    if res["grey"]:
        print(f"  GREY, not simulated (refresh first): {', '.join(res['grey'])}")
    print(f"  {'row':11}{'radar':>7}{'radar INR':>11}{'P(RED)':>8}{'P(any)':>8}{'E[INR]':>10}{'P90 INR':>10}{'1st day':>8}{'P(RED) pers.':>13}")
    for x in res["rows"][:top]:
        fd = "-" if x["first_med"] is None else str(x["first_med"])
        print(f"  {x['dc'] + ' ' + x['sku']:11}{x['radar']:>7}{x['radar_inr']:11,}{x['p_red']:8.3f}{x['p_any']:8.3f}"
              f"{x['e_money']:10,.0f}{x['p90_money']:10,.0f}{fd:>8}{x['p_red_persistent']:13.3f}")
    rest = res["rows"][top:]
    if rest:
        print(f"  ... {len(rest)} more rows, largest P(RED) among them {max(x['p_red'] for x in rest):.3f}")


def main() -> None:
    r = run()
    print(f"{r['n']:,} futures per row. Demand: weekly ratio from {r['pool_n']} ordinary weeks (P10/P50/P90 {r['pool_q']}). "
          f"Lateness per truck: days {r['late_days']} with probabilities {r['late_p']}")
    table(r["f11"])
    print(f"  sum of expected margin lost over the 24 rows: INR {r['sum_e_f11']:,} (the radar's RED rows: INR {r['f11']['radar_money']:,})")
    a = r["after"]
    print(f"  CHN MN-1L after the F.11 transfer: radar {a['radar']} (INR {a['radar_inr']:,}, balance dips to {a['min_balance']:,.0f}); "
          f"P(RED) {a['p_red']:.3f}, P(any) {a['p_any']:.3f}, E[INR] {a['e_money']:,.0f}, P90 INR {a['p90_money']:,.0f}")
    table(r["w12"])
    print(f"  sum of expected margin lost over the 23 rows: INR {r['sum_e_w12']:,} (the radar's RED rows: INR {r['w12']['radar_money']:,})")
    m = r["monsoon"]
    print("the F.11 position with monsoon lateness (P late 1 day 0.10, 3 days 0.10):")
    for x in m["rows"][:4]:
        print(f"  {x['dc']} {x['sku']}: P(RED) {x['p_red']:.3f}, E[INR] {x['e_money']:,.0f}, P90 INR {x['p90_money']:,.0f}")
    act = [x for x in r["f11"]["rows"] if x["p_red"] > 0.5]
    a = r["after"]
    print("Decision: plan the rows with P(RED) above 0.5: " +
          ", ".join(f"{x['dc']} {x['sku']} ({x['p_red']:.3f})" for x in act) + ";")
    print(f"  after F.11 Chennai is {a['radar']} on the radar but P(RED) {a['p_red']:.3f}: keep it on the watch list.")


if __name__ == "__main__":
    main()
