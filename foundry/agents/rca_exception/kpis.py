"""KPIs and deviation detection. Maths decides what is a deviation; the model never does.

OTIF        share of orders both on time and in full (per DC, or per DC and SKU)
fill rate   cases shipped / cases requested
DOS         days of supply = on hand / daily demand (daily demand from the network's weekly mean)

A deviation is raised when a KPI breaches its absolute threshold from the profile, or sits z_alert
standard deviations below its trailing baseline, whichever fires first.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

from foundry.profile import Profile
from foundry.store import Store


@dataclass
class Deviation:
    kpi: str            # OTIF | FILL_RATE | DOS
    dc: str
    sku: str | None
    week: int
    value: float
    baseline_mean: float
    baseline_sd: float
    z: float
    threshold: float
    reason: str         # "below warn threshold" | "z below alert" | both

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def key(self) -> str:
        return f"{self.kpi}:{self.dc}:{self.sku or 'ALL'}:w{self.week}"


def otif(store: Store, week: int, dc: str, sku: str | None = None) -> float:
    rows = [r for r in store.rows("orders", dc=dc, week=week) if sku is None or r["sku"] == sku]
    if not rows:
        return float("nan")
    return sum(1 for r in rows if r["on_time"] and r["in_full"]) / len(rows)


def fill_rate(store: Store, week: int, dc: str, sku: str | None = None) -> float:
    rows = [r for r in store.rows("orders", dc=dc, week=week) if sku is None or r["sku"] == sku]
    req = sum(r["requested"] for r in rows)
    return sum(r["shipped"] for r in rows) / req if req else float("nan")


def days_of_supply(store: Store, week: int, dc: str, sku: str) -> float:
    inv = store.rows("inventory", week=week, dc=dc, sku=sku)
    if not inv:
        return float("nan")
    return store.net.days_of_supply(sku, dc, inv[0]["on_hand"])


def _baseline(series: list[float]) -> tuple[float, float]:
    vals = [v for v in series if not math.isnan(v)]
    if len(vals) < 2:
        return (float("nan"), float("nan"))
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / (len(vals) - 1)
    return (m, math.sqrt(var))


def deviations(store: Store, profile: Profile, week: int) -> list[Deviation]:
    """Every KPI that moved this week, DC level first, then DC and SKU for A and B items, then DOS."""
    k = profile.kpis
    n_base = int(k.get("baseline_weeks", 8))
    base_weeks = [w for w in range(max(1, week - n_base), week)]
    out: list[Deviation] = []

    def check(kpi: str, dc: str, sku: str | None, value: float, series: list[float], warn: float, min_n: int = 0, n: int = 0):
        if math.isnan(value) or n < min_n:
            return
        m, sd = _baseline(series)
        z = (value - m) / sd if sd and sd > 0 else 0.0
        reasons = []
        if value < warn:
            reasons.append("below warn threshold")
        if z <= -float(k.get("z_alert", 2.0)):
            reasons.append("z below alert")
        if reasons:
            out.append(Deviation(kpi, dc, sku, week, round(value, 4), round(m, 4), round(sd, 4), round(z, 2), warn, " and ".join(reasons)))

    for dc in store.net.dcs:
        check("OTIF", dc, None, otif(store, week, dc), [otif(store, w, dc) for w in base_weeks], float(k["otif_warn"]))
        check("FILL_RATE", dc, None, fill_rate(store, week, dc), [fill_rate(store, w, dc) for w in base_weeks], float(k["fill_rate_target"]) - 0.02)
        for sku, (_n, cls, *_rest) in store.net.parts.items():
            if cls in ("A", "B"):
                n_orders = len(store.rows("orders", dc=dc, week=week, sku=sku))
                check("OTIF", dc, sku, otif(store, week, dc, sku), [otif(store, w, dc, sku) for w in base_weeks],
                      float(k.get("sku_otif_warn", 0.85)), min_n=8, n=n_orders)
            dos = days_of_supply(store, week, dc, sku)
            min_dos = float(k["min_dos_days"])
            if dos < min_dos:
                hist = [days_of_supply(store, w, dc, sku) for w in base_weeks]
                m, sd = _baseline(hist)
                z = (dos - m) / sd if sd and sd > 0 else 0.0
                chronic = (not math.isnan(m)) and m < min_dos
                # a DC that is always short (Chennai, by design) is reported only when it gets worse
                if (not chronic) or z <= -float(k.get("z_alert", 2.0)):
                    reason = "below min days of supply" + (", chronic and worsening" if chronic else "")
                    out.append(Deviation("DOS", dc, sku, week, round(dos, 1), round(m, 1), round(sd, 1), round(z, 2), min_dos, reason))
    # worst first: biggest gap to threshold
    out.sort(key=lambda d: (d.value - d.threshold) / max(d.threshold, 1e-9))
    return out
