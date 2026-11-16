"""The diagnosis tree: six candidate causes, each with an evidence gatherer that returns a score
between 0 and 1 and the facts behind it. The ranking is arithmetic. The model's job is to read the
evidence, explain it to a planner, and choose the playbook; it never invents a cause the tree did
not score.

Causes (the order is the order a transport planner would check them in):
  DATA_STALE            the number is wrong, not the chain
  TRANSPORT_DELAY       inbound trucks late or slipped
  DOCK_CONGESTION       the DC itself is the bottleneck (DC-level)
  SUPPLY_MISS           the plant shipped short or not at all
  DEMAND_SPIKE          sell-out ran far above forecast
  INVENTORY_IMBALANCE   short here, long somewhere else, same SKU
  NO_CLEAR_CAUSE        nothing scored: likely noise; watch, do not act
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, asdict, field

from foundry.profile import Profile
from foundry.store import Store
from foundry.agents.rca_exception.kpis import days_of_supply

CAUSES = ["DATA_STALE", "TRANSPORT_DELAY", "DOCK_CONGESTION", "SUPPLY_MISS", "DEMAND_SPIKE", "INVENTORY_IMBALANCE"]


@dataclass
class Evidence:
    cause: str
    score: float
    facts: dict = field(default_factory=dict)
    summary: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Diagnosis:
    dc: str
    sku: str | None
    week: int
    kpi: str
    primary: str
    confidence: float
    contributing: list[str]
    evidence: list[Evidence]

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def ev_data_stale(store: Store, profile: Profile, week: int, dc: str, sku: str) -> Evidence:
    inv = store.rows("inventory", week=week, dc=dc, sku=sku)
    if not inv:
        return Evidence("DATA_STALE", 0.0, {}, "no snapshot")
    age = float(inv[0]["snapshot_age_h"])
    max_age = float(profile.diagnosis["max_snapshot_age_h"])
    score = _clamp(age / (2 * max_age)) if age > max_age else 0.0
    facts = {"snapshot_age_h": age, "max_snapshot_age_h": max_age}
    # an impossible drop: on hand fell by more than 80% while receipts covered demand
    prev = store.rows("inventory", week=week - 1, dc=dc, sku=sku)
    rec = store.rows("receipts", week=week, dc=dc, sku=sku)
    dem = store.rows("demand", week=week, dc=dc, sku=sku)
    if prev and rec and dem and prev[0]["on_hand"] > 0:
        drop = 1 - inv[0]["on_hand"] / prev[0]["on_hand"]
        covered = rec[0]["received"] >= 0.8 * dem[0]["actual"]
        facts.update({"on_hand_drop_pct": round(drop * 100), "receipts_covered_demand": covered, "last_good_on_hand": prev[0]["on_hand"]})
        if drop > 0.8 and covered:
            score = max(score, 1.0)
    summary = f"snapshot is {age:.0f} h old (limit {max_age:.0f} h)" if age > max_age else "snapshot fresh"
    if facts.get("on_hand_drop_pct", 0) > 80 and facts.get("receipts_covered_demand"):
        summary += f"; on hand fell {facts['on_hand_drop_pct']}% in a week while receipts covered demand, which is not physically possible"
    return Evidence("DATA_STALE", round(score, 2), facts, summary)


def ev_transport_delay(store: Store, profile: Profile, week: int, dc: str, sku: str) -> Evidence:
    trucks = store.rows("shipments", week=week, lane_to=dc, sku=sku)
    if not trucks:
        return Evidence("TRANSPORT_DELAY", 0.0, {}, "no inbound trucks this week")
    thr_days = float(profile.diagnosis["late_truck_days"])
    late = [t for t in trucks if float(t["late_days"]) >= thr_days]
    share = len(late) / len(trucks)
    thr = float(profile.diagnosis["late_truck_share"])
    slipped = sum(t["cases"] for t in late if float(t["late_days"]) >= 3.0)
    carrier = Counter(t["carrier"] for t in late).most_common(1)[0][0] if late else None
    avg_late = sum(float(t["late_days"]) for t in late) / len(late) if late else 0.0
    # how many trucks were late, scaled by how late: one truck a day late is a weak signal
    score = _clamp(share / (2 * thr)) * _clamp(avg_late / 2.0) if share >= thr else 0.0
    facts = {"trucks": len(trucks), "late_trucks": len(late), "late_share": round(share, 2), "avg_late_days": round(avg_late, 1),
             "cases_slipped_to_next_week": slipped, "carrier_most_late": carrier, "lane": f"{trucks[0]['lane_from']}->{dc}"}
    summary = (f"{len(late)} of {len(trucks)} inbound trucks on {facts['lane']} late by {avg_late:.1f} days"
               f"{', ' + str(slipped) + ' cases slipped to next week' if slipped else ''}"
               f"{', mostly ' + carrier if carrier else ''}") if late else "inbound trucks on time"
    return Evidence("TRANSPORT_DELAY", round(score, 2), facts, summary)


def ev_dock_congestion(store: Store, profile: Profile, week: int, dc: str, sku: str | None = None) -> Evidence:
    this = store.rows("dock", week=week, dc=dc)
    base_n = int(profile.kpis.get("baseline_weeks", 8))
    base = [r["avg_dwell_h"] for r in store.dock if r["dc"] == dc and week - base_n <= r["week"] < week]
    if not this or not base:
        return Evidence("DOCK_CONGESTION", 0.0, {}, "no dock data")
    dwell = float(this[0]["avg_dwell_h"])
    base_mean = sum(base) / len(base)
    factor = dwell / base_mean if base_mean else 0.0
    thr = float(profile.diagnosis["dock_dwell_factor"])
    score = _clamp((factor - 1) / (thr - 1) * 0.5 + 0.5) if factor >= thr else _clamp((factor - 1) / (thr - 1) * 0.5) if factor > 1.3 else 0.0
    facts = {"avg_dwell_h": dwell, "baseline_dwell_h": round(base_mean, 1), "factor": round(factor, 2), "trucks_served": this[0]["trucks_served"]}
    if factor >= thr:
        summary = f"dock dwell {dwell:.1f} h against a baseline of {base_mean:.1f} h ({factor:.1f}x), all SKUs at {dc} affected"
    elif score > 0:   # a weak signal must not read as "normal" (found running a brewer, 8 Nov)
        summary = f"dock dwell {dwell:.1f} h against a baseline of {base_mean:.1f} h ({factor:.1f}x): elevated, under the {thr:.1f}x line"
    else:
        summary = "dock dwell normal"
    return Evidence("DOCK_CONGESTION", round(score, 2), facts, summary)


def ev_supply_miss(store: Store, profile: Profile, week: int, dc: str, sku: str) -> Evidence:
    rows = [r for r in store.receipts if r["dc"] == dc and r["sku"] == sku and week - 1 <= r["week"] <= week]
    if not rows:
        return Evidence("SUPPLY_MISS", 0.0, {}, "no receipts data")
    planned = sum(r["planned"] for r in rows)
    received = sum(r["received"] for r in rows)
    short = 1 - received / planned if planned else 0.0
    # transport slips also reduce receipts; a supply miss is a short plant shipment, so subtract slipped cases
    slipped = sum(t["cases"] for t in store.shipments if t["lane_to"] == dc and t["sku"] == sku and week - 1 <= t["week"] <= week and float(t["late_days"]) >= 3.0)
    short_plant = 1 - (received + slipped) / planned if planned else 0.0
    thr = float(profile.diagnosis["supply_short_pct"])
    score = _clamp(short_plant / 0.5) if short_plant >= thr else 0.0
    facts = {"planned_2w": planned, "received_2w": received, "short_pct": round(short * 100), "short_pct_excl_transport": round(short_plant * 100), "plant": store.net.parts[sku][2]}
    summary = f"plant {facts['plant']} shipped {received} of {planned} planned cases over two weeks ({facts['short_pct_excl_transport']}% short, transport slips excluded)" if short_plant >= thr else "plant shipments complete"
    return Evidence("SUPPLY_MISS", round(score, 2), facts, summary)


def ev_demand_spike(store: Store, profile: Profile, week: int, dc: str, sku: str) -> Evidence:
    rows = [r for r in store.demand if r["dc"] == dc and r["sku"] == sku and week - 1 <= r["week"] <= week]
    if not rows:
        return Evidence("DEMAND_SPIKE", 0.0, {}, "no demand data")
    f = sum(r["forecast"] for r in rows)
    a = sum(r["actual"] for r in rows)
    ratio = a / f - 1 if f else 0.0
    thr = float(profile.diagnosis["demand_spike_pct"])
    score = _clamp(ratio / (3 * thr)) if ratio >= thr else 0.0
    facts = {"forecast_2w": f, "actual_2w": a, "over_forecast_pct": round(ratio * 100)}
    summary = f"sell-out {a} against a forecast of {f} over two weeks ({ratio * 100:.0f}% over)" if ratio >= thr else "sell-out near forecast"
    return Evidence("DEMAND_SPIKE", round(score, 2), facts, summary)


def ev_inventory_imbalance(store: Store, profile: Profile, week: int, dc: str, sku: str) -> Evidence:
    min_dos = float(profile.kpis["min_dos_days"])
    max_dos = float(profile.kpis["max_dos_days"])
    max_age = float(profile.diagnosis["max_snapshot_age_h"])

    def trusted_on_hand(d: str) -> tuple[float, bool]:
        inv = store.rows("inventory", week=week, dc=d, sku=sku)
        if inv and float(inv[0]["snapshot_age_h"]) <= max_age:
            return float(inv[0]["on_hand"]), True
        prev = store.rows("inventory", week=week - 1, dc=d, sku=sku)
        return (float(prev[0]["on_hand"]) if prev else 0.0), False

    here, fresh = trusted_on_hand(dc)
    dos_here = store.net.days_of_supply(sku, dc, here)
    longs = []
    for other in store.net.dcs:
        if other == dc or not store.net.stocked(sku, other):      # a DC that never carries this pack is not "long"
            continue
        oh, ofresh = trusted_on_hand(other)
        d = store.net.days_of_supply(sku, other, oh)
        if d > max_dos:
            surplus = (d - max_dos) * store.net.daily_demand(sku, other)
            longs.append({"dc": other, "dos": round(d, 1), "surplus_cases": round(surplus), "fresh": ofresh})
    longs.sort(key=lambda x: -x["surplus_cases"])
    score = 1.0 if (dos_here < min_dos and longs) else (0.3 if dos_here < min_dos else 0.0)
    facts = {"dos_here": round(dos_here, 1), "snapshot_fresh": fresh, "long_dcs": longs}
    if dos_here < min_dos and longs:
        top = longs[0]
        lane = store.net.lane(top["dc"], dc)
        facts["lane"] = {"from": top["dc"], "days": lane[0], "inr_per_case": lane[1]} if lane else None
        summary = (f"{dc} holds {dos_here:.1f} days while {top['dc']} holds {top['dos']} days of {sku}"
                   f" ({top['surplus_cases']} cases above {max_dos:.0f} days{'' if top['fresh'] else ', from last good snapshot'})")
    else:
        summary = "no surplus elsewhere to move" if dos_here < min_dos else "stock balanced"
    return Evidence("INVENTORY_IMBALANCE", round(score, 2), facts, summary)


def gather(store: Store, profile: Profile, week: int, dc: str, sku: str | None, kpi: str = "OTIF") -> Diagnosis:
    """Run every gatherer, rank, and name a primary cause. kpi tells the ranking what moved."""
    evidence: list[Evidence] = []
    if sku:
        evidence.append(ev_data_stale(store, profile, week, dc, sku))
        evidence.append(ev_transport_delay(store, profile, week, dc, sku))
        evidence.append(ev_dock_congestion(store, profile, week, dc, sku))
        evidence.append(ev_supply_miss(store, profile, week, dc, sku))
        evidence.append(ev_demand_spike(store, profile, week, dc, sku))
        evidence.append(ev_inventory_imbalance(store, profile, week, dc, sku))
    else:
        evidence.append(ev_dock_congestion(store, profile, week, dc, None))
        # a DC-level OTIF move: look for the SKU-level causes that touch the most orders
        for cause in ("TRANSPORT_DELAY", "SUPPLY_MISS", "DEMAND_SPIKE"):
            best = None
            for s in store.net.parts:
                fn = {"TRANSPORT_DELAY": ev_transport_delay, "SUPPLY_MISS": ev_supply_miss, "DEMAND_SPIKE": ev_demand_spike}[cause]
                e = fn(store, profile, week, dc, s)
                if best is None or e.score > best.score:
                    best = e
                    best.facts["sku"] = s
            if best:
                evidence.append(best)
    for plug in profile.plugins.causes:                     # Part 6: a company's own causes run beside the six
        if sku or plug.dc_level:
            evidence.append(plug.gatherer(store, profile, week, dc, sku))
    evidence = profile.plugins.apply_overrides(evidence, kpi)
    scored = sorted(evidence, key=lambda e: -e.score)
    ranking = [e for e in scored]
    # data first: a DOS move on a stale snapshot is a data problem until proven otherwise
    stale = next((e for e in evidence if e.cause == "DATA_STALE"), None)
    if kpi == "DOS" and stale and stale.score > 0:
        # a stock KPI read off a snapshot older than the limit is a data problem until the refresh says otherwise
        primary = stale
        cands = [stale] + [e for e in ranking if e is not stale]
    else:
        # an imbalance explains a low DOS, not a drop in OTIF caused by late trucks; demote it on OTIF moves
        cands = [e for e in ranking if not (kpi == "OTIF" and e.cause == "INVENTORY_IMBALANCE")]
        primary = cands[0] if cands else ranking[0]
    others = [e for e in ranking if e is not primary]
    competitors = [e for e in cands if e is not primary]
    second = competitors[0].score if competitors else 0.0
    if primary.score < 0.3:
        name, conf = "NO_CLEAR_CAUSE", round(1 - primary.score, 2)
    else:
        name = primary.cause
        conf = round(_clamp(0.5 + 0.5 * (primary.score - second) + 0.2 * primary.score - 0.1, 0.35, 0.95), 2)
    contributing = [e.cause for e in others if e.score >= 0.4 and e.cause != name]
    return Diagnosis(dc, sku, week, kpi, name, conf, contributing, evidence)
