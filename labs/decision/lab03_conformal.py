"""Lab 3 · Split conformal intervals around the sensing agent's number.

For each target week t (13-20) the calibration set is every one-step forecast the agent made for an
earlier week (targets 9..t-1, all forty series): the score is the relative miss e = actual / sensed - 1.
Ordinary weeks and promo weeks are calibrated separately (Mondrian conformal): a promo week is not
exchangeable with an ordinary one.
  ordinary  two one-sided quantiles, alpha/2 each side: [sensed (1 + q_lo), sensed (1 + q_hi)]
  promo     few scores, so a symmetric score |e|: sensed (1 +/- q); needs at least 4 scores for 80%
Then the Pongal call: origin 17, week 18, sensed 4,270.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from labs.decision import core
from labs.decision.data import one_step
from labs.decision.lab02_scoring import TAUS, forecasters, frame, score

ALPHA = 0.2


def band_for(cal: pd.DataFrame, sensed: float, promo: bool, alpha: float = ALPHA) -> dict:
    grp = cal[cal.promo == promo]
    e = list(grp.actual / grp.sensed - 1.0)
    n = len(e)
    if promo:
        q = core.conformal_q([abs(x) for x in e], alpha)
        how = f"symmetric |e|, n={n}"
        if not math.isfinite(q):
            # too few promo weeks to promise 1 - alpha: borrow every week's score and say so
            e_all = list(cal.actual / cal.sensed - 1.0)
            q = core.conformal_q([abs(x) for x in e_all], alpha)
            how = f"fallback: only {n} promo scores, symmetric |e| over all {len(e_all)}"
        lo, hi = -q, q
        mid = 0.0
    else:
        hi = core.conformal_q(e, alpha / 2)
        lo = -core.conformal_q([-x for x in e], alpha / 2)
        mid = core.quantile(e, 0.5)
        how = f"two-sided, n={n}"
    return {"low": sensed * (1 + lo), "mid": sensed * (1 + mid), "high": sensed * (1 + hi), "n": n, "how": how,
            "q_lo": lo, "q_hi": hi}


def conformal_frame(test: pd.DataFrame, allf: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in test.iterrows():
        cal = allf[allf.target <= r.target - 1]
        b = band_for(cal, r.sensed, bool(r.promo))
        rows.append({**r.to_dict(), "lo": b["low"], "mid": b["mid"], "hi": b["high"], "n_cal": b["n"]})
    return pd.DataFrame(rows)


def run() -> dict:
    allf = one_step()
    test = frame()
    cf = conformal_frame(test, allf)
    q = {0.1: cf.lo.clip(lower=0), 0.5: cf.mid, 0.9: cf.hi}
    sc_conf = score(test, q, "agent + conformal")
    base = [score(test, qq, name) for name, qq in forecasters(test).items() if name in ("consensus + normal", "agent + normal")]
    by_week = cf.assign(inside=(cf.actual >= cf.lo) & (cf.actual <= cf.hi)).groupby("target").inside.mean().round(3).to_dict()
    pongal = allf[(allf.series == "CHN MN-1L") & (allf.origin == 17)].iloc[0]
    cal = allf[allf.target <= 17]
    pb = band_for(cal, float(pongal.sensed), True)
    promo_scores = cal[cal.promo].assign(e=lambda x: x.actual / x.sensed - 1.0)[["series", "target", "actual", "sensed", "e"]]
    ord_b = band_for(cal, float(pongal.sensed), False)
    normal = (float(pongal.sensed) + core.N01.inv_cdf(0.1) * 450, float(pongal.sensed) + core.N01.inv_cdf(0.9) * 450)
    # the agent's own cleaning rule for promo history: a promo week that sold below the ordinary
    # level is a stockout or a data error, not a promo (foundry.agents.sensing.logic.promo_uplift)
    clean = cal[~(cal.promo & (cal.actual < cal.consensus))]
    cb = band_for(clean, float(pongal.sensed), True)
    return {"results": base + [sc_conf], "by_week": by_week, "pongal": {"sensed": float(pongal.sensed), "actual": float(pongal.actual),
            "band": pb, "clean_band": cb, "ordinary_band": ord_b, "normal_band": normal, "promo_scores": promo_scores.round(3).to_dict("records")},
            "cf": cf}


def main() -> None:
    r = run()
    print("80% intervals on the 320 one-step forecasts (targets 13-20):")
    print(f"  {'forecaster':22}{'q-loss %':>9}{'P10-P90 cover':>15}{'width %':>9}{'promo cover':>13}")
    for x in r["results"]:
        pc = f"{x['coverage_promo']:.2f} of {x['n_promo']}"
        print(f"  {x['name']:22}{x['qloss_pct']:9.2f}{x['coverage']:15.3f}{x['width_pct']:9.1f}{pc:>13}")
    print("  conformal coverage by target week:", r["by_week"])
    p = r["pongal"]
    print(f"Pongal call (CHN MN-1L, origin 17, week 18): sensed {p['sensed']:,.0f}, actual {p['actual']:,.0f}")
    for s in p["promo_scores"]:
        print(f"    promo score {s['series']} week {s['target']}: actual {s['actual']:,.0f} vs sensed {s['sensed']:,.0f}, e {s['e']:+.3f}")
    b = p["band"]
    print(f"  promo band ({b['how']}): {b['low']:,.0f} to {b['high']:,.0f} (+/-{b['q_hi']:.1%})")
    cb = p["clean_band"]
    print(f"  the agent's cleaning rule drops KOL week 9 (sold under the level): {cb['how']}: {cb['low']:,.0f} to {cb['high']:,.0f}")
    ob = p["ordinary_band"]
    print(f"  if it were calibrated like an ordinary week ({ob['how']}): {ob['low']:,.0f} to {ob['high']:,.0f}")
    nb = p["normal_band"]
    print(f"  normal with the planning sigma 450: {nb[0]:,.0f} to {nb[1]:,.0f}")
    c = r["results"][-1]
    cb, nb = r["pongal"]["clean_band"], r["pongal"]["normal_band"]
    print(f"Decision: ship the agent with conformal bands (cover {c['coverage']:.3f}, q-loss {c['qloss_pct']:.2f}%); "
          f"for Pongal")
    print(f"  give Priya {cb['low']:,.0f} to {cb['high']:,.0f}, not the normal's {nb[0]:,.0f} to {nb[1]:,.0f}, "
          f"and stock to Lab 4's quantile.")


if __name__ == "__main__":
    main()
