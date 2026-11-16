"""Lab 10 · The (s, S) policy: when Chennai should order mango nectar, and how much.

Chennai (CHN) reorders MN-1L from Nashik once a week: GFB plans in weekly buckets (profile: bucket
week). An order placed on Monday lands two Mondays later (F.5: lead time 2 weeks). The rule is two
numbers:
  s   the reorder level: at the Monday review, if stock on hand plus stock on order is at or below s,
      place an order
  S   the order-up-to level: order S minus that position (so S - s is about the order size)
Weekly demand: Chennai's consensus 2,600 x a ratio drawn from Lab 1's pool of 632 ordinary weeks (the
forecast error this network has had: P10 0.793, P90 1.213). A case not on the shelf is a lost sale.
Costs, per week:
  K   INR 18,000 an order: F.6's order cost. ASSUMPTION that F.6's figure is what one Chennai
      replenishment costs (planning, booking, receiving); vary 6,000 to 36,000 (K_GRID)
  h   INR 1.85 a case a week: 20% a year of the INR 480 case cost (F.6), held one week
  p   INR 62 a case short: MN-1L's contribution (F.6), the margin a lost sale takes away.
      ASSUMPTION that a short case is lost (a kirana buys another brand); vary to INR 31, half the
      margin, for a customer who waits (P_GRID)
Four rules, scored on the same simulated weeks (common random numbers: the difference is the rule):
  ROP + EOQ        s = F.5's reorder point 6,247 (95% over the 2-week lead time), S = s + EOQ
  R+L fix + EOQ    s = 95% of demand over the review week plus the lead time (3 weeks), S = s + EOQ
  every week       order up to S every Monday (s = S - 1, no order cost saved); best S on the grid
  best (s, S)      the lowest simulated cost on a grid of s and S - s
"""
from __future__ import annotations

import math

import numpy as np

from foundry import canon
from foundry.network import GFB
from labs.decision import core
from labs.decision.lab08_mc_radar import demand_pool

SKU, DC = "MN-1L", "CHN"
MU, SIGMA, LEAD = 2600.0, 450.0, 2               # F.5 (the simulation draws from the pool, not this normal)
K = float(canon.ORDER_COST)                       # 18,000 (assumption that it applies here)
H = GFB.holding_cost_per_case_day(SKU) * 7        # 1.84 a case a week
P = GFB.contribution_per_case(SKU)                # 62
K_GRID = (6000.0, 18000.0, 36000.0)
P_GRID = (31.0, 62.0)
S_GRID = np.arange(5000, 12001, 250)              # reorder levels tried
Q_GRID = np.arange(1000, 14001, 500)              # S - s tried
N_WEEKS, WARM = 20000, 52


def demand(n_weeks: int = N_WEEKS, seed: int = 10) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return MU * rng.choice(demand_pool(), size=n_weeks + WARM)


def simulate(s: np.ndarray, S: np.ndarray, d: np.ndarray, k: float = K, h: float = H, p: float = P) -> dict:
    """Weekly review, lead time LEAD weeks, lost sales; every (s[i], S[i]) on the same demand d.
    Monday: the order placed LEAD weeks ago lands; review; order S - position if position <= s.
    The week: serve min(on hand, demand), the rest is lost; pay h on what is left on Sunday."""
    s, S = np.asarray(s, float), np.asarray(S, float)
    n = len(s)
    on_hand = S.copy()
    pipe = np.zeros((LEAD, n))                     # pipe[j]: lands in j + 1 weeks
    orders, served, lost, held = (np.zeros(n) for _ in range(4))
    for t, dem in enumerate(d):
        on_hand += pipe[0]
        pipe = np.roll(pipe, -1, axis=0)
        pipe[-1] = 0.0
        position = on_hand + pipe.sum(axis=0)
        q = np.where(position <= s, S - position, 0.0)
        pipe[-1] = q
        sv = np.minimum(on_hand, dem)
        on_hand -= sv
        if t >= WARM:
            orders += q > 0
            served += sv
            lost += dem - sv
            held += on_hand
    weeks = len(d) - WARM
    dem_tot = float(d[WARM:].sum())
    return {"cost": (k * orders + h * held + p * lost) / weeks, "order_cost": k * orders / weeks, "hold_cost": h * held / weeks,
            "short_cost": p * lost / weeks, "fill": served / dem_tot, "orders_year": orders / weeks * 52, "avg_on_hand": held / weeks}


def grid(d: np.ndarray, k: float = K, p: float = P) -> dict:
    ss, qq = np.meshgrid(S_GRID, Q_GRID, indexing="ij")
    r = simulate(ss.ravel(), (ss + qq).ravel(), d, k=k, p=p)
    cost = r["cost"].reshape(ss.shape)
    i, j = np.unravel_index(int(np.argmin(cost)), cost.shape)
    return {"cost": cost, "s": float(S_GRID[i]), "Q": float(Q_GRID[j]), "best": float(cost[i, j])}


def row(name: str, s: float, S: float, d: np.ndarray) -> dict:
    r = simulate(np.array([s]), np.array([S]), d)
    return {"rule": name, "s": round(s), "S": round(S), **{k: float(v[0]) for k, v in r.items()}}


