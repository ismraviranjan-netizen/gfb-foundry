"""Lab 15 · The value ledger: the agents' measured effect as one number a VP can sign, with its range.

Ananya Kulkarni (VP supply chain) asks one question: what is the sensing agent worth to GFB in a year?
The ledger answers line by line, and every line says how it is known:
  measured   the switchback pilot of Lab 14 (blocks of five weeks, one-week washout, weeks 65-104):
             the weekly saving and its standard error, split into margin protected and stock carried
  data       how often the agent adjusts a row, and how often an adjustment needs a co-sign
             (data.sensed2: decision "adjust"; above profile sensing.cosign_above_pct, 25%)
  assumed    everything else, with a low, likely and high value (a triangle), named here to vary:
    OFF_SEASON  the 12 weeks a year the pilot did not cover (January to March), as a share of the
                pilot's weekly saving: 0.25 / 0.5 / 1.0 (they hold Holi and Pongal, not the summer)
    RUN_INR     one sensing run: 2.5 / 3.56 / 8.0 rupees (The Agent Desk's scripted run is INR 3.56;
                a live model may read more), one run per row per week
    REVIEW      Kabir's review of one adjustment: 3 / 5 / 10 minutes at INR 600 / 800 / 1,200 an hour
    COSIGN      Devika's co-sign: 5 / 10 / 15 minutes at INR 1,000 / 1,500 / 2,500 an hour
    PLATFORM    the AWS bill: INR 2,000 / 5,415 / 5,415 a month (The Live Tower's second budget alarm is
                USD 57 a month, at the repo's INR 95 a dollar: a ceiling)
    BUILD       one-off build and pilot: INR 3 / 6 / 10 lakh (for the payback only)
20,000 draws (seed 15): the weekly saving from a normal with the pilot's estimate and standard error,
every assumption from its triangle. Out come P10, P50 and P90 per line and for the net, P(net > 0) and
the payback. Then the four numbers that must not reach the VP, each computed by an earlier lab.
"""
from __future__ import annotations

import numpy as np

from foundry.bedrock_client import INR_PER_USD
from foundry.data.generate import canonical_store
from foundry.profile import load_profile
from foundry.agents.rca_exception.queue import build_queue
from labs.decision import plansim as P
from labs.decision.data import sensed2

OFF_SEASON = (0.25, 0.5, 1.0)
RUN_INR = (2.5, 3.56, 8.0)
REVIEW_MIN, REVIEW_RATE = (3.0, 5.0, 10.0), (600.0, 800.0, 1200.0)
COSIGN_MIN, COSIGN_RATE = (5.0, 10.0, 15.0), (1000.0, 1500.0, 2500.0)
PLATFORM_MONTH = (2000.0, 57.0 * INR_PER_USD, 57.0 * INR_PER_USD)
BUILD = (3e5, 6e5, 10e5)
PILOT_WEEKS, YEAR = 40, 52
N, SEED = 20000, 15
LAKH = 1e5


def tri(rng, low_mode_high, n):
    a, m, b = low_mode_high
    return np.full(n, m) if a == b else rng.triangular(a, m, b, n)


def workload() -> dict:
    """From the agent's own decisions over the pilot weeks: adjustments and co-signs a week."""
    sn = sensed2(54, 104, 3)
    s = sn[(sn.target >= P.PILOT) & (sn.target <= P.LAST)]
    cosign = load_profile("gfb").section("sensing").get("cosign_above_pct", 0.25)
    adj = s[s.decision == "adjust"]
    weeks = s.target.nunique()
    return {"runs_week": len(s) / weeks, "adjust_week": len(adj) / weeks,
            "cosign_week": float((adj.adjustment_pct.abs() > cosign).sum()) / weeks, "cosign_pct": cosign}


