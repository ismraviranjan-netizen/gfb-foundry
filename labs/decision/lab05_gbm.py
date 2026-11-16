"""Lab 5 · One global gradient-boosted model for forty series.

Part a, the twenty-week history: train on targets 9-16 (320 rows), test one step ahead on targets
17-20 (160 rows). Part b, the two-year history: train on targets 54-88 (1,400 rows), test one step
ahead on weeks 89-104 (640 rows), the second Diwali included. Models on the consensus-scaled tables:
  GBM raw       squared loss on actual / consensus, with the promo calendar as a feature
  GBM hybrid    squared loss on the de-promoed ratio (no promo feature); on a promo week the
                forecast is multiplied by (1 + the sensing agent's calibrated uplift)
  GBM q10/q90   quantile loss on the de-promoed ratio: an 80% interval, promo rule on top
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from foundry.agents.sensing import logic as sensing
from labs.decision import core
from labs.decision.data import store_and_profile
from labs.decision.features import NAMES, NAMES2, table, table2
from labs.decision.gbm import GBM

BASE_FEATURES = [n for n in NAMES if n != "promo_t"]
PARAMS = {"n_trees": 200, "lr": 0.05, "depth": 3, "min_leaf": 10, "subsample": 0.8, "seed": 0}


def agent_uplift(row) -> float:
    """The sensing agent's promo rule: the calendar's uplift times what promos here have realised."""
    if row["promo_t"] <= 0:
        return 0.0
    s, p = store_and_profile()
    pu = sensing.promo_uplift(s, p, row["dc"], row["sku"], int(row["target"]) - 1, int(row["target"]))
    return float(pu["uplift"]) if pu["uplift"] is not None else float(row["promo_t"])


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    prm = {**PARAMS, **(params or {})}
    out = test.copy()
    raw = GBM("squared", **prm).fit(train[NAMES].to_numpy(), train.ratio.to_numpy())
    out["gbm_raw"] = raw.predict(test[NAMES].to_numpy()) * test.consensus
    base = GBM("squared", **prm).fit(train[BASE_FEATURES].to_numpy(), train.base_ratio.to_numpy())
    lift = test.apply(agent_uplift, axis=1)
    out["agent_uplift"] = lift
    out["gbm_hybrid"] = base.predict(test[BASE_FEATURES].to_numpy()) * test.consensus * (1.0 + lift)
    for tau in (0.1, 0.5, 0.9):
        m = GBM("quantile", tau=tau, **prm).fit(train[BASE_FEATURES].to_numpy(), train.base_ratio.to_numpy())
        out[f"gbm_q{int(tau * 100)}"] = m.predict(test[BASE_FEATURES].to_numpy()) * test.consensus * (1.0 + lift)
    out.attrs["importance"] = base.importance(BASE_FEATURES)
    out.attrs["importance_raw"] = raw.importance(NAMES)
    return out


def run(train_upto: int = 16) -> dict:
    t = table()
    train, test = t[t.target <= train_upto], t[t.target > train_upto]
    # the boosting curve: squared loss on the de-promoed ratio, train and test, tree by tree
    curve_m = GBM("squared", **{**PARAMS, "n_trees": 400}).fit(train[BASE_FEATURES].to_numpy(), train.base_ratio.to_numpy(),
                                                             test[BASE_FEATURES].to_numpy(), test.base_ratio.to_numpy())
    out = fit_predict(train, test)
    y = out.actual.tolist()
    res = {}
    for col in ("consensus", "gbm_raw", "gbm_hybrid"):
        res[col] = {"wape": round(core.wape(y, out[col].tolist()), 2), "bias": round(core.bias_pct(y, out[col].tolist()), 2)}
    promo = out[out.promo_t > 0]
    cover = float(np.mean((out.actual >= out.gbm_q10) & (out.actual <= out.gbm_q90)))
    chn = out[out.series == "CHN MN-1L"][["target", "actual", "consensus", "gbm_raw", "gbm_hybrid", "gbm_q10", "gbm_q90", "agent_uplift"]]
    return {"n_train": len(train), "n_test": len(test), "metrics": res, "promo_rows": promo[["series", "target", "actual", "consensus", "gbm_raw", "gbm_hybrid", "agent_uplift"]].round(1).to_dict("records"),
            "coverage_80": round(cover, 3), "width_pct": round(100 * float(np.mean(out.gbm_q90 - out.gbm_q10)) / float(out.actual.mean()), 1),
            "importance": out.attrs["importance"], "importance_raw": out.attrs["importance_raw"], "chn": chn.round(1).to_dict("records"),
            "curve_train": curve_m.history, "curve_test": curve_m.val_history,
            "best_trees": int(np.argmin(curve_m.val_history)) + 1}


def run_long(train_upto: int = 88) -> dict:
    """The two-year table: one fit at week 88, one-step forecasts for weeks 89-104 with the latest lags."""
    t = table2()
    train, test = t[t.target <= train_upto], t[t.target > train_upto]
    prm = {**PARAMS, "n_trees": 300, "min_leaf": 20}
    m = GBM("squared", **prm).fit(train[NAMES2].to_numpy(), train.ratio.to_numpy(), test[NAMES2].to_numpy(), test.ratio.to_numpy())
    pred = m.predict(test[NAMES2].to_numpy()) * test.consensus.to_numpy()
    y = test.actual.tolist()
    promo = (test.promo_t > 0).to_numpy()
    def w(f, mask=None):
        mask = np.ones(len(y), bool) if mask is None else mask
        return round(core.wape([a for a, k in zip(y, mask) if k], [b for b, k in zip(f, mask) if k]), 2)
    res = {"gbm": {"all": w(pred), "promo": w(pred, promo), "ordinary": w(pred, ~promo)},
           "consensus": {"all": w(test.consensus.tolist()), "promo": w(test.consensus.tolist(), promo), "ordinary": w(test.consensus.tolist(), ~promo)},
           "seasonal naive": {"all": w(test.last_year.tolist()), "promo": w(test.last_year.tolist(), promo), "ordinary": w(test.last_year.tolist(), ~promo)}}
    ex = test.assign(gbm=pred)
    chn = ex[ex.series == "CHN MN-1L"][["target", "actual", "consensus", "last_year", "gbm"]].round(0)
    diwali = ex[(ex.target == 95)][["series", "actual", "consensus", "gbm", "promo_t"]].round(2)
    return {"n_train": len(train), "n_test": len(test), "n_promo": int(promo.sum()), "metrics": res, "importance": m.importance(NAMES2),
            "curve_train": m.history, "curve_test": m.val_history, "best_trees": int(np.argmin(m.val_history)) + 1,
            "chn": chn.to_dict("records"), "diwali": diwali.to_dict("records")}


def main() -> None:
    r = run()
    print("a) twenty weeks:")
    print(f"train {r['n_train']} rows (targets 9-16), test {r['n_test']} rows (targets 17-20), one step ahead")
    for k, v in r["metrics"].items():
        print(f"  {k:12} WAPE {v['wape']:6.2f}%  bias {v['bias']:+6.2f}%")
    print("  promo weeks in the test:")
    for x in r["promo_rows"]:
        print(f"    {x['series']} week {x['target']}: actual {x['actual']:,.0f}, consensus {x['consensus']:,.0f}, "
              f"GBM raw {x['gbm_raw']:,.0f}, GBM hybrid {x['gbm_hybrid']:,.0f} (agent uplift {x['agent_uplift']:+.1%})")
    print(f"  GBM q10-q90 coverage {r['coverage_80']:.3f} (target 0.80), width {r['width_pct']}% of mean demand")
    print("  feature importance (hybrid base model):", {k: v for k, v in sorted(r["importance"].items(), key=lambda kv: -kv[1])})
    print(f"  boosting curve: test loss lowest at {r['best_trees']} trees; train {r['curve_train'][-1]:.4f} vs test {r['curve_test'][-1]:.4f} at 400")
    for x in r["chn"]:
        print(f"    CHN MN-1L week {x['target']}: actual {x['actual']:,.0f}  hybrid {x['gbm_hybrid']:,.0f}  [{x['gbm_q10']:,.0f}, {x['gbm_q90']:,.0f}]")
    L = run_long()
    print(f"b) two years: train {L['n_train']} rows (targets 54-88), test {L['n_test']} rows (weeks 89-104, {L['n_promo']} promo rows)")
    for k, v in L["metrics"].items():
        print(f"  {k:15} WAPE all {v['all']:6.2f}%  ordinary {v['ordinary']:6.2f}%  promo {v['promo']:6.2f}%")
    print(f"  boosting curve: test loss lowest at {L['best_trees']} trees (of 300)")
    print("  feature importance:", {k: v for k, v in sorted(L["importance"].items(), key=lambda kv: -kv[1])[:8]})
    for x in L["chn"][:6]:
        print(f"    CHN MN-1L week {x['target']:.0f}: actual {x['actual']:,.0f} consensus {x['consensus']:,.0f} last year {x['last_year']:,.0f} GBM {x['gbm']:,.0f}")
    m, lm = r["metrics"], L["metrics"]
    print(f"Decision: on twenty weeks keep the consensus (GBM hybrid WAPE {m['gbm_hybrid']['wape']:.2f} vs "
          f"{m['consensus']['wape']:.2f}).")
    print(f"  On two years the GBM wins ordinary weeks ({lm['gbm']['ordinary']:.2f} vs "
          f"{lm['consensus']['ordinary']:.2f}) but not promo weeks")
    print(f"  ({lm['gbm']['promo']:.2f} vs the seasonal naive's {lm['seasonal naive']['promo']:.2f}): "
          f"keep a promo rule on top.")

if __name__ == "__main__":
    main()