def run(n_weeks: int = N_WEEKS, seed: int = 10) -> dict:
    d = demand(n_weeks, seed)
    pool = demand_pool()
    sd_week = float(MU * pool.std(ddof=1))
    eoq = math.sqrt(2 * MU * 52 * K / (H * 52))
    rop = GFB.reorder_point(SKU, DC)
    z95 = core.N01.inv_cdf(0.95)
    s_rl = MU * (LEAD + 1) + z95 * SIGMA * math.sqrt(LEAD + 1)
    g = grid(d)
    every = simulate(S_GRID - 1.0, S_GRID.astype(float), d)
    je = int(np.argmin(every["cost"]))
    rows = [row("ROP + EOQ", rop, rop + eoq, d), row("R+L fix + EOQ", s_rl, s_rl + eoq, d),
            row("every week", float(S_GRID[je]) - 1.0, float(S_GRID[je]), d),
            row("best (s, S)", g["s"], g["s"] + g["Q"], d)]
    # how flat is the bottom: cost (and order frequency) along each axis through the optimum
    i0, j0 = list(S_GRID).index(g["s"]), list(Q_GRID).index(g["Q"])
    along_s = [(int(S_GRID[i]), float(g["cost"][i, j0])) for i in range(len(S_GRID))]
    oq = simulate(np.full(len(Q_GRID), g["s"]), g["s"] + Q_GRID.astype(float), d)
    along_q = [(int(Q_GRID[j]), float(oq["cost"][j]), float(52 / oq["orders_year"][j])) for j in range(len(Q_GRID))]
    dips = [x for k, x in enumerate(along_q) if 0 < k < len(along_q) - 1 and x[1] < along_q[k - 1][1] and x[1] < along_q[k + 1][1]]
    low, high = dict(along_s)[int(g["s"]) - 1500], dict(along_s)[int(g["s"]) + 1500]
    near = g["cost"] <= g["best"] * 1.02
    sens = []
    for k in K_GRID:
        for p in P_GRID:
            gk = grid(d, k=k, p=p)
            sens.append({"K": k, "p": p, "s": gk["s"], "S": gk["s"] + gk["Q"], "cost": gk["best"],
                         "eoq": math.sqrt(2 * MU * 52 * k / (H * 52))})
    return {"n_weeks": n_weeks, "mu": MU, "sd_week": sd_week, "K": K, "h": H, "p": P, "eoq": eoq, "rop": rop, "s_rl": s_rl,
            "rows": rows, "best": g, "along_s": along_s, "along_q": along_q, "dips": dips, "low": low, "high": high,
            "near_s": (int(S_GRID[near.any(axis=1)].min()), int(S_GRID[near.any(axis=1)].max())),
            "near_q": (int(Q_GRID[near.any(axis=0)].min()), int(Q_GRID[near.any(axis=0)].max())), "sens": sens,
            "saving_vs_rop": rows[0]["cost"] - rows[3]["cost"]}


def main() -> None:
    r = run()
    b = r["best"]
    best = r["rows"][3]
    print(f"CHN MN-1L, weekly review, lead time {LEAD} weeks, {r['n_weeks']:,} simulated weeks")
    print(f"  demand {r['mu']:,.0f} a week (sd {r['sd_week']:,.0f}, from the pool); K INR {r['K']:,.0f} an order")
    print(f"  h INR {r['h']:.2f} a case a week; p INR {r['p']:.0f} a case short (a lost sale)")
    print(f"  EOQ {r['eoq']:,.0f} cases; F.5 reorder point {r['rop']:,.0f}; "
          f"95% s over review + lead (3 weeks) {r['s_rl']:,.0f}")
    print(f"  {'rule':15}{'s':>7}{'S':>8}{'INR/wk':>8}{'order':>7}{'hold':>7}{'short':>7}{'fill':>7}{'orders/yr':>10}")
    for x in r["rows"]:
        print(f"  {x['rule']:15}{x['s']:7,}{x['S']:8,}{x['cost']:8,.0f}{x['order_cost']:7,.0f}{x['hold_cost']:7,.0f}"
              f"{x['short_cost']:7,.0f}{x['fill']:7.3f}{x['orders_year']:10.1f}")
    print(f"  within 2% of the best: s {r['near_s'][0]:,}-{r['near_s'][1]:,}, "
          f"S - s {r['near_q'][0]:,}-{r['near_q'][1]:,}")
    print(f"  s 1,500 too low costs INR {r['low']:,.0f}/wk; 1,500 too high INR {r['high']:,.0f}/wk")
    print("  cost dips along S - s where an order comes every whole number of weeks:")
    for q, c, w in r["dips"]:
        print(f"    S - s {q:6,}: an order every {w:.1f} weeks, INR {c:,.0f}/wk")
    print("  sensitivity (best s, S on the grid):")
    for x in r["sens"]:
        print(f"    K {x['K']:6,.0f}  p {x['p']:4.0f}:  s {x['s']:7,.0f}  S {x['S']:7,.0f}  INR {x['cost']:6,.0f}/wk"
              f"  (EOQ {x['eoq']:,.0f})")
    print(f"Decision: Chennai MN-1L to s = {b['s']:,.0f}, S = {b['s'] + b['Q']:,.0f} (fill {best['fill']:.3f}, "
          f"{best['orders_year']:.0f} orders a year):")
    print(f"  INR {r['saving_vs_rop']:,.0f} a week less than ROP + EOQ, "
          f"INR {r['saving_vs_rop'] * 52 / 1e5:.1f} lakh a year on one SKU at one DC.")


if __name__ == "__main__":
    main()
