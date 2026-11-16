"""Lab 2 · Score a range: pinball loss, coverage, width and calibration.

Every series, origins 12-19 (targets 13-20): 320 one-step forecasts. Four forecasters give a P10, a
P50 and a P90 for each:
  consensus + normal   the ERP habit: the flat consensus plus or minus z * planning sigma
  agent + normal       the sensing agent's number plus or minus z * planning sigma
  agent, too sure      the agent's number, half the sigma (an interval that looks sharp)
  agent, too wide      the agent's number, twice the sigma (an interval that is never wrong)
Scores: quantile loss (mean pinball over the three taus, as % of mean demand), the share of actuals
inside P10-P90 (target 80%), the mean width (% of mean demand), and the share below each quantile.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from labs.decision import core
from labs.decision.data import one_step

TAUS = (0.1, 0.5, 0.9)
Z = {t: core.N01.inv_cdf(t) for t in TAUS}


def frame(first_origin: int = 12, last_origin: int = 19) -> pd.DataFrame:
    d = one_step()
    return d[(d.origin >= first_origin) & (d.origin <= last_origin)].reset_index(drop=True)


def normal_band(point: pd.Series, sigma: pd.Series, k: float = 1.0) -> dict:
    return {t: point + Z[t] * k * sigma for t in TAUS}


def score(d: pd.DataFrame, q: dict, name: str) -> dict:
    y = d.actual.to_numpy()
    mean_y = y.mean()
    loss = np.mean([np.mean([core.pinball(a, b, t) for a, b in zip(y, q[t].to_numpy())]) for t in TAUS])
    lo, hi = q[0.1].to_numpy(), q[0.9].to_numpy()
    cov = float(np.mean((y >= lo) & (y <= hi)))
    width = float(np.mean(hi - lo))
    below = {t: float(np.mean(y < q[t].to_numpy())) for t in TAUS}
    promo = d.promo.to_numpy()
    cov_promo = float(np.mean(((y >= lo) & (y <= hi))[promo])) if promo.any() else None
    return {"name": name, "n": len(y), "qloss_pct": round(100 * loss / mean_y, 2), "coverage": round(cov, 3),
            "width_pct": round(100 * width / mean_y, 1), "below": {t: round(v, 3) for t, v in below.items()},
            "coverage_promo": cov_promo, "n_promo": int(promo.sum())}


def forecasters(d: pd.DataFrame) -> dict:
    return {
        "consensus + normal": normal_band(d.consensus, d.sigma),
        "agent + normal": normal_band(d.sensed, d.sigma),
        "agent, too sure": normal_band(d.sensed, d.sigma, 0.5),
        "agent, too wide": normal_band(d.sensed, d.sigma, 2.0),
    }


def reliability(d: pd.DataFrame, point: pd.Series, scale: pd.Series, taus=np.linspace(0.05, 0.95, 19)) -> list[tuple]:
    """Nominal tau against the observed share of actuals below the tau-quantile."""
    y = d.actual.to_numpy()
    out = []
    for t in taus:
        qt = point.to_numpy() + core.N01.inv_cdf(float(t)) * scale.to_numpy()
        out.append((round(float(t), 2), round(float(np.mean(y < qt)), 3)))
    return out


def run() -> dict:
    d = frame()
    res = [score(d, q, name) for name, q in forecasters(d).items()]
    rel = {"consensus + normal": reliability(d, d.consensus, d.sigma), "agent + normal": reliability(d, d.sensed, d.sigma),
           "agent, too sure": reliability(d, d.sensed, 0.5 * d.sigma)}
    return {"n": len(d), "results": res, "reliability": rel, "mean_actual": float(d.actual.mean())}


def main() -> None:
    r = run()
    print(f"{r['n']} one-step forecasts (40 series x origins 12-19); mean actual {r['mean_actual']:,.0f} cases")
    print(f"  {'forecaster':22}{'q-loss %':>9}{'P10-P90 cover':>15}{'width %':>9}{'<P10':>7}{'<P50':>7}{'<P90':>7}{'promo cover':>13}")
    for x in r["results"]:
        b = x["below"]
        pc = f"{x['coverage_promo']:.2f} of {x['n_promo']}" if x["coverage_promo"] is not None else "-"
        print(f"  {x['name']:22}{x['qloss_pct']:9.2f}{x['coverage']:15.3f}{x['width_pct']:9.1f}{b[0.1]:7.3f}{b[0.5]:7.3f}{b[0.9]:7.3f}{pc:>13}")
    best = min(r["results"], key=lambda x: x["qloss_pct"])
    print(f"Decision: prefer '{best['name']}' (q-loss {best['qloss_pct']:.2f}%), but its P10-P90 covers "
          f"{best['coverage']:.3f}, not 0.80,")
    print(f"  and {best['coverage_promo']:.2f} of promo weeks: calibrate the band before anyone stocks to it (Lab 3).")


if __name__ == "__main__":
    main()
