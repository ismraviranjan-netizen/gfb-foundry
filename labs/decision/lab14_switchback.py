"""Lab 14 · A switchback test: switch the agent on and off for the whole network, week by week.

Why time and not places: the rebalancing agent moves stock between DCs, so a DC that has an agent
changes the DCs that do not (a transfer into Chennai is stock Kolkata no longer has), and a
DC-against-DC comparison (Lab 13) breaks. For anything that acts on the whole network, randomise time
instead: the network runs with the agent ON in some weeks and OFF in others, by coin toss, and ON weeks
are compared with OFF weeks. Here the agent is the sensing agent on all forty rows, weeks 65-104: the
effect plansim can compute both ways, so every design can be checked against the truth.
The trap is carryover: an order placed in an ON week lands one or two weeks later, maybe in an OFF week,
and stock bought in an ON block is still on the shelf when the OFF block starts.
  designs   blocks of B weeks (1, 2, 4, 5 or 10); in each pair of blocks a coin decides which is ON;
            the first `washout` weeks of every block (0, 1 or 2) are left out of the comparison
  scored    600 coin-toss schedules per design, each run through the simulation: bias against the
            truth (all ON minus all OFF), spread, root-mean-square error
  the pilot one schedule of the best design (seed 14): its estimate and standard error from the
            pilot's own weeks, then Fisher's exact test: keep the outcomes, re-toss the coins every
            possible way (how often does chance alone give as much?)
Estimate = mean weekly network cost in kept OFF weeks - in kept ON weeks (positive = the agent saves).
ASSUMPTIONS: those of plansim; blocks are paired so each pair holds one ON and one OFF (the season is
balanced inside a pair); the 90% interval treats kept weeks as independent (Neyman's variance).
"""
from __future__ import annotations

import numpy as np

from labs.decision import plansim as P

DESIGNS = [(1, 0), (2, 0), (2, 1), (4, 0), (4, 1), (4, 2), (5, 0), (5, 1), (5, 2), (10, 0), (10, 1), (10, 2)]
N_SCHEDULES, SEED = 600, 14


def n_weeks() -> int:
    return int(P.pilot_weeks().sum())