def ledger(effect: float, se: float, margin: float, hold: float, n: int = N, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    w = workload()
    weekly = rng.normal(effect, se, n)
    lines = {
        "pilot weeks": weekly * PILOT_WEEKS,
        "weeks outside": weekly * (YEAR - PILOT_WEEKS) * tri(rng, OFF_SEASON, n),
        "agent runs": -w["runs_week"] * YEAR * tri(rng, RUN_INR, n),
        "Kabir's reviews": -w["adjust_week"] * YEAR * tri(rng, REVIEW_MIN, n) / 60 * tri(rng, REVIEW_RATE, n),
        "Devika's co-signs": -w["cosign_week"] * YEAR * tri(rng, COSIGN_MIN, n) / 60 * tri(rng, COSIGN_RATE, n),
        "platform": -12 * tri(rng, PLATFORM_MONTH, n),
    }
    net = sum(lines.values())
    build = tri(rng, BUILD, n)
    payback = np.where(net > 0, build / np.maximum(net, 1e-9) * YEAR, np.inf)
    q = lambda v: [float(np.quantile(v, k)) for k in (0.1, 0.5, 0.9)]
    return {"lines": {k: q(v) for k, v in lines.items()}, "net": q(net), "p_positive": float(np.mean(net > 0)),
            "payback_weeks": q(payback), "workload": w, "share_margin": margin / effect if effect else float("nan"),
            "margin": margin, "hold": hold, "net_draws": net}


def run(n: int = N, seed: int = SEED) -> dict:
    from labs.decision import lab12_regression_mean as L12, lab13_did as L13, lab14_switchback as L14
    sb = L14.run()
    led = ledger(sb["estimate"], sb["se"], sb["margin"], sb["hold"], n, seed)
    # the simulation's check (a pilot never has it): ON from week 55 against OFF, weeks 55-104
    x = P.inputs()
    wk = x["weeks"]
    off = P.simulate(np.zeros(len(wk), bool))["cost"][0].sum(0)
    on = P.simulate(wk >= 55)["cost"][0].sum(0)
    d = off - on
    off_ratio = float(d[(wk >= 55) & (wk < P.PILOT)].mean() / d[wk >= P.PILOT].mean())
    true_year = float(d[wk >= 55].mean() * YEAR)
    q = build_queue(canonical_store(), load_profile("gfb"), 12, propose=False)
    exposure = sum(it.impact_inr for it in q.items)
    did = L13.run()
    rtm = L12.run()
    wrong = [("week-12 queue, money at stake", exposure, "exposure for one week, not value"),
             ("Gurgaon before/after x 52", -did["naive"] * YEAR, "the summer ending, not the agent"),
             ("switchback week by week x 52", sb["weekly_design"]["mean"] * YEAR, "carryover flips the sign"),
             ("worst ten rows, error cut", rtm["claim"], f"{rtm['rtm']:.1f} of them are luck")]
    return {"switchback": {k: sb[k] for k in ("estimate", "se", "ci", "p", "margin", "hold")}, "ledger": led,
            "sim_off_ratio": off_ratio, "sim_true_year": true_year, "wrong": wrong, "best": sb["best"]}


def main() -> None:
    r = run()
    s, L = r["switchback"], r["ledger"]
    w = L["workload"]
    print(f"measured (Lab 14 switchback): INR {s['estimate']:,.0f} a week saved, standard error "
          f"{s['se']:,.0f}")
    print(f"  of which margin protected {s['margin']:+,.0f} and stock carried {s['hold']:+,.0f} a week")
    print(f"data: {w['runs_week']:.0f} runs, {w['adjust_week']:.1f} adjustments and {w['cosign_week']:.1f} "
          f"co-signs (above {w['cosign_pct']:.0%}) a week")
    print(f"the ledger, one year, INR lakh ({N:,} draws)")
    print(f"  {'line':18}{'P10':>8}{'P50':>8}{'P90':>8}   known by")
    how = {"pilot weeks": "measured: Lab 14, 40 weeks", "weeks outside": "assumed: 12 weeks x 0.25-1.0",
           "agent runs": "data x INR 2.5-8 a run", "Kabir's reviews": "data x 3-10 min x INR 600-1,200/h",
           "Devika's co-signs": "data x 5-15 min x INR 1,000-2,500/h", "platform": "assumed: up to USD 57 a month"}
    for k, v in L["lines"].items():
        print(f"  {k:18}{v[0] / LAKH:8.2f}{v[1] / LAKH:8.2f}{v[2] / LAKH:8.2f}   {how[k]}")
    n = L["net"]
    print(f"  {'net a year':18}{n[0] / LAKH:8.2f}{n[1] / LAKH:8.2f}{n[2] / LAKH:8.2f}")
    pb = L["payback_weeks"]
    print(f"  P(net > 0) = {L['p_positive']:.3f}; payback on a one-off INR 3-10 lakh build: "
          f"{pb[1]:.0f} weeks (P50), {pb[2]:.0f} (P90)")
    print(f"simulation check: weeks outside run at {r['sim_off_ratio']:.2f}x the pilot rate; "
          f"true value INR {r['sim_true_year'] / LAKH:.1f} lakh a year")
    print("numbers that must not reach the VP:")
    for name, val, why in r["wrong"]:
        shown = f"{val:.1f} points" if abs(val) < 100 else f"INR {val / LAKH:.1f} lakh"
        print(f"  {name:31}{shown:>15}   {why}")
    print(f"Decision (the VP line): the sensing agent is worth INR {n[1] / LAKH:.0f} lakh a year to GFB, net "
          f"of running")
    print(f"  costs (80% range INR {n[0] / LAKH:.0f} to {n[2] / LAKH:.0f} lakh), measured by a 40-week "
          f"switchback;")
    print(f"  P(it loses money) = {1 - L['p_positive']:.2f}. Re-measure with more pairs before the budget round.")


if __name__ == "__main__":
    main()
