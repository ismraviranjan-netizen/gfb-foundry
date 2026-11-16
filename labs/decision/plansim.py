"""The planning simulation behind Labs 13-15: what a forecast is worth once it drives orders.

Every one of the 40 series runs, over weeks 54-104 of the two-year history (data.history2), the
weekly order-up-to rule a planner runs in a spreadsheet:
  Monday of week t   the order placed L weeks ago lands (L = net.lead_weeks: 1, or 2 for five CHN rows);
                     the planner looks at stock on hand plus on order and orders up to
                     S = forecast of demand over weeks t..t+L + z x sigma x sqrt(L + 1), z = 1.645
  the week           demand is the actual; what is not on the shelf is lost; Sunday's stock pays holding
The forecast is the consensus (flat: the planning tool's number) when the agent is OFF, and the sensing
agent's numbers for weeks t, t+1, t+2 (data.sensed2, horizon 3, origin t - 1) when it is ON.
Cost per series per week = lost cases x contribution + Sunday's stock x a week's holding (20% a year).
Weeks 54-64 are a warm-up with the agent OFF everywhere; pilots run in weeks 65-104.
ASSUMPTIONS (the same for every lab that uses this): a short case is a lost sale; safety stock uses the
planning sigma at 95% (F.5's z); the planner orders exactly what the rule says (no override); no plant or
truck capacity limit. Because it is a simulation, both futures exist for every week (agent ON and OFF):
the labs use that to print the true effect next to what each measurement design would have reported.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from labs.decision.data import history2, sensed2, two_year_store

FIRST, PILOT, LAST = 54, 65, 104
Z = 1.645
DCS = ("BHW", "GGN", "KOL", "CHN")


@lru_cache(maxsize=1)
def _inputs() -> dict:
    h = history2()
    net = two_year_store().net
    sn = sensed2(FIRST, LAST, 3).set_index(["series", "target"])
    keys = sorted(h.series.unique())
    weeks = np.arange(FIRST, LAST + 1)
    A, C, FA = [], [], []
    L, sig, cu, hw = [], [], [], []
    for k in keys:
        dc, sku = k.split()
        g = h[h.series == k].set_index("week")
        lw = net.lead_weeks(sku, dc)
        A.append(g.actual.loc[FIRST:LAST].to_numpy(float))
        C.append(g.consensus.loc[FIRST:LAST].to_numpy(float) * (lw + 1))
        s = sn.loc[k]
        FA.append(sum(s[f"h{j + 1}"].loc[FIRST:LAST].to_numpy(float) for j in range(lw + 1)))
        L.append(lw)
        sig.append(net.weekly_sigma(sku, dc))
        cu.append(net.contribution_per_case(sku))
        hw.append(net.holding_cost_per_case_day(sku) * 7)
    L, sig = np.array(L), np.array(sig, float)
    return {"series": keys, "dc": np.array([k.split()[0] for k in keys]), "sku": np.array([k.split()[1] for k in keys]),
            "weeks": weeks, "actual": np.array(A), "cons_cover": np.array(C), "agent_cover": np.array(FA),
            "lead": L, "safety": Z * sig * np.sqrt(L + 1), "contrib": np.array(cu, float), "hold_week": np.array(hw, float),
            "consensus": np.array(C) / (L + 1)[:, None]}


def inputs() -> dict:
    return _inputs()


def simulate(on, many: bool = False) -> dict:
    """on: True/False per week over weeks 54-104 (W = 51 weeks). Shape (W,): every series the same;
    (n_series, W): one schedule per series; many=True with (m, W) or (m, n_series, W): m schedules at once.
    Returns arrays (m, n_series, W), m = 1 unless many: cost, lost (cases), lost_money and hold (INR)."""
    x = inputs()
    A, ns, W = x["actual"], len(x["series"]), len(x["weeks"])
    on = np.asarray(on, bool)
    if not many:
        on = on[None, None, :] if on.ndim == 1 else on[None, :, :]
    elif on.ndim == 2:
        on = on[:, None, :]
    m = on.shape[0]
    on = np.broadcast_to(on, (m, ns, W))
    L, idx = x["lead"], np.arange(ns)
    pipe = np.zeros((m, ns, W + 3))
    for i in range(ns):
        pipe[:, i, :L[i]] = x["consensus"][i, 0]
    oh = np.broadcast_to(x["consensus"][:, 0] + x["safety"], (m, ns)).copy()
    two = L == 2
    lost = np.zeros((m, ns, W))
    hold = np.zeros((m, ns, W))
    for t in range(W):
        oh += pipe[:, :, t]
        S = np.where(on[:, :, t], x["agent_cover"][:, t], x["cons_cover"][:, t]) + x["safety"]
        position = oh + np.where(two, pipe[:, :, t + 1], 0.0)
        pipe[:, idx, t + L] += np.maximum(0.0, S - position)
        served = np.minimum(oh, A[:, t])
        lost[:, :, t] = A[:, t] - served
        oh -= served
        hold[:, :, t] = oh * x["hold_week"]
    money = lost * x["contrib"][:, None]
    return {"cost": money + hold, "lost": lost, "lost_money": money, "hold": hold}


def pilot_weeks() -> np.ndarray:
    return inputs()["weeks"] >= PILOT


def schedule(weeks_on: np.ndarray, dcs=DCS) -> np.ndarray:
    """(n_series, W) assignment: ON in the pilot weeks given (bool over weeks 65-104) at the DCs given."""
    x = inputs()
    full = np.zeros(len(x["weeks"]), bool)
    full[x["weeks"] >= PILOT] = weeks_on
    return np.isin(x["dc"], dcs)[:, None] & full[None, :]


@lru_cache(maxsize=1)
def _both() -> tuple:
    off = simulate(np.zeros(len(inputs()["weeks"]), bool))
    on = simulate(pilot_weeks())
    return off, on


def both() -> tuple:
    """The two futures: the agent OFF everywhere, and ON everywhere from week 65."""
    return _both()
