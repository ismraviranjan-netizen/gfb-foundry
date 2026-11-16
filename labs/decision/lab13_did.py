"""Lab 13 · Difference-in-differences: what did the sensing agent do at Gurgaon?

The pilot: from week 77 Kabir lets the sensing agent drive Gurgaon's ten rows; Bhiwandi, Kolkata and
Chennai stay on the consensus. The outcome is what each DC's orders cost a week in the planning
simulation (labs.decision.plansim: margin lost on short cases plus holding). Window: twelve weeks before
(65-76) and twelve after (77-88). Three answers:
  naive    Gurgaon after - Gurgaon before: the before-and-after slide
  DiD      (Gurgaon after - before) - (controls after - before): the controls carry whatever changed
           for everyone (the summer ending, a festival, a price move)
  truth    Gurgaon with the agent - Gurgaon without, weeks 77-88: only a simulation has both futures
Checks a real pilot can run without the truth:
  pre-trend  the gap Gurgaon - controls week by week before week 77; a slope means they were already
             drifting apart (parallel trends fails, and the DiD inherits the drift)
  placebos   the same DiD on the no-agent future, at every other 24-week window and with each DC
             as the 'treated' one: the size of a DiD when nothing happened
  bootstrap  resample weeks inside before and after: a 90% interval for the estimate
Sign: a negative change is a cost that fell (a saving). ASSUMPTIONS: those of plansim, plus the control:
the average of the other three DCs in rupees (vary: scaled to Gurgaon's before level, or one DC alone).
"""
from __future__ import annotations

import numpy as np

from labs.decision import plansim as P

TREATED, START, PRE, POST = "GGN", 77, 12, 12


def weekly_by_dc(cost: np.ndarray) -> dict:
    """cost (n_series, W) -> {dc: weekly cost over weeks 54-104}."""
    x = P.inputs()
    return {dc: cost[x["dc"] == dc].sum(axis=0) for dc in P.DCS}


def did(by_dc: dict, treated: str, start: int, pre: int = PRE, post: int = POST, scaled: bool = False,
        controls=None) -> dict:
    weeks = P.inputs()["weeks"]
    b = (weeks >= start - pre) & (weeks < start)
    a = (weeks >= start) & (weeks < start + post)
    ctrl_dcs = controls or [d for d in P.DCS if d != treated]
    tr = by_dc[treated]
    co = np.mean([by_dc[d] for d in ctrl_dcs], axis=0)
    if scaled:
        co = co * tr[b].mean() / co[b].mean()
    naive = tr[a].mean() - tr[b].mean()
    est = naive - (co[a].mean() - co[b].mean())
    return {"naive": float(naive), "did": float(est), "gap": tr - co, "before": b, "after": a,
            "tr_before": float(tr[b].mean()), "tr_after": float(tr[a].mean()), "co_before": float(co[b].mean()),
            "co_after": float(co[a].mean())}


