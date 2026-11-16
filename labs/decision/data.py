"""The GFB tables as pandas frames, and the sensing agent's one-step numbers for every series.

history()      one row per (week, dc, sku): consensus, actual, the calendar's promo uplift, the
               planning master's weekly mean and sigma, the class and the margin per case
one_step()     for every series and every origin week o: the agent's number for week o + 1
               (foundry.agents.sensing.logic.sense, horizon 1), the consensus and what happened
The sensing history is twenty weeks, seed 11, no incidents (Part 3 of The Agent Foundry).
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

from foundry.agents.sensing import logic as sensing
from foundry.data.generate import sensing_history
from foundry.profile import load_profile


@lru_cache(maxsize=1)
def store_and_profile():
    return sensing_history(), load_profile("gfb")


def history() -> pd.DataFrame:
    s, _ = store_and_profile()
    net = s.net
    promo = {(r["week"], r["dc"], r["sku"]): float(r["uplift_pct"]) for r in s.promos}
    rows = []
    for r in s.demand:
        dc, sku, w = r["dc"], r["sku"], r["week"]
        rows.append({"week": w, "dc": dc, "sku": sku, "cls": net.sku_class(sku), "consensus": float(r["forecast"]),
                     "actual": float(r["actual"]), "uplift": promo.get((w, dc, sku), 0.0),
                     "mu": net.weekly_mean(sku, dc), "sigma": net.weekly_sigma(sku, dc),
                     "margin": net.contribution_per_case(sku)})
    df = pd.DataFrame(rows).sort_values(["dc", "sku", "week"]).reset_index(drop=True)
    df["series"] = df["dc"] + " " + df["sku"]
    return df


@lru_cache(maxsize=4)
def _one_step(first_origin: int, last_origin: int) -> pd.DataFrame:
    s, p = store_and_profile()
    h = history().set_index(["series", "week"])
    rows = []
    for sku in s.net.parts:
        for dc in s.net.dcs:
            key = f"{dc} {sku}"
            for o in range(first_origin, last_origin + 1):
                r = sensing.sense(s, p, dc, sku, o, horizon=1)
                f = r.forecasts[0]
                t = o + 1
                row = h.loc[(key, t)]
                rows.append({"series": key, "dc": dc, "sku": sku, "cls": row["cls"], "origin": o, "target": t,
                             "actual": row["actual"], "consensus": row["consensus"], "sensed": float(f["sensed"]),
                             "decision": r.decision, "promo": bool(f["promo"]), "uplift": row["uplift"],
                             "sigma": row["sigma"], "margin": row["margin"]})
    return pd.DataFrame(rows)


def one_step(first_origin: int = 8, last_origin: int = 19) -> pd.DataFrame:
    """The agent's one-step-ahead number for every series, origins first_origin..last_origin."""
    return _one_step(first_origin, last_origin).copy()


# --- the two-year history for Part II ----------------------------------------------------------
# Twenty weeks cannot teach a global model anything the consensus does not already know (Lab 5a
# shows it). Part II needs what a real juice-and-snacks company has: two summers, two Diwalis, forty
# promos and a planning calendar whose uplift numbers are not quite right. Same generator, same
# network, different signals; week 1 is the first week of January 2025.
REALISATION = {"BHW": 1.15, "GGN": 1.0, "KOL": 0.85, "CHN": 0.7}   # what each DC's promos deliver, per calendar point


def two_year_signals():
    from foundry.data.generate import Signals
    season = {}
    for dc in ("BHW", "GGN", "KOL", "CHN"):
        season[(dc, "MN-1L")] = (0.22, 19, 52)      # mango: peaks in May
        season[(dc, "MN-200")] = (0.25, 19, 52)
        season[(dc, "LJ-1L")] = (0.35, 21, 52)      # lime cooler: the hottest weeks
        season[(dc, "AJ-1L")] = (0.10, 2, 52)       # apple juice: a little winter lift
        season[(dc, "TG-250")] = (0.30, 11, 52)     # thandai: Holi and the spring
    trend = {("CHN", "MN-1L"): 0.004, ("GGN", "MN-200"): 0.003, ("KOL", "AJ-1L"): -0.002, ("BHW", "GC-200"): 0.002}
    return Signals(trend=trend, season=season)


