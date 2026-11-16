"""Demand sensing: the maths. Stdlib only; every number the agent states comes from here.

series        the closed weeks of forecast (the consensus) and actual (sell-out), with the promo calendar
mape, bias    how wrong the consensus has been, and which way
exp_smooth    a short-horizon level from the last history_weeks weeks, promos taken out first
promo_uplift  what a promo did last time, measured against the level just before it
backtest      rolling-origin, one step ahead, over the last holdout_weeks closed weeks: pick alpha and w
sense         the sensed numbers for the next horizon weeks, the adjustment, and the decision rule
truth         for labs and evals only: what actually happened in the target weeks

The consensus is the planning tool's number, flat within the quarter. Sensing never writes it; it
proposes a nudge for the next one or two weeks and Kabir decides.
"""
from __future__ import annotations

import math
import statistics as st
from dataclasses import asdict, dataclass, field

from foundry.profile import Profile
from foundry.store import Store

W_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)      # blend weight on the consensus; 1.0 = keep the consensus


@dataclass
class Series:
    dc: str
    sku: str
    weeks: list[int]
    forecast: list[float]
    actual: list[float]
    promo: dict[int, dict]                 # week -> {"name", "uplift_pct"} for every calendar week, past and future

    @property
    def n(self) -> int:
        return len(self.weeks)

    def baseline(self, uplift_for: dict[int, float] | None = None) -> list[float]:
        """Actuals with the promo weeks deflated, so the level is the level of ordinary weeks."""
        out = []
        for w, a in zip(self.weeks, self.actual):
            u = (uplift_for or {}).get(w)
            if u is None:
                u = self.promo.get(w, {}).get("uplift_pct") or 0.0
            out.append(a / (1.0 + u) if u > -1 else a)
        return out


def series(store: Store, dc: str, sku: str, upto_week: int) -> Series:
    rows = sorted((r for r in store.demand if r["dc"] == dc and r["sku"] == sku and r["week"] <= upto_week), key=lambda r: r["week"])
    promo = {r["week"]: {"name": r["name"], "uplift_pct": float(r["uplift_pct"]) if r.get("uplift_pct") not in (None, "") else None,
                         "kind": r.get("kind") or None}
             for r in store.promos if r["dc"] == dc and r["sku"] == sku}
    return Series(dc, sku, [r["week"] for r in rows], [float(r["forecast"]) for r in rows], [float(r["actual"]) for r in rows], promo)


def mape(actuals: list[float], forecasts: list[float]) -> float:
    """Mean absolute percentage error, in percent. Weeks with zero actual are skipped."""
    pairs = [(a, f) for a, f in zip(actuals, forecasts) if a]
    if not pairs:
        return 0.0
    return 100.0 * sum(abs(a - f) / a for a, f in pairs) / len(pairs)


def bias(actuals: list[float], forecasts: list[float]) -> float:
    """Mean of (actual - forecast) / forecast, in percent. Positive = the forecast ran low."""
    pairs = [(a, f) for a, f in zip(actuals, forecasts) if f]
    if not pairs:
        return 0.0
    return 100.0 * sum((a - f) / f for a, f in pairs) / len(pairs)


def exp_smooth(values: list[float], alpha: float) -> float:
    """Simple exponential smoothing. The level starts at the mean of the first three points and
    then moves alpha of the way to each new point. Returns the level after the last point."""
    if not values:
        return 0.0
    level = st.mean(values[:3])
    for v in values[3:]:
        level = alpha * v + (1.0 - alpha) * level
    return level


def _sensing(profile: Profile) -> dict:
    return profile.raw.get("sensing", {})


