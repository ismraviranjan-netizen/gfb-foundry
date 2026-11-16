"""Generate a company's weekly history from its Network, with injected incidents in the last weeks.

The generator is a tiny weekly simulation: receipts arrive (a truck 3+ days late slips to next
week, 1-2 days late ships its orders late), orders are filled from what is on hand, OTIF falls out
of the arithmetic. An injection changes one physical thing (a late truck, a jammed dock, a promo,
a short plant run, a stale snapshot) and the KPIs move as a consequence, which is what makes the
diagnosis tree testable: the tests know the cause because they put it there.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from foundry.network import GFB, Network
from foundry.store import Store

BASELINE_DWELL_H = 3.0
BASELINE_LATE_SHARE = 0.08
BASELINE_ON_TIME = 0.975
TARGET_WEEKS_OF_COVER = 1.6          # non-MN-1L SKUs open with 11 days of cover
ORDERS_PER_WEEK = {"A": 12, "B": 8, "C": 5}
CARRIERS = ["Sahyadri Roadways", "Deccan Freight", "Konkan Carriers"]


@dataclass
class Promo:
    """A row in the promo calendar: the planning team knows these weeks are coming; the consensus
    forecast usually does not carry them. uplift_pct is applied to sell-out in that week."""
    week: int
    dc: str
    sku: str
    name: str
    uplift_pct: float


@dataclass
class Signals:
    """Structure in demand that a flat consensus forecast misses: a trend per (dc, sku) in pct per
    week, and a seasonal wave per (dc, sku) as (amplitude, peak week, period in weeks)."""
    trend: dict = field(default_factory=dict)
    season: dict = field(default_factory=dict)
    consensus_season: bool = False      # True: the consensus forecast carries the season (planners know summer); sensing hunts the rest
    index: dict = field(default_factory=dict)   # (dc, sku) -> a planner's weekly index (52 values, mean 1.0); wins over the cosine

    def season_multiplier(self, dc: str, sku: str, week: int) -> float:
        ix = self.index.get((dc, sku))
        if ix:
            return float(ix[(week - 1) % len(ix)])
        s = self.season.get((dc, sku))
        if not s:
            return 1.0
        amp, peak, period = s
        return 1.0 + amp * math.cos(2 * math.pi * (week - peak) / period)

    def multiplier(self, dc: str, sku: str, week: int) -> float:
        m = 1.0 + self.trend.get((dc, sku), 0.0) * (week - 1)
        return m * self.season_multiplier(dc, sku, week)


@dataclass
class Injection:
    """One physical thing going wrong. kind in:
    transport_delay(late_share, late_days, carrier) · dock_congestion(dwell_h, on_time_share) ·
    demand_spike(pct) · supply_miss(short_pct) · data_stale(age_h, reported_on_hand) ·
    imbalance(short_on_hand, long_dc, long_on_hand) · extra_receipt(cases): a run pushed into one DC on
    top of its plan (it lands on time, before the week's orders ship: the DC is physically long)
    """
    kind: str
    week: int
    dc: str
    sku: str | None = None
    params: dict = field(default_factory=dict)


def build(injections: list[Injection] | None = None, seed: int = 7, weeks: int = 12, net: Network | None = None,
          signals: Signals | None = None, promos: list[Promo] | None = None) -> Store:
    """With no signals and no promos the random stream is untouched, so the canonical week-12
    numbers in the book do not move when a sensing history is generated with the same seed."""
    net = net or GFB
    rng = random.Random(seed)
    inj = injections or []
    signals = signals or Signals()
    promos = promos or []
    store = Store(weeks=weeks, net=net)
    for pr in promos:
        store.promos.append({"week": pr.week, "dc": pr.dc, "sku": pr.sku, "name": pr.name, "uplift_pct": pr.uplift_pct})
    promo_at = {(pr.week, pr.dc, pr.sku): pr.uplift_pct for pr in promos}
    carriers = list(net.carriers) or CARRIERS                       # a company's own hauliers, if its network names them
    orders_per_week = {**ORDERS_PER_WEEK, **net.orders_per_week}

    def find(kind, week, dc, sku=None):
        for i in inj:
            if i.kind == kind and i.week == week and i.dc == dc and (sku is None or i.sku in (None, sku)):
                return i
        return None

    target = {}
    on_hand = {}
    for sku in net.parts:
        for dc in net.dcs:
            if not net.stocked(sku, dc):              # a pack labelled for another market: no rows at all
                continue
            target[(dc, sku)] = net.opening_on_hand(sku, dc, TARGET_WEEKS_OF_COVER)
            on_hand[(dc, sku)] = target[(dc, sku)]
    carry_over: dict = {}   # cases on trucks that slipped into next week, per (dc, sku)
    truck_no = 1000

    for week in range(1, weeks + 1):
        for dc in net.dcs:
            dock_inj = find("dock_congestion", week, dc)
            dwell = dock_inj.params.get("dwell_h", 9.5) if dock_inj else round(rng.gauss(BASELINE_DWELL_H, 0.4), 1)
            on_time_share = dock_inj.params.get("on_time_share", 0.82) if dock_inj else BASELINE_ON_TIME
            trucks_served = 0

            for sku, (name, cls, plant, cost, units, _v) in net.parts.items():
                if (dc, sku) not in on_hand:
                    continue
                mu = net.weekly_mean(sku, dc)
                sigma = net.weekly_sigma(sku, dc)
                # the consensus: flat within the quarter (GFB), or carrying the known season (a brewer's summer)
                forecast = round(mu * signals.season_multiplier(dc, sku, week)) if signals.consensus_season else round(mu)
                actual = max(0.0, rng.gauss(mu, sigma))
                actual *= signals.multiplier(dc, sku, week)                      # trend and season the consensus misses
                actual *= 1.0 + promo_at.get((week, dc, sku), 0.0)              # a promo the calendar knows about
                spike = find("demand_spike", week, dc, sku)
                if spike:
                    actual = forecast * (1.0 + spike.params.get("pct", 0.6))
                actual = round(actual)
                store.demand.append({"week": week, "dc": dc, "sku": sku, "forecast": forecast, "actual": actual})

                # replenishment: order-up-to around the target, damped, planned on the forecast
                base = forecast if signals.consensus_season else mu
                planned = max(0, round(base + 0.5 * (target[(dc, sku)] - on_hand[(dc, sku)])))
                miss = find("supply_miss", week, dc, sku)
                received_share = 1.0 - (miss.params.get("short_pct", 1.0) if miss else 0.0)
                shippable = planned * received_share
                n_trucks = max(1, math.ceil(shippable * net.space(sku) / net.truck_cases)) if shippable > 0 else 0
                delay = find("transport_delay", week, dc, sku)
                late_share = delay.params.get("late_share", 0.67) if delay else BASELINE_LATE_SHARE
                late_days = delay.params.get("late_days", 3.0) if delay else 1.0
                carrier_bias = delay.params.get("carrier") if delay else None

                arrived_on_time = carry_over.pop((dc, sku), 0.0)   # last week's slipped trucks land first
                push = find("extra_receipt", week, dc, sku)
                if push:
                    arrived_on_time += float(push.params.get("cases", 0))
                arrived_late = 0.0
                # under an injection the number of late trucks is exact (at least one); baseline lateness is random
                n_late = max(1, round(late_share * n_trucks)) if (delay and n_trucks) else None
                for _t in range(n_trucks):
                    truck_no += 1
                    cases = round(shippable / n_trucks)
                    is_late = (_t < n_late) if n_late is not None else (rng.random() < late_share)
                    ld = late_days if is_late else 0.0
                    carrier = carrier_bias if (is_late and carrier_bias) else rng.choice(carriers)
                    store.shipments.append({
                        "week": week, "lane_from": plant, "lane_to": dc, "sku": sku,
                        "truck_id": f"{net.code}-T-{truck_no}", "carrier": carrier, "cases": cases, "late_days": ld,
                    })
                    trucks_served += 1
                    if ld >= 3.0:
                        carry_over[(dc, sku)] = carry_over.get((dc, sku), 0.0) + cases
                    elif ld > 0:
                        arrived_late += cases
                    else:
                        arrived_on_time += cases
                arrived = arrived_on_time + arrived_late
                store.receipts.append({"week": week, "dc": dc, "sku": sku, "planned": planned, "received": round(arrived)})

                # ship orders: on-hand and on-time receipts first (on time), late receipts next (late),
                # then nothing (short). OTIF is the share that was both on time and in full.
                n_orders = orders_per_week[cls]
                weights = [rng.uniform(0.6, 1.4) for _ in range(n_orders)]
                wsum = sum(weights)
                prompt_pool = on_hand[(dc, sku)] + arrived_on_time
                late_pool = arrived_late
                shipped_total = 0.0
                for k, w in enumerate(weights):
                    req = round(actual * w / wsum)
                    from_prompt = min(req, max(0.0, prompt_pool))
                    prompt_pool -= from_prompt
                    from_late = min(req - from_prompt, max(0.0, late_pool))
                    late_pool -= from_late
                    shp = from_prompt + from_late
                    shipped_total += shp
                    on_time = (rng.random() < on_time_share) and from_late == 0
                    store.orders.append({
                        "week": week, "dc": dc, "sku": sku, "order_id": f"SO-{week:02d}-{dc}-{sku}-{k+1:02d}",
                        "requested": req, "shipped": round(shp), "on_time": on_time, "in_full": shp >= req,
                    })
                on_hand[(dc, sku)] = max(0.0, on_hand[(dc, sku)] + arrived - shipped_total)

                stale = find("data_stale", week, dc, sku)
                age_h = stale.params.get("age_h", 36) if stale else round(rng.uniform(1.0, 6.0), 1)
                reported = on_hand[(dc, sku)]
                if stale:
                    prev = [r for r in store.inventory if r["dc"] == dc and r["sku"] == sku and r["week"] == week - 1]
                    reported = stale.params.get("reported_on_hand", prev[0]["on_hand"] if prev else reported)
                store.inventory.append({
                    "week": week, "dc": dc, "sku": sku, "on_hand": round(reported),
                    "open_receipt": round(carry_over.get((dc, sku), 0.0)), "snapshot_age_h": age_h,
                })
            store.dock.append({"week": week, "dc": dc, "trucks_served": trucks_served, "avg_dwell_h": dwell})

    for i in inj:
        if i.kind == "imbalance":
            for r in store.inventory:
                if r["week"] == i.week and r["sku"] == i.sku:
                    if r["dc"] == i.dc:
                        r["on_hand"] = i.params.get("short_on_hand", 300)
                    elif r["dc"] == i.params.get("long_dc", "KOL"):
                        r["on_hand"] = i.params.get("long_on_hand", 9000)
    return store


def canonical_week12() -> list[Injection]:
    """The incident week the book and the demo run on: five things go wrong at once."""
    return [
        Injection("transport_delay", 12, "CHN", "MN-1L", {"late_share": 0.67, "late_days": 3.0, "carrier": "Sahyadri Roadways"}),
        Injection("dock_congestion", 12, "GGN", None, {"dwell_h": 9.5, "on_time_share": 0.82}),
        Injection("demand_spike", 11, "BHW", "GC-200", {"pct": 1.0}),   # Diwali promo doubles chakli sell-out
        Injection("demand_spike", 12, "BHW", "GC-200", {"pct": 1.0}),
        Injection("supply_miss", 11, "BHW", "MP-1KG", {"short_pct": 0.5}),  # pulp line down: half a run, then none
        Injection("supply_miss", 12, "BHW", "MP-1KG", {"short_pct": 1.0}),
        Injection("data_stale", 12, "KOL", "MN-1L", {"age_h": 36, "reported_on_hand": 400}),
    ]


def canonical_promos() -> list[Promo]:
    """The promo calendar behind the week-11/12 chakli spike: the planning team knew it was coming."""
    return [Promo(11, "BHW", "GC-200", "Diwali chakli promo", 1.0), Promo(12, "BHW", "GC-200", "Diwali chakli promo", 1.0)]


def canonical_store(seed: int = 7, refreshed: bool = False) -> Store:
    """The incident-week store every agent and the tower read: twelve weeks, five things wrong in
    week 12, and the promo calendar. Identical numbers to build(canonical_week12(), seed=7).
    refreshed=True is the same week after Rohan's refresh of the Kolkata snapshot."""
    s = build(canonical_week12(), seed=seed, promos=canonical_promos())
    if refreshed:
        refresh_snapshot(s, 12, "KOL", "MN-1L")
    return s


def refresh_snapshot(store: Store, week: int, dc: str, sku: str, age_h: float = 1.0) -> dict:
    """What Rohan's refresh does: re-read the DC's stock for one SKU. The physically consistent
    number is last week's on hand plus what was received minus what was shipped this week; the
    snapshot age becomes an hour. Returns the before/after so the lab can print it."""
    prev = next((r for r in store.inventory if r["dc"] == dc and r["sku"] == sku and r["week"] == week - 1), None)
    cur = next((r for r in store.inventory if r["dc"] == dc and r["sku"] == sku and r["week"] == week), None)
    if not cur or not prev:
        return {"error": f"no inventory rows for {dc} {sku} in weeks {week - 1}-{week}"}
    received = sum(r["received"] for r in store.receipts if r["dc"] == dc and r["sku"] == sku and r["week"] == week)
    shipped = sum(r["shipped"] for r in store.orders if r["dc"] == dc and r["sku"] == sku and r["week"] == week)
    before = dict(cur)
    cur["on_hand"] = max(0, round(prev["on_hand"] + received - shipped))
    cur["snapshot_age_h"] = age_h
    return {"dc": dc, "sku": sku, "week": week, "before": before, "after": dict(cur), "received": received, "shipped": shipped}


# --- the demand history the sensing agent learns on -------------------------------------------
def sensing_signals() -> Signals:
    """Structure the flat consensus misses: a growing Chennai (1.5% a week), a growing Gurgaon
    small-pack, a shrinking Kolkata apple juice, and a summer wave on Bhiwandi nectar."""
    return Signals(
        trend={("CHN", "MN-1L"): 0.015, ("GGN", "MN-200"): 0.012, ("KOL", "AJ-1L"): -0.010},
        season={("BHW", "MN-1L"): (0.12, 8, 52), ("CHN", "MN-200"): (0.10, 10, 52)},
    )


def sensing_promos() -> list[Promo]:
    """Twenty weeks of promotions the calendar knows about. Weeks 17-20 are the held-out weeks."""
    return [
        Promo(4, "GGN", "GC-200", "Holi chakli", 0.60), Promo(5, "GGN", "GC-200", "Holi chakli", 0.60),
        Promo(7, "BHW", "MN-1L", "Summer 1+1", 0.40),
        Promo(9, "KOL", "MN-200", "Puja pack", 0.50), Promo(10, "KOL", "MN-200", "Puja pack", 0.50),
        Promo(12, "GGN", "GC-200", "Monsoon snack", 0.45),
        Promo(14, "BHW", "GC-200", "Diwali chakli", 1.00), Promo(15, "BHW", "GC-200", "Diwali chakli", 1.00),
        Promo(18, "CHN", "MN-1L", "Pongal pack", 0.35),
        Promo(19, "GGN", "GC-200", "Year-end chakli", 0.50),
    ]


def sensing_history(weeks: int = 20, seed: int = 11, net: Network | None = None) -> Store:
    """The demand history for Part 3: no incidents, twenty weeks, trends, a season and promos."""
    return build([], seed=seed, weeks=weeks, net=net, signals=sensing_signals(), promos=sensing_promos())


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "data/out"
    s = canonical_store()
    s.to_csv(out)
    print({t: len(getattr(s, t)) for t in ("demand", "inventory", "orders", "shipments", "dock", "receipts")}, "->", out)