def two_year_calendar():
    """(week, dc, sku, name, true uplift). The calendar the planners see says true / realisation."""
    ev = []
    for y, base in ((0, 0), (1, 52)):
        for dc in ("GGN", "BHW"):
            ev += [(base + 10, dc, "TG-250", "Holi thandai", 0.80), (base + 10, dc, "GC-200", "Holi chakli", 0.40)]
        for dc in ("BHW", "CHN", "GGN"):
            ev += [(base + 17, dc, "MN-1L", "Summer 1+1", 0.45), (base + 18, dc, "MN-200", "Summer 1+1 small", 0.40)]
        ev += [(base + 2, "CHN", "MN-1L", "Pongal pack", 0.30)]
        ev += [(base + 41, "KOL", "MN-200", "Puja pack", 0.50), (base + 41, "KOL", "GC-200", "Puja snacks", 0.45)]
        for dc in ("BHW", "GGN", "KOL", "CHN"):
            ev += [(base + 43, dc, "GC-200", "Diwali chakli", 0.90), (base + 44, dc, "NM-500", "Diwali namkeen", 0.60)]
        ev += [(base + 51, "GGN", "GC-200", "Year-end chakli", 0.50), (base + 30, "BHW", "LJ-1L", "Monsoon cooler", 0.35)]
    return [e for e in ev if 1 <= e[0] <= 104]


@lru_cache(maxsize=1)
def two_year_store():
    from foundry.data.generate import Promo, build
    true = [Promo(w, dc, sku, name, u * REALISATION[dc]) for (w, dc, sku, name, u) in two_year_calendar()]
    s = build([], seed=21, weeks=104, signals=two_year_signals(), promos=true)
    # the calendar the planners hold carries the nominal uplift, not what the DC will realise
    nominal = {(w, dc, sku): u for (w, dc, sku, name, u) in two_year_calendar()}
    for r in s.promos:
        r["uplift_pct"] = nominal[(r["week"], r["dc"], r["sku"])]
    return s


def history2() -> pd.DataFrame:
    s = two_year_store()
    net = s.net
    promo = {(r["week"], r["dc"], r["sku"]): float(r["uplift_pct"]) for r in s.promos}
    rows = []
    for r in s.demand:
        dc, sku, w = r["dc"], r["sku"], r["week"]
        rows.append({"week": w, "dc": dc, "sku": sku, "cls": net.sku_class(sku), "consensus": float(r["forecast"]),
                     "actual": float(r["actual"]), "uplift": promo.get((w, dc, sku), 0.0),
                     "mu": net.weekly_mean(sku, dc), "sigma": net.weekly_sigma(sku, dc), "margin": net.contribution_per_case(sku)})
    df = pd.DataFrame(rows).sort_values(["dc", "sku", "week"]).reset_index(drop=True)
    df["series"] = df["dc"] + " " + df["sku"]
    return df


# --- the sensing agent on the two-year history, fast -------------------------------------------
# sense() reads one SKU's rows (and, for a promo, the same SKU at the other DCs), so a store that
# holds one SKU's rows gives the same answer as the whole store, about four times faster.
@lru_cache(maxsize=1)
def sku_stores() -> dict:
    from foundry.store import Store
    s = two_year_store()
    return {sku: Store(demand=[r for r in s.demand if r["sku"] == sku], promos=[r for r in s.promos if r["sku"] == sku],
                       weeks=s.weeks, net=s.net) for sku in s.net.parts}


@lru_cache(maxsize=4)
def _sensed2(first_target: int, last_target: int, horizon: int) -> pd.DataFrame:
    _, p = store_and_profile()
    subs = sku_stores()
    rows = []
    for sku, st in subs.items():
        for dc in st.net.dcs:
            for t in range(first_target, last_target + 1):
                r = sensing.sense(st, p, dc, sku, t - 1, horizon=horizon)
                row = {"series": f"{dc} {sku}", "dc": dc, "sku": sku, "target": t, "decision": r.decision,
                       "adjustment_pct": float(r.forecasts[0]["adjustment_pct"])}
                for k, f in enumerate(r.forecasts):
                    row[f"h{k + 1}"] = float(f["sensed"])
                rows.append(row)
    return pd.DataFrame(rows).sort_values(["series", "target"]).reset_index(drop=True)


def sensed2(first_target: int = 54, last_target: int = 104, horizon: int = 1) -> pd.DataFrame:
    """The sensing agent's numbers on the two-year history, origin = target - 1: h1 is its number for
    the target week, h2 and h3 (horizon 3) for the weeks after; decision is adjust, silent or ask."""
    return _sensed2(first_target, last_target, horizon).copy()
