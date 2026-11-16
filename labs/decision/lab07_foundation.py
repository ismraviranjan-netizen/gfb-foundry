"""Lab 7 · A foundation-model adapter: GFB's history in the shape a Chronos-2 endpoint expects, scored by
the same harness as Lab 6, with no network call.

A live call needs a SageMaker endpoint on a GPU instance (or AutoGluon-Cloud); neither runs inside this
repo, so the book marks the live numbers NOT VERIFIED. What runs here, end to end:
  1 payload(): for one origin, every series as {"target", "item_id", "start", "past_covariates",
    "future_covariates"} with the promo calendar as a known-future covariate, and "parameters" with
    prediction_length, quantile_levels and freq (the JumpStart notebook's documented request shape)
  2 parse(): the documented response, {"predictions": [{"item_id", "mean", "0.1", "0.5", "0.9"}]},
    back into rows the backtest can score
  3 StandIn: a local model that answers in exactly that JSON (seasonal naive with an empirical spread),
    so payload -> JSON text -> parse -> score is tested without an endpoint
  4 the scorecard: WAPE of the median, pinball loss, coverage and width against Lab 6 on the same rows
Set CHRONOS_ENDPOINT (and have boto3 and AWS credentials) to send the same payload to a real endpoint.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import numpy as np
import pandas as pd

from labs.decision import core
from labs.decision.data import history2

QUANTILES = [0.1, 0.5, 0.9]
WEEK1 = dt.date(2025, 1, 6)          # the Monday of the first week of January 2025 (data.py: week 1)


def payload(h: pd.DataFrame, origin: int, horizon: int = 1, context: int = 104, covariates: bool = True) -> dict:
    """The request for one forecast origin: context = the last `context` weeks up to the origin."""
    inputs = []
    for key, g in h.groupby("series"):
        g = g.sort_values("week")
        past = g[(g.week <= origin) & (g.week > origin - context)]
        fut = g[(g.week > origin) & (g.week <= origin + horizon)]
        item = {"target": [float(v) for v in past.actual], "item_id": key,
                "start": (WEEK1 + dt.timedelta(weeks=int(past.week.iloc[0]) - 1)).isoformat() + "T00:00:00"}
        if covariates:
            item["past_covariates"] = {"promo_uplift": [float(v) for v in past.uplift]}
            item["future_covariates"] = {"promo_uplift": [float(v) for v in fut.uplift]}
        inputs.append(item)
    return {"inputs": inputs, "parameters": {"prediction_length": horizon, "quantile_levels": QUANTILES, "freq": "W-MON"}}


class StandIn:
    """Answers a Chronos-style request locally: the median is the same week last year, rescaled by
    how this year has run against last year over the last 8 weeks; the spread is the empirical
    quantiles of that ratio over the last 26 weeks. A future promo the calendar knows about and last
    year's same week did not have lifts the forecast by the calendar's number."""

    name = "stand-in (seasonal, empirical spread)"

    def invoke(self, body: str) -> str:
        req = json.loads(body)
        H = int(req["parameters"]["prediction_length"])
        qs = req["parameters"]["quantile_levels"]
        out = []
        for it in req["inputs"]:
            y = np.asarray(it["target"], float)
            T = len(y)
            fut = it.get("future_covariates", {}).get("promo_uplift", [0.0] * H)
            pastp = it.get("past_covariates", {}).get("promo_uplift", [0.0] * T)
            pred = {"item_id": it["item_id"], "mean": [], **{str(q): [] for q in qs}}
            ratios = y[T - 26:] / np.maximum(y[T - 26 - 52:T - 52], 1.0) if T >= 78 else np.array([1.0])
            level = float(np.mean(y[T - 8:]) / max(np.mean(y[T - 60:T - 52]), 1.0)) if T >= 60 else 1.0
            for k in range(H):
                base = y[T - 52 + k] * level if T >= 52 else float(np.mean(y[-8:]))
                if T >= 52 and fut[k] > 0 and pastp[T - 52 + k] == 0:
                    base *= 1.0 + fut[k]
                spread = ratios / np.median(ratios)
                for q in qs:
                    pred[str(q)].append(round(float(base * np.quantile(spread, q)), 1))
                pred["mean"].append(round(float(base * np.mean(spread)), 1))
            out.append(pred)
        return json.dumps({"predictions": out})


class Endpoint:
    """The live path (not run in this repo): the same body to a SageMaker real-time endpoint."""

    def __init__(self, name: str):
        import boto3                      # only when CHRONOS_ENDPOINT is set
        self.name, self.rt = name, boto3.client("sagemaker-runtime")

    def invoke(self, body: str) -> str:
        r = self.rt.invoke_endpoint(EndpointName=self.name, ContentType="application/json", Body=body)
        return r["Body"].read().decode()


def parse(text: str, origin: int) -> list[dict]:
    rows = []
    for p in json.loads(text)["predictions"]:
        for k, med in enumerate(p["0.5"]):
            rows.append({"series": p["item_id"], "target": origin + 1 + k, "fm_q10": p["0.1"][k], "fm_q50": med,
                         "fm_q90": p["0.9"][k], "fm_mean": p["mean"][k]})
    return rows


def model():
    name = os.environ.get("CHRONOS_ENDPOINT")
    return (Endpoint(name), f"endpoint {name}") if name else (StandIn(), "stand-in: no endpoint configured, live call not run")


def scorecard(d: pd.DataFrame, cols: dict) -> list[dict]:
    """cols: name -> (q10, q50, q90) column names. Pinball over the three taus, as % of mean demand."""
    y = d.actual.to_numpy()
    out = []
    for name, (lo, mid, hi) in cols.items():
        q = {0.1: d[lo].to_numpy(), 0.5: d[mid].to_numpy(), 0.9: d[hi].to_numpy()}
        loss = np.mean([np.mean([core.pinball(a, b, t) for a, b in zip(y, q[t])]) for t in QUANTILES])
        out.append({"name": name, "wape": round(core.wape(list(y), list(q[0.5])), 2), "qloss_pct": round(100 * loss / y.mean(), 2),
                    "cover": round(float(np.mean((y >= q[0.1]) & (y <= q[0.9]))), 3),
                    "width_pct": round(100 * float(np.mean(q[0.9] - q[0.1])) / y.mean(), 1)})
    return out


def run(first_target: int = 73, last_target: int = 104) -> dict:
    h = history2()
    m, how = model()
    body0 = json.dumps(payload(h, first_target - 1))
    rows = []
    for o in range(first_target - 1, last_target):
        rows += parse(m.invoke(json.dumps(payload(h, o))), o)
    fm = pd.DataFrame(rows)
    from labs.decision.lab06_backtest import forecasts, intervals  # the same rows, the same harness
    f = forecasts()
    iv = intervals(f)
    # rebuild the CQR band per row for the comparison (the corrections lab 6 printed, per block)
    corr = {c["block"]: (c["qlo"], c["qhi"]) for c in iv["corrections"]}
    f = f[f.target >= first_target].copy()
    f["cqr_lo"] = [max(0.0, a - corr[b][0] * c) for a, b, c in zip(f.q10, f.block, f.consensus)]
    f["cqr_hi"] = [a + corr[b][1] * c for a, b, c in zip(f.q90, f.block, f.consensus)]
    d = f.merge(fm, on=["series", "target"])
    d["cons_lo"] = d.consensus + core.N01.inv_cdf(0.1) * d.series.map(h.groupby("series").sigma.first())
    d["cons_hi"] = d.consensus + core.N01.inv_cdf(0.9) * d.series.map(h.groupby("series").sigma.first())
    cols = {"consensus + normal": ("cons_lo", "consensus", "cons_hi"), m.name if hasattr(m, "name") else "endpoint": ("fm_q10", "fm_q50", "fm_q90"),
            "GBM hybrid + CQR (lab 6)": ("cqr_lo", "GBM hybrid", "cqr_hi")}
    sc = scorecard(d, cols)
    p0 = json.loads(body0)
    chn = next(i for i in p0["inputs"] if i["item_id"] == "CHN MN-1L")
    return {"how": how, "n": len(d), "bytes": len(body0), "n_series": len(p0["inputs"]), "context": len(chn["target"]),
            "params": p0["parameters"], "chn_head": chn["target"][:4], "chn_start": chn["start"],
            "chn_future": chn["future_covariates"], "scorecard": sc, "first": first_target, "last": last_target,
            "promo_rows": int((d.promo_t > 0).sum())}


def main() -> None:
    r = run()
    print(f"model: {r['how']}")
    print(f"one request (origin {r['first'] - 1}): {r['n_series']} series, {r['context']} weeks of context each, "
          f"{r['bytes']:,} bytes of JSON; parameters {r['params']}")
    print(f"  CHN MN-1L: start {r['chn_start']}, first values {r['chn_head']}, future covariates {r['chn_future']}")
    print(f"scored on targets {r['first']}-{r['last']}: {r['n']} one-step forecasts ({r['promo_rows']} promo rows)")
    print(f"  {'forecaster':40}{'WAPE':>7}{'q-loss %':>10}{'P10-P90 cover':>15}{'width %':>9}")
    for x in r["scorecard"]:
        print(f"  {x['name']:40}{x['wape']:7.2f}{x['qloss_pct']:10.2f}{x['cover']:15.3f}{x['width_pct']:9.1f}")
    bar = r["scorecard"][-1]
    cand = r["scorecard"][1]
    print(f"Decision: a foundation model replaces the GBM hybrid only if it beats WAPE {bar['wape']:.2f}")
    print(f"  and cover {bar['cover']:.3f} on these rows; the {cand['name'].split(' (')[0]} scores "
          f"{cand['wape']:.2f}. A live endpoint's")
    print("  numbers are NOT VERIFIED here.")

if __name__ == "__main__":
    main()
