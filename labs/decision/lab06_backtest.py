"""Lab 6 · Backtests and forecast value added (FVA) on the two-year history.

Rolling origin, one step ahead, targets 65-104: 40 weeks x 40 series = 1,600 forecasts. The global model
is refitted every 8 weeks on every target before the block (an expanding window), the way a weekly job
would run it; between refits it forecasts with the latest lags.
The FVA ladder, each step one forecaster:
  naive            last week's actual
  seasonal naive   the same week last year
  consensus        the planning tool's flat number
  sensing agent    foundry.agents.sensing.logic.sense, horizon 1: the agent you built
  GBM              one global model, squared loss on actual / consensus, the promo calendar as a feature
  GBM hybrid       the GBM on the de-promoed ratio, times (1 + the agent's realised promo uplift)
  combo            the average of GBM hybrid and seasonal naive
FVA = WAPE(benchmark) - WAPE(forecaster), in points of WAPE: positive means the step added value.
Part c: the quantile GBM's 80% interval out of sample, raw and conformalised (CQR), targets 73-104.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from foundry.agents.sensing import logic as sensing
from labs.decision import core
from labs.decision.data import history2, sensed2, store_and_profile, two_year_store
from labs.decision.features import NAMES2, table2
from labs.decision.gbm import GBM

BASE2 = [n for n in NAMES2 if n != "promo_t"]
PRM = {"n_trees": 150, "lr": 0.05, "depth": 3, "min_leaf": 20, "subsample": 0.8, "seed": 0}
FIRST, LAST, BLOCK = 65, 104, 8
ORDER = ["naive", "seasonal naive", "consensus", "sensing agent", "GBM", "GBM hybrid", "combo"]


def _uplift(s2, p, row) -> float:
    if row.promo_t <= 0:
        return 0.0
    u = sensing.promo_uplift(s2, p, row.dc, row.sku, int(row.target) - 1, int(row.target))
    return float(u["uplift"]) if u["uplift"] is not None else float(row.promo_t)


def forecasts() -> pd.DataFrame:
    """Every forecaster's one-step number for every series and target 65-104, plus q10/q90.
    Computed once per process (Lab 7 scores against the same rows); a copy is returned."""
    return _forecasts().copy()


@lru_cache(maxsize=1)
def _forecasts() -> pd.DataFrame:
    t2 = table2()
    s2 = two_year_store()
    _, p = store_and_profile()
    h = history2().set_index(["series", "week"])
    out = []
    for b in range(FIRST, LAST + 1, BLOCK):
        train = t2[t2.target < b]
        test = t2[(t2.target >= b) & (t2.target < b + BLOCK)].copy()
        m = GBM("squared", **PRM).fit(train[NAMES2].to_numpy(), train.ratio.to_numpy())
        base = GBM("squared", **PRM).fit(train[BASE2].to_numpy(), train.base_ratio.to_numpy())
        q10 = GBM("quantile", tau=0.1, **PRM).fit(train[BASE2].to_numpy(), train.base_ratio.to_numpy())
        q90 = GBM("quantile", tau=0.9, **PRM).fit(train[BASE2].to_numpy(), train.base_ratio.to_numpy())
        lift = np.array([_uplift(s2, p, r) for r in test.itertuples()])
        c = test.consensus.to_numpy()
        test["GBM"] = m.predict(test[NAMES2].to_numpy()) * c
        test["GBM hybrid"] = base.predict(test[BASE2].to_numpy()) * c * (1 + lift)
        test["q10"] = q10.predict(test[BASE2].to_numpy()) * c * (1 + lift)
        test["q90"] = q90.predict(test[BASE2].to_numpy()) * c * (1 + lift)
        test["lift"] = lift
        test["block"] = b
        out.append(test)
    f = pd.concat(out).reset_index(drop=True)
    f["naive"] = [h.loc[(s, w - 1), "actual"] for s, w in zip(f.series, f.target)]
    f["seasonal naive"] = f.last_year
    # the agent's number for each target, origin target - 1 (data.sensed2 runs sense() per SKU, same answer)
    sn = sensed2(54, LAST, 3).set_index(["series", "target"]).h1
    f["sensing agent"] = [float(sn.loc[(s, int(w))]) for s, w in zip(f.series, f.target)]
    f["combo"] = (f["GBM hybrid"] + f["seasonal naive"]) / 2
    f["promo"] = f.promo_t > 0
    f["cls"] = [h.loc[(s, w), "cls"] for s, w in zip(f.series, f.target)]
    return f


def mase_scale() -> dict:
    """Per series: the in-sample MAE of the naive forecast over weeks 1-64 (what MASE divides by)."""
    h = history2()
    out = {}
    for key, g in h[h.week <= FIRST - 1].groupby("series"):
        a = g.sort_values("week").actual.to_numpy()
        out[key] = float(np.mean(np.abs(np.diff(a))))
    return out


def metrics(f: pd.DataFrame) -> dict:
    scale = mase_scale()
    sc = f.series.map(scale).to_numpy()
    y = f.actual.to_numpy()
    res = {}
    for name in ORDER:
        x = f[name].to_numpy()
        w = lambda mask: round(core.wape(list(y[mask]), list(x[mask])), 2)
        allm = np.ones(len(y), bool)
        res[name] = {"wape": w(allm), "ordinary": w(~f.promo.to_numpy()), "promo": w(f.promo.to_numpy()),
                     "bias": round(core.bias_pct(list(y), list(x)), 2), "mase": round(float(np.mean(np.abs(y - x) / sc)), 3)}
    for name in ORDER:
        res[name]["fva_naive"] = round(res["naive"]["wape"] - res[name]["wape"], 2)
        res[name]["fva_consensus"] = round(res["consensus"]["wape"] - res[name]["wape"], 2)
    return res


def by_series(f: pd.DataFrame, name: str, bench: str = "consensus") -> dict:
    """In how many of the 40 series does `name` beat `bench` on WAPE over the 40 weeks."""
    wins = 0
    for _, g in f.groupby("series"):
        y = list(g.actual)
        wins += core.wape(y, list(g[name])) < core.wape(y, list(g[bench]))
    return {"wins": int(wins), "of": int(f.series.nunique())}


def by_class(f: pd.DataFrame) -> list[dict]:
    rows = []
    for cls, g in f.groupby("cls"):
        y = list(g.actual)
        rows.append({"cls": cls, "n": len(g), "consensus": round(core.wape(y, list(g.consensus)), 2),
                     "seasonal naive": round(core.wape(y, list(g["seasonal naive"])), 2),
                     "GBM hybrid": round(core.wape(y, list(g["GBM hybrid"])), 2), "combo": round(core.wape(y, list(g.combo)), 2)})
    return rows


def intervals(f: pd.DataFrame, alpha: float = 0.2) -> dict:
    """Raw quantile-GBM 80% intervals against conformalised ones (CQR), scored on targets 73-104.
    Calibration for a block = every earlier block's out-of-sample scores, in consensus units:
    lower score (q10 - y) / c, upper score (y - q90) / c; one-sided conformal quantile at alpha / 2 each."""
    rows = []
    for b in sorted(f.block.unique()):
        if b == FIRST:
            continue
        cal = f[f.block < b]
        lo_s = list((cal.q10 - cal.actual) / cal.consensus)
        hi_s = list((cal.actual - cal.q90) / cal.consensus)
        qlo, qhi = core.conformal_q(lo_s, alpha / 2), core.conformal_q(hi_s, alpha / 2)
        g = f[f.block == b]
        for r in g.itertuples():
            rows.append({"block": b, "promo": r.promo, "y": r.actual, "q10": r.q10, "q90": r.q90,
                         "lo": max(0.0, r.q10 - qlo * r.consensus), "hi": r.q90 + qhi * r.consensus, "qlo": qlo, "qhi": qhi})
    d = pd.DataFrame(rows)
    mean_y = float(d.y.mean())
    raw_in = (d.y >= d.q10) & (d.y <= d.q90)
    cqr_in = (d.y >= d.lo) & (d.y <= d.hi)
    corr = d.groupby("block")[["qlo", "qhi"]].first().round(3).reset_index().to_dict("records")
    return {"n": len(d), "raw_cover": round(float(raw_in.mean()), 3), "cqr_cover": round(float(cqr_in.mean()), 3),
            "raw_width_pct": round(100 * float((d.q90 - d.q10).mean()) / mean_y, 1),
            "cqr_width_pct": round(100 * float((d.hi - d.lo).mean()) / mean_y, 1),
            "raw_cover_promo": round(float(raw_in[d.promo].mean()), 3), "cqr_cover_promo": round(float(cqr_in[d.promo].mean()), 3),
            "n_promo": int(d.promo.sum()), "corrections": corr,
            "winkler_raw": round(float(np.mean([core.winkler(a, b, c, alpha) for a, b, c in zip(d.y, d.q10, d.q90)])) / mean_y * 100, 1),
            "winkler_cqr": round(float(np.mean([core.winkler(a, b, c, alpha) for a, b, c in zip(d.y, d.lo, d.hi)])) / mean_y * 100, 1)}


def run() -> dict:
    f = forecasts()
    return {"n": len(f), "n_promo": int(f.promo.sum()), "metrics": metrics(f), "wins_hybrid": by_series(f, "GBM hybrid"),
            "wins_agent": by_series(f, "sensing agent"), "wins_combo": by_series(f, "combo"),
            "wins_combo_vs_snaive": by_series(f, "combo", "seasonal naive"), "by_class": by_class(f), "intervals": intervals(f),
            "diwali": f[f.target == 95][["series", "actual", "consensus", "seasonal naive", "GBM", "GBM hybrid", "lift"]].round(0).to_dict("records"),
            "chn": f[(f.series == "CHN MN-1L") & (f.target >= 97)][["target", "actual", "consensus", "naive", "seasonal naive", "sensing agent", "GBM hybrid", "combo"]].round(0).to_dict("records"),
            "frame": f}


def main() -> None:
    r = run()
    print(f"rolling origin, one step ahead: {r['n']} forecasts (40 series x targets {FIRST}-{LAST}), {r['n_promo']} promo rows; GBM refitted every {BLOCK} weeks")
    print(f"  {'forecaster':16}{'WAPE':>7}{'ordin.':>8}{'promo':>8}{'bias %':>8}{'MASE':>7}{'FVA v naive':>13}{'FVA v cons.':>13}")
    for name in ORDER:
        x = r["metrics"][name]
        print(f"  {name:16}{x['wape']:7.2f}{x['ordinary']:8.2f}{x['promo']:8.2f}{x['bias']:+8.2f}{x['mase']:7.3f}{x['fva_naive']:+13.2f}{x['fva_consensus']:+13.2f}")
    for k, label in (("wins_agent", "sensing agent"), ("wins_hybrid", "GBM hybrid"), ("wins_combo", "combo")):
        print(f"  {label} beats the consensus in {r[k]['wins']} of {r[k]['of']} series")
    print(f"  combo beats the seasonal naive in {r['wins_combo_vs_snaive']['wins']} of 40 series")
    print("  WAPE by class:")
    for x in r["by_class"]:
        print(f"    class {x['cls']} ({x['n']} rows): consensus {x['consensus']:.2f}  seasonal naive {x['seasonal naive']:.2f}  "
              f"GBM hybrid {x['GBM hybrid']:.2f}  combo {x['combo']:.2f}")
    print("  Diwali week 95 (GC-200 rows):")
    for x in r["diwali"]:
        if "GC-200" in x["series"]:
            print(f"    {x['series']}: actual {x['actual']:,.0f} consensus {x['consensus']:,.0f} last year {x['seasonal naive']:,.0f} "
                  f"GBM {x['GBM']:,.0f} hybrid {x['GBM hybrid']:,.0f} (agent uplift {x['lift']:+.0%})")
    iv = r["intervals"]
    print(f"c) 80% intervals, targets 73-104 ({iv['n']} forecasts, {iv['n_promo']} promo):")
    print(f"  quantile GBM raw: cover {iv['raw_cover']:.3f}, width {iv['raw_width_pct']}% of mean demand, Winkler {iv['winkler_raw']}%; promo cover {iv['raw_cover_promo']:.3f}")
    print(f"  conformalised:    cover {iv['cqr_cover']:.3f}, width {iv['cqr_width_pct']}% of mean demand, Winkler {iv['winkler_cqr']}%; promo cover {iv['cqr_cover_promo']:.3f}")
    print("  corrections by block (consensus units, low side / high side):", [(c["block"], c["qlo"], c["qhi"]) for c in iv["corrections"]])
    print("  CHN MN-1L, targets 97-104:")
    for x in r["chn"]:
        print(f"    week {x['target']:.0f}: actual {x['actual']:,.0f} cons {x['consensus']:,.0f} naive {x['naive']:,.0f} last yr {x['seasonal naive']:,.0f} "
              f"agent {x['sensing agent']:,.0f} hybrid {x['GBM hybrid']:,.0f} combo {x['combo']:,.0f}")
    h = r["metrics"]["GBM hybrid"]
    iv = r["intervals"]
    print(f"Decision: ship GBM hybrid (WAPE {h['wape']:.2f}, FVA {h['fva_consensus']:+.2f} on the consensus, "
          f"better in")
    print(f"  {r['wins_hybrid']['wins']} of 40 series) with conformal intervals (cover {iv['cqr_cover']:.3f}); "
          f"keep the seasonal naive")
    print("  as the bar for promo weeks.")

if __name__ == "__main__":
    main()
