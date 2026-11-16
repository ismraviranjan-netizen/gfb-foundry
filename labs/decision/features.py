"""One table for one global model: a row per (series, target week), features from the weeks
before the target only, every number scaled by the series' consensus so a 57-case thandai and a
4,200-case nectar can share trees.

  r1..r4     actual / consensus, one to four weeks back
  base4      mean of the de-promoed ratio over the last four weeks (actual / consensus / (1 + uplift))
  base8      the same over eight weeks
  slope8     the slope of the de-promoed ratio over eight weeks, per week (a trend)
  promo_t    the calendar's uplift for the target week (known in advance: a future covariate)
  promo_l1   the calendar's uplift last week (a promo week inflates r1)
  cv         planning sigma / planning mean (how noisy the series is)
  is_a       1 for class A SKUs
Targets: ratio = actual / consensus; base_ratio = ratio / (1 + promo_t).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from labs.decision.data import history

NAMES = ["r1", "r2", "r3", "r4", "base4", "base8", "slope8", "promo_t", "promo_l1", "cv", "is_a"]
FIRST_TARGET = 9


def table() -> pd.DataFrame:
    h = history()
    rows = []
    for key, g in h.groupby("series"):
        g = g.sort_values("week")
        r = (g.actual / g.consensus).to_numpy()
        u = g.uplift.to_numpy()
        b = r / (1.0 + u)
        weeks = g.week.to_numpy()
        cv = float(g.sigma.iloc[0] / g.mu.iloc[0])
        is_a = 1.0 if g.cls.iloc[0] == "A" else 0.0
        for i in range(FIRST_TARGET - 1, len(g)):
            w8 = b[i - 8:i]
            slope = float(np.polyfit(np.arange(8), w8, 1)[0])
            rows.append({"series": key, "dc": g.dc.iloc[0], "sku": g.sku.iloc[0], "target": int(weeks[i]),
                         "consensus": float(g.consensus.iloc[i]), "actual": float(g.actual.iloc[i]),
                         "r1": r[i - 1], "r2": r[i - 2], "r3": r[i - 3], "r4": r[i - 4],
                         "base4": float(b[i - 4:i].mean()), "base8": float(w8.mean()), "slope8": slope,
                         "promo_t": float(u[i]), "promo_l1": float(u[i - 1]), "cv": cv, "is_a": is_a,
                         "ratio": r[i], "base_ratio": b[i]})
    return pd.DataFrame(rows)


# --- the two-year table (Part II proper) -------------------------------------------------------
NAMES2 = ["r1", "r2", "base4", "base8", "slope8", "s52", "woy_sin", "woy_cos", "promo_t", "promo_l1",
          "cv", "is_a", "dc_BHW", "dc_GGN", "dc_KOL", "dc_CHN"]
FIRST_TARGET2 = 54          # a year of history plus a week, so last year's same week exists


def table2() -> pd.DataFrame:
    """As table(), on two years, plus: s52 = the de-promoed ratio a year ago (mean of weeks t-53..t-51),
    week-of-year as a point on a circle, and which DC (a promo realises differently per DC)."""
    from labs.decision.data import history2
    h = history2()
    rows = []
    for key, g in h.groupby("series"):
        g = g.sort_values("week")
        r = (g.actual / g.consensus).to_numpy()
        u = g.uplift.to_numpy()
        b = r / (1.0 + u)
        weeks = g.week.to_numpy()
        cv = float(g.sigma.iloc[0] / g.mu.iloc[0])
        is_a = 1.0 if g.cls.iloc[0] == "A" else 0.0
        dc = g.dc.iloc[0]
        for i in range(FIRST_TARGET2 - 1, len(g)):
            w8 = b[i - 8:i]
            woy = (int(weeks[i]) - 1) % 52
            rows.append({"series": key, "dc": dc, "sku": g.sku.iloc[0], "target": int(weeks[i]),
                         "consensus": float(g.consensus.iloc[i]), "actual": float(g.actual.iloc[i]),
                         "r1": r[i - 1], "r2": r[i - 2], "base4": float(b[i - 4:i].mean()), "base8": float(w8.mean()),
                         "slope8": float(np.polyfit(np.arange(8), w8, 1)[0]), "s52": float(b[i - 53:i - 50].mean()),
                         "woy_sin": float(np.sin(2 * np.pi * woy / 52)), "woy_cos": float(np.cos(2 * np.pi * woy / 52)),
                         "promo_t": float(u[i]), "promo_l1": float(u[i - 1]), "cv": cv, "is_a": is_a,
                         "dc_BHW": float(dc == "BHW"), "dc_GGN": float(dc == "GGN"), "dc_KOL": float(dc == "KOL"), "dc_CHN": float(dc == "CHN"),
                         "ratio": r[i], "base_ratio": b[i], "last_year": float(g.actual.iloc[i - 52])})
    return pd.DataFrame(rows)