def run(seed: int = 13, n_boot: int = 4000) -> dict:
    x = P.inputs()
    weeks = x["weeks"]
    off, _ = P.both()
    sched = P.schedule(weeks[weeks >= P.PILOT] >= START, dcs=(TREATED,))
    pilot = P.simulate(sched)
    by_off = weekly_by_dc(off["cost"][0])
    by_pilot = weekly_by_dc(pilot["cost"][0])
    d = did(by_pilot, TREATED, START)
    truth = float((by_pilot[TREATED] - by_off[TREATED])[d["after"]].mean())
    scaled = did(by_pilot, TREATED, START, scaled=True)["did"]
    single = {c: did(by_pilot, TREATED, START, controls=[c])["did"] for c in P.DCS if c != TREATED}
    # pre-trend: slope of the gap before the start, in INR per week
    gb = d["gap"][d["before"]]
    xs = np.arange(len(gb), dtype=float)
    slope, icpt = np.polyfit(xs, gb, 1)
    resid = gb - (slope * xs + icpt)
    slope_se = float(np.sqrt(resid.var(ddof=2) / ((xs - xs.mean()) ** 2).sum()))
    slope = float(slope)
    event = [(int(w), float(g - gb.mean())) for w, g, k in zip(weeks, d["gap"], d["before"] | d["after"]) if k]
    # placebos: no agent anywhere, every window that fits in weeks 65-104, every DC as the treated one
    plac = []
    for dc in P.DCS:
        for s in range(P.PILOT + PRE, P.LAST - POST + 2):
            if dc == TREATED and s == START:
                continue
            plac.append(did(by_off, dc, s)["did"])
    plac = np.array(plac)
    # bootstrap the weeks inside each period
    rng = np.random.default_rng(seed)
    ga = d["gap"][d["after"]]
    boot = np.array([rng.choice(ga, len(ga)).mean() - rng.choice(gb, len(gb)).mean() for _ in range(n_boot)])
    lo, hi = np.quantile(boot, [0.05, 0.95])
    # what the same agent is worth at Gurgaon over the whole 40 weeks, and network-wide
    _, on = P.both()
    pw = weeks >= P.PILOT
    ggn40 = float((weekly_by_dc(on["cost"][0])[TREATED] - by_off[TREATED])[pw].mean())
    win = d["before"] | d["after"]
    series = {"weeks": weeks[win].tolist(), "treated": by_pilot[TREATED][win].tolist(),
              "treated_off": by_off[TREATED][win].tolist(),
              "controls": np.mean([by_pilot[c] for c in P.DCS if c != TREATED], axis=0)[win].tolist()}
    return {"naive": d["naive"], "did": d["did"], "truth": truth, "series": series, "scaled": scaled, "single": single,
            "tr_before": d["tr_before"], "tr_after": d["tr_after"], "co_before": d["co_before"], "co_after": d["co_after"],
            "slope": slope, "slope_se": slope_se, "drift": slope * PRE, "event": event, "placebo_sd": float(plac.std()),
            "placebo_q": [float(np.quantile(plac, q)) for q in (0.05, 0.95)], "n_placebo": len(plac),
            "placebo_beyond": float(np.mean(np.abs(plac) >= abs(d["did"]))), "boot": [float(lo), float(hi)],
            "ggn_40wk": ggn40}


def main() -> None:
    r = run()
    print(f"Gurgaon pilot: the agent from week {START}; before weeks {START - PRE}-{START - 1}, "
          f"after {START}-{START + POST - 1}")
    print(f"  weekly cost (INR): Gurgaon {r['tr_before']:,.0f} -> {r['tr_after']:,.0f}; "
          f"controls {r['co_before']:,.0f} -> {r['co_after']:,.0f}")
    print(f"  naive before/after  {r['naive']:9,.0f} a week")
    print(f"  difference-in-diff  {r['did']:9,.0f} a week (controls scaled to Gurgaon: {r['scaled']:,.0f})")
    print(f"  truth (simulation)  {r['truth']:9,.0f} a week")
    print("  one control DC at a time: " + ", ".join(f"{k} {v:,.0f}" for k, v in r["single"].items()))
    print(f"  pre-trend: the gap moved {r['slope']:,.0f} a week before the start (standard error "
          f"{r['slope_se']:,.0f}): {abs(r['slope']) / r['slope_se']:.1f} se")
    q = r["placebo_q"]
    print(f"  placebos ({r['n_placebo']} windows with no agent): 90% between {q[0]:,.0f} and {q[1]:,.0f}; "
          f"{r['placebo_beyond']:.0%} as large")
    print(f"  bootstrap 90% interval: {r['boot'][0]:,.0f} to {r['boot'][1]:,.0f}")
    print(f"  the agent at Gurgaon over all 40 pilot weeks (simulation): {r['ggn_40wk']:,.0f} a week")
    print(f"Decision: book INR {-r['did']:,.0f} a week at Gurgaon (DiD), not the slide's "
          f"INR {-r['naive']:,.0f};")
    print(f"  with placebos at +/- INR {max(map(abs, q)):,.0f}, 12 weeks at one DC cannot tell it from zero:")
    print("  measure the network with a switchback before the number goes to Ananya (Lab 14).")


if __name__ == "__main__":
    main()