def _mid_alpha(profile: Profile) -> float:
    alphas = list(_sensing(profile).get("alphas", [0.35]))
    return alphas[len(alphas) // 2]


def promo_uplift(store: Store, profile: Profile, dc: str, sku: str, upto_week: int, target_week: int | None = None) -> dict:
    """How much a promo will lift sell-out in the target week, as a share of the ordinary level.

    The calendar gives a number (uplift_pct); history says how much of the calendar's number promos
    here actually realise. Each past promo week is measured against the level just before it
    (actual / level - 1); a promo week that sold below the level is a stockout, not a promo, and is
    thrown out. realisation = mean(observed / calendar) over those weeks, bounded to [0.3, 1.5].
      1. own DC and SKU history  -> uplift = calendar x realisation (source "own history")
      2. same SKU at other DCs   -> the same, source "same SKU, other DCs"
      3. no history              -> the calendar's own number, realisation 1.0 (source "calendar")
      4. no calendar number      -> None: the agent stays silent and asks the planner
    """
    hist = _sensing(profile).get("history_weeks", 8)
    alpha = _mid_alpha(profile)
    ratio_mode = _sensing(profile).get("method", "level") == "ratio"
    row = next((r for r in store.promos if r["dc"] == dc and r["sku"] == sku and r["week"] == target_week), None) if target_week is not None else None
    kind = (row or {}).get("kind") or None          # a heatwave learns from heatwaves, a bank holiday from bank holidays

    def observed(d: str) -> list[float]:
        s = series(store, d, sku, upto_week)
        if s.n < 4:
            return []
        base = s.baseline()
        out = []
        for i, w in enumerate(s.weeks):
            cal = s.promo.get(w, {}).get("uplift_pct")
            if w in s.promo and cal and i >= 3 and s.promo[w].get("kind") == kind:
                if ratio_mode:      # against the consensus of that week, so the season is not counted as the event
                    prior = [b / f for b, f in zip(base[max(0, i - hist):i], s.forecast[max(0, i - hist):i]) if f]
                    level = exp_smooth(prior, alpha) * s.forecast[i] if prior else 0.0
                else:
                    level = exp_smooth(base[max(0, i - hist):i], alpha)
                if level > 0:
                    u = s.actual[i] / level - 1.0
                    if u > 0:
                        out.append(u / cal)
        return out

    cal = None
    if row and row.get("uplift_pct") not in (None, "", 0, 0.0):
        cal = float(row["uplift_pct"])
    if cal is None:
        return {"uplift": None, "calendar": None, "realisation": None, "source": "unknown", "observations": 0}
    own = observed(dc)
    if own:
        r = min(1.5, max(0.3, st.mean(own)))
        return {"uplift": round(cal * r, 3), "calendar": cal, "realisation": round(r, 2), "source": "own history", "observations": len(own)}
    other = []
    for d in store.net.dcs:
        if d != dc:
            other.extend(observed(d))
    if other:
        r = min(1.5, max(0.3, st.mean(other)))
        return {"uplift": round(cal * r, 3), "calendar": cal, "realisation": round(r, 2), "source": "same SKU, other DCs", "observations": len(other)}
    return {"uplift": cal, "calendar": cal, "realisation": 1.0, "source": "calendar", "observations": 0}


def short_horizon(store: Store, profile: Profile, dc: str, sku: str, origin_week: int, alpha: float, horizon: int = 2) -> list[dict]:
    """The level at the origin, promos taken out, then put back for target weeks the calendar names.

    method: level (GFB, the default) smooths the de-promoted actuals: right when the consensus is flat.
    method: ratio smooths actual / consensus and multiplies the target week's consensus by it: right when
    the consensus already carries a season (a brewer's summer). Smoothing levels across a seasonal step
    would read the coming peak as a fall and cut the forecast just before it (found running a brewer's
    coastal DC, 8 Nov)."""
    hist = _sensing(profile).get("history_weeks", 8)
    s = series(store, dc, sku, origin_week)
    base = s.baseline()[-hist:]
    ratio_mode = _sensing(profile).get("method", "level") == "ratio"
    if ratio_mode:
        fc = s.forecast[-hist:]
        ratios = [b / f for b, f in zip(base, fc) if f]
        ratio = exp_smooth(ratios, alpha) if ratios else 1.0
    level = exp_smooth(base, alpha)
    out = []
    for h in range(1, horizon + 1):
        t = origin_week + h
        if ratio_mode:
            row = next((r for r in store.demand if r["dc"] == dc and r["sku"] == sku and r["week"] == t), None)
            cons_t = float(row["forecast"]) if row else (s.forecast[-1] if s.forecast else 0.0)
            level = ratio * cons_t
        promo = s.promo.get(t)
        u = 0.0
        src = None
        if promo:
            pu = promo_uplift(store, profile, dc, sku, origin_week, t)
            u, src = pu["uplift"], pu["source"]
        out.append({"week": t, "level": round(level, 1), "promo": promo["name"] if promo else None,
                    "uplift": u, "uplift_source": src, "short_horizon": round(level * (1.0 + (u or 0.0)), 1),
                    "uplift_unknown": bool(promo) and u is None})
    return out


def backtest(store: Store, profile: Profile, dc: str, sku: str, origin_week: int) -> dict:
    """Rolling origin, one step ahead, over the last holdout_weeks closed weeks up to the origin,
    promo weeks left out (the calendar is judged separately). For each held-out week t the
    short-horizon level is built from the history_weeks weeks before t only. The grid is alphas x
    blend weights; the pair with the lowest MAPE wins. w = 1.0 is 'keep the consensus', so the blend
    never loses to the consensus on the held-out weeks; the question is whether it wins by enough,
    and in enough of the weeks."""
    cfg = _sensing(profile)
    hold = int(cfg.get("holdout_weeks", 8))
    hist = int(cfg.get("history_weeks", 8))
    alphas = [float(a) for a in cfg.get("alphas", [0.2, 0.35, 0.5])]
    s = series(store, dc, sku, origin_week)
    held = [w for w in s.weeks if w > hist and w not in s.promo][-hold:]
    if s.n < hist + 4 or len(held) < 4:
        return {"ok": False, "reason": f"series too short: {s.n} closed weeks, need {hist + 4} with 4 ordinary weeks to hold out", "holdout_weeks": held}
    rows = {w: next(i for i, x in enumerate(s.weeks) if x == w) for w in held}
    actual = {w: s.actual[rows[w]] for w in held}
    cons = {w: s.forecast[rows[w]] for w in held}
    short = {}
    for a in alphas:
        for t in held:
            short[(a, t)] = short_horizon(store, profile, dc, sku, t - 1, a, horizon=1)[0]["level"]
    mape_cons = mape([actual[t] for t in held], [cons[t] for t in held])
    best = None
    grid = []
    for a in alphas:
        for w in W_GRID:
            blend = [w * cons[t] + (1.0 - w) * short[(a, t)] for t in held]
            m = mape([actual[t] for t in held], blend)
            grid.append({"alpha": a, "w": w, "mape": round(m, 2)})
            if best is None or m < best[2] - 1e-9:
                best = (a, w, m)
    a, w, m = best
    wins = sum(1 for t in held if abs(actual[t] - (w * cons[t] + (1 - w) * short[(a, t)])) < abs(actual[t] - cons[t]) - 1e-9)
    improvement = (mape_cons - m) / mape_cons if mape_cons else 0.0
    return {"ok": True, "holdout_weeks": held, "alpha": a, "w": w, "mape_consensus": round(mape_cons, 2),
            "mape_blend": round(m, 2), "improvement_pct": round(improvement, 3), "wins": wins, "win_share": round(wins / len(held), 2),
            "grid": grid,
            "held": [{"week": t, "actual": actual[t], "consensus": cons[t], "level": round(short[(a, t)], 1),
                      "blend": round(w * cons[t] + (1 - w) * short[(a, t)], 1)} for t in held]}


def run_rules(store: Store, profile: Profile, dc: str, sku: str, upto_week: int) -> dict:
    """Simple run rules on the de-promoed series: three weeks the same side of the forecast is a trend;
    one week more than two sigma above is a spike that has not been confirmed."""
    s = series(store, dc, sku, upto_week)
    if s.n < 3:
        return {"rule": "none", "reason": "fewer than three weeks"}
    base = s.baseline()
    sigma = store.net.weekly_sigma(sku, dc)
    last3 = [(b - f) for b, f in zip(base[-3:], s.forecast[-3:])]
    if all(d > 0 for d in last3):
        return {"rule": "trend up", "weeks_above": 3}
    if all(d < 0 for d in last3):
        return {"rule": "trend down", "weeks_below": 3}
    if base[-1] > s.forecast[-1] + 2 * sigma and base[-2] <= s.forecast[-2] + 2 * sigma:
        return {"rule": "spike (unconfirmed)", "jump_pct": round(base[-1] / s.forecast[-1] - 1, 3)}
    return {"rule": "none"}


def consecutive_on_side(store: Store, dc: str, sku: str, upto_week: int, side: int) -> int:
    """How many closed weeks in a row, counting back from upto_week, the de-promoed actual sat on
    one side of the consensus (side +1 above, -1 below)."""
    s = series(store, dc, sku, upto_week)
    base = s.baseline()
    n = 0
    for b, f in zip(reversed(base), reversed(s.forecast)):
        if (b - f) * side > 0:
            n += 1
        else:
            break
    return n


PROMO_CONFIDENCE = {"own history": 0.80, "same SKU, other DCs": 0.65, "calendar": 0.50}


def confidence(improvement: float, n_held: int, holdout_weeks: int) -> float:
    """0.35 floor, 0.95 ceiling. Grows with the improvement (full marks at 50%) and shrinks when the
    back-test had fewer held-out weeks than the profile asked for."""
    c = 0.35 + 0.6 * min(max(improvement, 0.0), 0.5) / 0.5 * min(1.0, n_held / max(1, holdout_weeks))
    return round(min(0.95, max(0.35, c)), 2)


@dataclass
class Sensing:
    dc: str
    sku: str
    origin_week: int
    horizon: int
    decision: str                       # adjust | silent
    reasons: list[str]
    forecasts: list[dict]               # per target week: consensus, short_horizon, sensed, adjustment_pct, promo
    backtest: dict
    confidence: float
    co_sign_needed: bool
    run_rule: dict
    summary: str

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def consensus(self) -> list[float]:
        return [f["consensus"] for f in self.forecasts]

    @property
    def sensed(self) -> list[float]:
        return [f["sensed"] for f in self.forecasts]

    @property
    def adjustment_pct(self) -> list[float]:
        return [f["adjustment_pct"] for f in self.forecasts]


def sense(store: Store, profile: Profile, dc: str, sku: str, origin_week: int, horizon: int = 2) -> Sensing:
    """The decision rule, in words. For each target week:
      promo week (calendar) : adjust by the calibrated uplift if it is known and the nudge is at least
                              min_adjustment_pct; unknown uplift -> silent, ask the planner.
      ordinary week         : adjust by the blend only if the back-test beat the consensus by
                              min_improvement_pct AND in min_win_share of the held-out weeks, the nudge
                              is at least min_adjustment_pct, the series has sat on that side of the
                              consensus for confirm_weeks weeks in a row (a trend, a season, a shift),
                              and the origin week is not a lone jump the rule has not confirmed.
    A week that fails keeps the consensus (adjustment 0). The run adjusts if any week passes."""
    cfg = _sensing(profile)
    min_adj = float(cfg.get("min_adjustment_pct", 0.05))
    min_imp = float(cfg.get("min_improvement_pct", 0.10))
    min_win = float(cfg.get("min_win_share", 0.6))
    confirm = int(cfg.get("confirm_weeks", 2))
    cosign_above = float(cfg.get("cosign_above_pct", 0.25))
    hold = int(cfg.get("holdout_weeks", 8))
    s = series(store, dc, sku, origin_week)
    bt = backtest(store, profile, dc, sku, origin_week)
    rule = run_rules(store, profile, dc, sku, origin_week) if s.n >= 3 else {"rule": "none"}

    def consensus_for(t: int) -> float:
        row = next((r for r in store.demand if r["dc"] == dc and r["sku"] == sku and r["week"] == t), None)
        return float(row["forecast"]) if row else (s.forecast[-1] if s.forecast else 0.0)

    if not bt["ok"]:
        forecasts = [{"week": origin_week + h, "consensus": consensus_for(origin_week + h), "level": None, "sensed": consensus_for(origin_week + h),
                      "adjustment_pct": 0.0, "promo": s.promo.get(origin_week + h, {}).get("name"), "uplift": None, "uplift_source": None,
                      "status": "silent: " + bt["reason"]} for h in range(1, horizon + 1)]
        return Sensing(dc, sku, origin_week, horizon, "silent", [bt["reason"]], forecasts, bt, 0.35, False, rule,
                       f"{dc} {sku}: stay silent, {bt['reason']}.")

    a, w = bt["alpha"], bt["w"]
    sh = short_horizon(store, profile, dc, sku, origin_week, a, horizon)
    sigma = store.net.weekly_sigma(sku, dc)
    base = s.baseline()
    lone_jump = (origin_week not in s.promo) and base[-1] > s.forecast[-1] + 2 * sigma
    forecasts = []
    reasons: list[str] = []
    conf = 0.35
    for row in sh:
        t = row["week"]
        c = consensus_for(t)
        blend = w * c + (1.0 - w) * row["level"]
        if row["promo"]:
            if row["uplift_unknown"]:
                sensed, status = c, "silent: promo in the calendar but its uplift is unknown (no history, no number); ask the planner"
            else:
                sensed = blend * (1.0 + row["uplift"])
                adj = sensed / c - 1.0 if c else 0.0
                if abs(adj) >= min_adj:
                    status = f"adjust: promo {row['promo']} in the calendar, uplift {row['uplift']:+.0%} from {row['uplift_source']}"
                    conf = max(conf, PROMO_CONFIDENCE.get(row["uplift_source"], 0.5))
                else:
                    sensed, status = c, f"silent: the promo nudge is {adj:+.1%}, under the {min_adj:.0%} bar"
        else:
            adj = blend / c - 1.0 if c else 0.0
            side = 1 if adj > 0 else -1
            run = consecutive_on_side(store, dc, sku, origin_week, side)
            fails = []
            if bt["improvement_pct"] < min_imp:
                fails.append(f"the blend beats the consensus by {bt['improvement_pct']:.0%} on the held-out weeks, under the {min_imp:.0%} bar")
            if bt["win_share"] < min_win:
                fails.append(f"the blend won {bt['wins']} of {len(bt['holdout_weeks'])} held-out weeks, under the {min_win:.0%} bar")
            if abs(adj) < min_adj:
                fails.append(f"the adjustment is {adj:+.1%}, under the {min_adj:.0%} bar: noise")
            if run < confirm:
                fails.append(f"the series has sat {'above' if side > 0 else 'below'} the consensus for {run} week(s); the rule asks for {confirm} in a row")
            if lone_jump and consecutive_on_side(store, dc, sku, origin_week, 1) < confirm:
                fails.append(f"a jump of {base[-1] / s.forecast[-1] - 1:+.0%} at the origin week is not in the calendar and has not repeated")
            if fails:
                sensed, status = c, "silent: " + fails[0]
                reasons.extend(f for f in fails if f not in reasons)
            else:
                sensed = blend
                status = f"adjust: blend (alpha {a}, w {w}) cut the held-out MAPE from {bt['mape_consensus']}% to {bt['mape_blend']}% ({bt['improvement_pct']:.0%} better, {bt['wins']}/{len(bt['holdout_weeks'])} weeks), {run} weeks in a row {'above' if side > 0 else 'below'}"
                conf = max(conf, confidence(bt["improvement_pct"], len(bt["holdout_weeks"]), hold))
        forecasts.append({"week": t, "consensus": c, "level": row["level"], "sensed": round(sensed, 1),
                          "adjustment_pct": round(sensed / c - 1.0, 3) if c else 0.0, "promo": row["promo"],
                          "uplift": row["uplift"], "uplift_source": row["uplift_source"], "status": status})
    adjusting = [f for f in forecasts if f["status"].startswith("adjust")]
    decision = "adjust" if adjusting else "silent"
    if decision == "adjust":
        reasons = [f["status"][8:] for f in adjusting] + [f["status"][8:] for f in forecasts if f["status"].startswith("silent")]
    else:
        reasons = [f["status"][8:] for f in forecasts if f["status"].startswith("silent")]
        reasons = list(dict.fromkeys(reasons))
        conf = 0.35
    max_adj = max((abs(f["adjustment_pct"]) for f in adjusting), default=0.0)
    co_sign = decision == "adjust" and max_adj > cosign_above
    f1 = adjusting[0] if adjusting else forecasts[0]
    if decision == "adjust":
        summary = (f"{dc} {sku}: sensed {f1['sensed']:.0f} against a consensus of {f1['consensus']:.0f} for week {f1['week']} "
                   f"({f1['adjustment_pct']:+.0%}); {reasons[0]}.")
    else:
        summary = f"{dc} {sku}: stay silent; {reasons[0]}."
    return Sensing(dc, sku, origin_week, horizon, decision, reasons, forecasts, bt, round(conf, 2), co_sign, rule, summary)


def truth(store: Store, sensing: Sensing) -> dict:
    """What the target weeks actually did (labs and evals only: the agent never sees the future)."""
    rows = []
    for f in sensing.forecasts:
        r = next((x for x in store.demand if x["dc"] == sensing.dc and x["sku"] == sensing.sku and x["week"] == f["week"]), None)
        if r:
            rows.append({"week": f["week"], "actual": float(r["actual"]), "consensus": f["consensus"], "sensed": f["sensed"]})
    if not rows:
        return {"weeks": [], "note": "no actuals for the target weeks"}
    return {"weeks": rows,
            "mape_consensus": round(mape([r["actual"] for r in rows], [r["consensus"] for r in rows]), 2),
            "mape_sensed": round(mape([r["actual"] for r in rows], [r["sensed"] for r in rows]), 2)}


def scan(store: Store, profile: Profile, origin_week: int, horizon: int = 2, classes: tuple[str, ...] = ("A", "B")) -> list[dict]:
    """Every A/B SKU at every DC, sensed; the pairs that pass the rule first, ranked by the money the
    nudge moves (largest adjustment x weekly cases x contribution), then the silent ones."""
    out = []
    for sku, row in store.net.parts.items():
        if row[1] not in classes:
            continue
        for dc in store.net.dcs:
            if not store.net.stocked(sku, dc):
                continue
            sn = sense(store, profile, dc, sku, origin_week, horizon)
            adj = max((f["adjustment_pct"] for f in sn.forecasts), key=abs, default=0.0)
            value = abs(adj) * store.net.weekly_mean(sku, dc) * store.net.contribution_per_case(sku)
            out.append({"dc": dc, "sku": sku, "decision": sn.decision, "adjustment_pct": adj, "value_inr": round(value),
                        "improvement_pct": sn.backtest.get("improvement_pct", 0.0), "co_sign_needed": sn.co_sign_needed,
                        "reason": sn.reasons[0] if sn.reasons else ""})
    out.sort(key=lambda r: (r["decision"] != "adjust", -r["value_inr"], -abs(r["improvement_pct"])))
    return out
