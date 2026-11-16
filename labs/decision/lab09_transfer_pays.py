"""Lab 9 · What is the chance the F.11 transfer pays?

F.11 on the radar: Chennai holds 1,480 cases of MN-1L, sells 371.4 a day, and Nashik's truck lands on day 12.
Kolkata can send 3,720 cases on the 4-day lane for INR 81,840 plus INR 3,914 of holding: INR 85,754. On the
radar's one future the move saves INR 1,84,229 and pays 2.15 times. Here the same move meets 20,000 futures:
  demand          weekly ratio from Lab 1's pool (as in Lab 8)
  Nashik's truck  per-truck lateness from the shipments table (as in Lab 8)
  the KOL->CHN lane  a whole-transfer delay from Farhan's lane scorecard (LANE below, fictional: no
                  transfer has run on this lane in the data, so the scorecard is an assumption to vary)
Both decisions (send, do not send) are scored on the same futures: common random numbers, so the
difference is the decision's effect and not luck. Then: the break-even delay; what a relief truck is worth
when the transfer breaks down (the F.11 Monday storyline); and which transfer size has the best expected net.
"""
from __future__ import annotations

import math

import numpy as np

from foundry.profile import load_profile
from foundry.risk import projection as P
from foundry.store import Store
from labs.decision.lab08_mc_radar import demand_pool, lateness_table

LANE = {0: 0.70, 1: 0.15, 2: 0.08, 3: 0.04, 5: 0.03}     # days late, probability (assumed)
N = 20000
CASES, LANE_DAYS = 3720, 4


def futures(row: P.Projection, net, rng, n: int = N) -> dict:
    """Draw the randomness once: weekly demand ratios, plant truck lateness, lane delay."""
    pool = demand_pool()
    days, p = lateness_table()
    weeks = math.ceil(row.horizon_days / 7)
    trucks = []
    for a in row.arrivals:
        k = max(1, math.ceil(a["cases"] / net.truck_cases))
        for _ in range(k):
            trucks.append((a["day"], a["cases"] / k, rng.choice(days, size=n, p=p).astype(int)))
    lane_k = np.array(list(LANE)), np.array(list(LANE.values()))
    return {"r": rng.choice(pool, size=(n, weeks)), "trucks": trucks,
            "delay": rng.choice(lane_k[0], size=n, p=lane_k[1]).astype(int), "n": n}