def toss(B: int, n: int, rng) -> np.ndarray:
    """n paired coin-toss schedules over the pilot weeks: (n, 40) booleans, True = ON."""
    nb = n_weeks() // B
    pairs = rng.random((n, nb // 2)) < 0.5
    lab = np.stack([pairs, ~pairs], axis=2).reshape(n, -1)
    return np.repeat(lab, B, axis=1)


def kept(B: int, washout: int) -> np.ndarray:
    return (np.arange(n_weeks()) % B) >= washout


def network_cost(schedules: np.ndarray, part: str = "cost") -> np.ndarray:
    """(n, 40) pilot schedules -> (n, 40) weekly network cost (weeks 54-64 are OFF)."""
    W = len(P.inputs()["weeks"])
    full = np.zeros((len(schedules), W), bool)
    full[:, P.pilot_weeks()] = schedules
    out = P.simulate(full, many=True)[part]
    return out.sum(axis=1)[:, P.pilot_weeks()]


def estimate(y: np.ndarray, on: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """Mean of kept OFF weeks - mean of kept ON weeks, row by row."""
    y, on = np.atleast_2d(y), np.atleast_2d(on)
    k_on, k_off = on & keep, ~on & keep
    return (y * k_off).sum(1) / k_off.sum(1) - (y * k_on).sum(1) / k_on.sum(1)


def truth() -> dict:
    off, on = P.both()
    w = P.pilot_weeks()
    d = {k: (off[k][0] - on[k][0]).sum(axis=0)[w] for k in ("cost", "lost_money", "hold")}
    first, last = d["cost"][:20].mean(), d["cost"][20:].mean()
    return {"per_week": float(d["cost"].mean()), "margin": float(d["lost_money"].mean()), "hold": float(d["hold"].mean()),
            "first20": float(first), "last20": float(last), "weekly": d["cost"]}


def score_designs(n: int = N_SCHEDULES, seed: int = SEED) -> list[dict]:
    t = truth()["per_week"]
    rng = np.random.default_rng(seed)
    rows = []
    cache = {}
    for B, wo in DESIGNS:
        if B not in cache:
            D = toss(B, n, rng)
            cache[B] = (D, network_cost(D))
        D, y = cache[B]
        e = estimate(y, D, kept(B, wo))
        rows.append({"B": B, "washout": wo, "mean": float(e.mean()), "bias": float(e.mean() - t), "sd": float(e.std()),
                     "rmse": float(np.sqrt(((e - t) ** 2).mean())), "kept": int(kept(B, wo).sum())})
    return rows


def all_tosses(B: int) -> np.ndarray:
    """Every way the coins can fall: 2 ** (number of pairs) schedules (16 for 5-week blocks)."""
    k = n_weeks() // B // 2
    bits = ((np.arange(2 ** k)[:, None] >> np.arange(k)) & 1).astype(bool)
    lab = np.stack([bits, ~bits], axis=2).reshape(2 ** k, -1)
    return np.repeat(lab, B, axis=1)


def fisher(y: np.ndarray, on: np.ndarray, B: int, wo: int) -> dict:
    """Fisher's exact test: keep the outcomes, re-toss the coins every possible way. If the agent did
    nothing, every re-toss was as likely as the real one: p = the share of them with an estimate at
    least as far from zero. With k pairs the smallest possible two-sided p is 2 / 2 ** k."""
    keep = kept(B, wo)
    D = all_tosses(B)
    obs = float(estimate(y, on, keep)[0])
    null = estimate(np.broadcast_to(y, D.shape), D, keep)
    return {"estimate": obs, "p": float(np.mean(np.abs(null) >= abs(obs) - 1e-9)), "n_tosses": len(D),
            "p_min": 2.0 / len(D)}


def neyman(y: np.ndarray, on: np.ndarray, keep: np.ndarray) -> dict:
    """The difference in means and its standard error, from the pilot's own weeks (Neyman's
    conservative variance: var(OFF) / n_off + var(ON) / n_on); a normal 90% interval."""
    a, b = y[keep & ~on], y[keep & on]
    est = float(a.mean() - b.mean())
    se = float(np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)))
    return {"estimate": est, "se": se, "ci": [est - 1.645 * se, est + 1.645 * se]}


def run(n: int = N_SCHEDULES, seed: int = SEED) -> dict:
    t = truth()
    rows = score_designs(n, seed)
    best = min(rows, key=lambda x: x["rmse"])
    B, wo = best["B"], best["washout"]
    rng = np.random.default_rng(seed + 100)
    on = toss(B, 1, rng)
    parts = {k: network_cost(on, k)[0] for k in ("cost", "lost_money", "hold")}
    keep = kept(B, wo)
    f = fisher(parts["cost"], on[0], B, wo)
    ny = neyman(parts["cost"], on[0], keep)
    weekly = next(x for x in rows if x["B"] == 1 and x["washout"] == 0)
    return {"truth": t, "designs": rows, "best": best, "schedule": on[0], "pilot_cost": parts["cost"], "keep": keep,
            "estimate": ny["estimate"], "se": ny["se"], "ci": ny["ci"], "p": f["p"], "n_tosses": f["n_tosses"],
            "p_min": f["p_min"], "margin": float(estimate(parts["lost_money"], on[0], keep)[0]),
            "margin_se": neyman(parts["lost_money"], on[0], keep)["se"],
            "hold": float(estimate(parts["hold"], on[0], keep)[0]), "weekly_design": weekly,
            "n_on": int((on[0] & keep).sum()), "n_off": int((~on[0] & keep).sum())}


def main() -> None:
    r = run()
    t = r["truth"]
    print(f"the truth (simulation): the agent saves INR {t['per_week']:,.0f} a week network-wide, weeks 65-104")
    print(f"  of which margin {t['margin']:+,.0f} and holding {t['hold']:+,.0f}")
    print(f"  weeks 65-84 INR {t['first20']:,.0f} a week, weeks 85-104 INR {t['last20']:,.0f}: "
          f"the saving is seasonal")
    print(f"designs ({N_SCHEDULES} coin-toss schedules each): estimate of the weekly saving")
    print(f"  {'block':>5}{'washout':>8}{'weeks kept':>11}{'mean':>9}{'bias':>9}{'sd':>8}{'rmse':>8}")
    for x in r["designs"]:
        print(f"  {x['B']:5d}{x['washout']:8d}{x['kept']:11d}{x['mean']:9,.0f}{x['bias']:9,.0f}{x['sd']:8,.0f}{x['rmse']:8,.0f}")
    b = r["best"]
    w = r["weekly_design"]
    print(f"  week-by-week, no washout: mean INR {w['mean']:,.0f}: carryover flips the sign")
    print(f"the pilot: blocks of {b['B']} weeks, washout {b['washout']} (lowest rmse), seed {SEED}: "
          f"{r['n_on']} ON and {r['n_off']} OFF weeks kept")
    print("  schedule: " + "".join("X" if s else "." for s in r["schedule"]) + "  (X = ON)")
    print(f"  estimate INR {r['estimate']:,.0f} a week, of which margin {r['margin']:+,.0f} and holding "
          f"{r['hold']:+,.0f}")
    print(f"  standard error {r['se']:,.0f} from the pilot's own weeks: 90% interval "
          f"INR {r['ci'][0]:,.0f} to {r['ci'][1]:,.0f}")
    print(f"  the simulation's {N_SCHEDULES} pilots of this design: sd {b['sd']:,.0f} around the truth")
    print(f"  Fisher's exact test: p = {r['p']:.3f} over all {r['n_tosses']} ways the coins could fall")
    print(f"  (with {r['n_tosses']} ways, no pilot of this length can show p below {r['p_min']:.3f})")
    print(f"Decision: book INR {r['estimate']:,.0f} a week (90%: {r['ci'][0]:,.0f} to {r['ci'][1]:,.0f}), "
          f"from {b['B']}-week")
    print(f"  blocks with a {b['washout']}-week washout; never alternate week by week. To call it "
          f"proven, add pairs.")


if __name__ == "__main__":
    main()