def shortage(row: P.Projection, fut: dict, cases: float = 0.0, lands: np.ndarray | None = None) -> np.ndarray:
    """Cases short over the horizon on every future, with an optional transfer landing on `lands`."""
    H, daily, n = row.horizon_days, row.daily_demand, fut["n"]
    landing = np.zeros((n, H + 2))
    for day, q, late in fut["trucks"]:
        d = np.minimum(day + late, H + 1)
        np.add.at(landing, (np.arange(n), d), q)
    if cases:
        d = np.minimum(lands, H + 1)
        np.add.at(landing, (np.arange(n), d), cases)
    bal = np.full(n, float(row.start_on_hand))
    tot = np.zeros(n)
    for d in range(1, H + 1):
        dem = daily * fut["r"][:, (d - 1) // 7]
        served = np.minimum(bal, dem)
        tot += dem - served
        bal = bal - served + landing[:, d]
    return tot


def run(seed: int = 9) -> dict:
    s, p = Store.from_position(), load_profile("gfb")
    net = s.net
    row = P.project(s, p, 1, "CHN", "MN-1L")
    contrib = net.contribution_per_case("MN-1L")
    cpc = net.lane("KOL", "CHN")[1]
    hold_day = net.holding_cost_per_case_day("MN-1L")
    cost = lambda q: q * cpc + q * hold_day * LANE_DAYS
    rng = np.random.default_rng(seed)
    fut = futures(row, net, rng)
    base = shortage(row, fut)
    lands = LANE_DAYS + fut["delay"]
    sent = shortage(row, fut, CASES, lands)
    benefit = (base - sent) * contrib
    c = cost(CASES)
    net_v = benefit - c
    w = P.what_if(s, p, 1, "CHN", "MN-1L", CASES, LANE_DAYS)
    # break-even delay: the radar's one future, and the mean over futures, for each delay
    by_delay = []
    for k in range(0, 9):
        wk = P.what_if(s, p, 1, "CHN", "MN-1L", CASES, LANE_DAYS + k)
        mk = shortage(row, fut, CASES, np.full(fut["n"], LANE_DAYS + k))
        bk = (base - mk) * contrib
        by_delay.append({"delay": k, "radar_benefit": wk["money_avoided"], "mc_benefit": float(bk.mean()),
                         "p_pays": float((bk > c).mean())})
    # the relief truck: when the transfer is 2+ days late (known on day 2), a relief truck lands on day 5
    broke = fut["delay"] >= 2
    relief_lands = np.where(broke, LANE_DAYS + 1, lands)
    with_relief = shortage(row, fut, CASES, relief_lands)
    gain = (sent - with_relief) * contrib
    # transfer size: expected net for each quantity on the same futures
    # Two cost bases. "full freight" is the agent's own accounting: every case pays the lane. "premium": a case
    # that does not cover a shortage is sold later anyway and replaces a Nashik case that would have travelled
    # on NSK->CHN at INR 16, so it only costs the INR 6 premium (plus holding).
    nsk = net.lane("NSK", "CHN")[1]
    sizes = []
    for q in (1500, 2000, 2500, 3000, 3500, 3720, 4000, 4500, 5000):
        sq = shortage(row, fut, q, lands)
        avoided = base - sq
        b = avoided * contrib
        prem = b - cost(q) + nsk * np.maximum(0.0, q - avoided)
        sizes.append({"cases": q, "e_benefit": float(b.mean()), "cost": round(cost(q)), "e_net": float(b.mean() - cost(q)),
                      "p_pays": float((b > cost(q)).mean()), "e_net_premium": float(prem.mean())})
    best = max(sizes, key=lambda x: x["e_net"])
    best_prem = max(sizes, key=lambda x: x["e_net_premium"])
    return {"n": fut["n"], "lane": LANE, "cost": round(c), "lane_cost": round(CASES * cpc), "holding": round(CASES * hold_day * LANE_DAYS),
            "radar": {"before": w["before"]["money_at_risk"], "after": w["after"]["money_at_risk"], "benefit": w["money_avoided"],
                      "ratio": round(w["money_avoided"] / c, 2)},
            "p_pays": float((benefit > c).mean()), "e_benefit": float(benefit.mean()), "e_net": float(net_v.mean()),
            "net_q": [float(np.quantile(net_v, q)) for q in (0.05, 0.1, 0.5, 0.9)],
            "ratio_q": [float(np.quantile(benefit / c, q)) for q in (0.1, 0.5, 0.9)],
            "e_loss_without": float((base * contrib).mean()), "e_loss_with": float((sent * contrib).mean()),
            "by_delay": by_delay, "p_broke": float(broke.mean()), "relief_value": float(gain[broke].mean()),
            "relief_q": [float(np.quantile(gain[broke], q)) for q in (0.1, 0.5, 0.9)], "sizes": sizes, "best": best,
            "best_premium": best_prem, "nsk_cpc": nsk,
            "critical_ratio": round((contrib - cpc - hold_day * LANE_DAYS) / contrib, 3), "contrib": contrib, "cpc": cpc}


def main() -> None:
    r = run()
    rd = r["radar"]
    print(f"F.11 on the radar: INR {rd['before']:,} at risk -> INR {rd['after']:,} after the move; benefit INR {rd['benefit']:,} "
          f"for INR {r['cost']:,} (lane {r['lane_cost']:,} + holding {r['holding']:,}): {rd['ratio']}x")
    print(f"{r['n']:,} futures, lane scorecard (days late: probability) {r['lane']}")
    print(f"  expected margin lost: INR {r['e_loss_without']:,.0f} without the move, INR {r['e_loss_with']:,.0f} with it")
    print(f"  P(the move pays) {r['p_pays']:.3f}; expected benefit INR {r['e_benefit']:,.0f}; expected net INR {r['e_net']:,.0f}")
    q = r["net_q"]
    print(f"  net: P5 INR {q[0]:,.0f}, P10 INR {q[1]:,.0f}, P50 INR {q[2]:,.0f}, P90 INR {q[3]:,.0f}")
    rq = r["ratio_q"]
    print(f"  pays (benefit / cost): P10 {rq[0]:.2f}x, P50 {rq[1]:.2f}x, P90 {rq[2]:.2f}x")
    print("  by lane delay:   delay  radar benefit  mean benefit  P(pays)")
    for x in r["by_delay"]:
        print(f"                  {x['delay']:5d}{x['radar_benefit']:15,}{x['mc_benefit']:14,.0f}{x['p_pays']:9.3f}")
    rq = r["relief_q"]
    print(f"  the transfer is 2+ days late in {r['p_broke']:.3f} of futures; there a relief truck landing on day 5 is worth "
          f"INR {r['relief_value']:,.0f} on average (P10 {rq[0]:,.0f}, P50 {rq[1]:,.0f}, P90 {rq[2]:,.0f})")
    print(f"  transfer size (critical ratio for a transferred case: (62 - 22 - holding) / 62 = {r['critical_ratio']}):")
    print(f"    {'cases':>11}{'E[benefit]':>12}{'cost':>9}{'E[net] full':>13}{'P(pays)':>9}{'E[net] premium':>16}")
    for x in r["sizes"]:
        mark = " <- best (full)" if x["cases"] == r["best"]["cases"] else (" <- best (premium)" if x["cases"] == r["best_premium"]["cases"] else "")
        print(f"    {x['cases']:11,}{x['e_benefit']:12,.0f}{x['cost']:9,}{x['e_net']:13,.0f}{x['p_pays']:9.3f}{x['e_net_premium']:16,.0f}{mark}")
    print(f"  premium basis: a case not needed before day 12 replaces a Nashik case at INR {r['nsk_cpc']:.0f}; best size "
          f"{r['best_premium']['cases']:,} cases (full-freight basis: {r['best']['cases']:,})")
    b = r["best"]
    print(f"Decision: send F.11 (P(pays) {r['p_pays']:.3f}, E[net] INR {r['e_net']:,.0f}), sized at "
          f"{b['cases']:,} cases")
    print(f"  (E[net] INR {b['e_net']:,.0f}). Pre-book the relief truck only when the lane runs 2+ days")
    print(f"  late ({r['p_broke']:.3f} of futures).")

if __name__ == "__main__":
    main()
