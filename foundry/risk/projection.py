"""The risk radar: a DRP grid (The Planner's Engine, Part III) run daily on today's truth.

    PAB(d) = PAB(d-1) - demand served on day d + arrivals landing on day d (put away overnight)

Day 0 is the snapshot at the end of the closed week; days 1..H follow. Conventions, all shared with
the rebalancing agent so the two never disagree:
  * an open receipt (cases in transit or slipped) lands at the end of day 2, or on the ETA a carrier
    reported (the listener writes it into the snapshot row as open_receipt_eta_days);
  * the plant's plan keeps landing. With a receipts history (the generated weeks), it lands mid-week
    (end of day 4, then every 7 days) at the average planned weekly quantity, scaled down when the
    plant has delivered less than the reliability floor over the last two weeks. With no history
    (an opening position, F.5), the canon's rule: the first receipt lands at lead time minus two days;
  * demand per day is the consensus weekly forecast / 7, or the sensing agent's number when asked;
  * a truck that lands on day d serves demand from day d+1.

One clock (8 Nov): arrival_schedule() is the only place that says what lands when. The radar projects
from it and the rebalancing agent times its moves from it (next_receipt_day), so the two cannot
disagree about the next truck. It also reads the tower's own memory, store.extra["transfers_inflight"]:
a transfer the tower applied lands at the receiver and has already left the sender.

Classes: RED a shortage of at least a quarter of a day's demand inside the horizon; AMBER no such
shortage but the balance dips under safety stock; GREEN otherwise; GREY the snapshot is too old to
trust (data before chain, as in the diagnosis tree). LONG marks a DC above its long line: a sender.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from foundry.notify.coworker import code as cur
from foundry.profile import Profile
from foundry.store import Store

PROCESSING_DAYS = 2
RECEIPT_LANDS_IN = 2
DEFAULTS = {"horizon_days": 14, "reliability_floor": 0.8, "pre_empt_min_inr": 25000, "plant_receipt_day": 4,
            "history_weeks": 4, "red_min_share_of_day": 0.25}


def cfg(profile: Profile) -> dict:
    c = dict(DEFAULTS)
    c.update(profile.section("risk"))
    return c


@dataclass
class Projection:
    dc: str
    sku: str
    week: int
    horizon_days: int
    risk: str                    # RED AMBER GREEN GREY
    long: bool
    start_on_hand: float
    snapshot_age_h: float
    fresh: bool
    daily_demand: float
    demand_source: str
    dos0: float
    safety_stock: float
    reorder_point: float
    first_short_day: int | None
    cases_short: float
    money_at_risk: int
    min_balance: float
    hours_to_impact: float
    priority: str
    sla_hours: int
    arrivals: list[dict] = field(default_factory=list)
    days: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self, with_days: bool = True) -> dict:
        d = asdict(self)
        if not with_days:
            d.pop("days")
        return d


def _last_week(store: Store, table: str, dc: str, sku: str, week: int) -> list[dict]:
    return sorted([r for r in getattr(store, table) if r["dc"] == dc and r["sku"] == sku and r["week"] <= week], key=lambda r: r["week"])


def plant_schedule(store: Store, profile: Profile, week: int, dc: str, sku: str, horizon: int, open_receipt: float) -> tuple[list[dict], list[str]]:
    """When and how much the plant's plan lands over the horizon, and the assumptions made."""
    c = cfg(profile)
    net = store.net
    hist = _last_week(store, "receipts", dc, sku, week)
    notes = []
    out = []
    if len(hist) >= 2:
        recent = hist[-int(c["history_weeks"]):]
        qty = sum(r["planned"] for r in recent) / len(recent)
        last2 = hist[-2:]
        planned2 = sum(r["planned"] for r in last2)
        delivered2 = sum(r["received"] for r in last2) + open_receipt     # slipped cases are late, not lost
        reliability = min(1.0, delivered2 / planned2) if planned2 else 1.0
        if reliability < float(c["reliability_floor"]):
            notes.append(f"plant delivered {reliability:.0%} of plan over two weeks; the radar assumes that continues until the plant planner says otherwise")
            qty *= reliability
        day = int(c["plant_receipt_day"])
        while day <= horizon:
            out.append({"day": day, "cases": round(qty, 1), "source": f"plant plan ({net.parts[sku][2]})"})
            day += 7
        notes.append(f"plant plan from history: {qty:,.0f} cases a week, landing mid-week")
    else:
        day = max(1, net.lead_days(sku, dc) - PROCESSING_DAYS)
        qty = net.weekly_mean(sku, dc)
        while day <= horizon:
            out.append({"day": day, "cases": round(qty, 1), "source": f"plant plan ({net.parts[sku][2]})"})
            day += 7
        notes.append(f"no receipts history: first plant receipt at lead time less {PROCESSING_DAYS} days (day {max(1, net.lead_days(sku, dc) - PROCESSING_DAYS)})")
    return out, notes


def lane_delay(store: Store, week: int, src: str, dst: str, on_the_road: bool = False) -> tuple[int, list[str]]:
    """Days a carrier or the listener says this lane is running late this week (store.extra["lane_delays"]).
    A delay with applies_to "new" (a strike: no new dispatches) leaves trucks already on the road alone;
    "all" (a road closure, a flood) holds those too. on_the_road=True asks only for the second kind."""
    days, why = 0, []
    for d in store.extra.get("lane_delays", []):
        if int(d["week"]) == week and d["lane_from"] == src and d["lane_to"] in (dst, "*"):
            if on_the_road and d.get("applies_to", "all") != "all":
                continue
            days += int(d["delay_days"])
            why.append(d.get("reason", "lane delay"))
    return days, why


def inflight(store: Store, week: int, dc: str, sku: str) -> tuple[list[dict], float]:
    """Transfers the tower applied that have not landed (arrivals into this DC, cases gone from it),
    plus cases held for transfers still waiting for signatures (store.extra["reservations"])."""
    arrivals, gone = [], 0.0
    for t in store.extra.get("transfers_inflight", []):
        if t.get("sku") != sku or t.get("status", "IN_TRANSIT") != "IN_TRANSIT":
            continue
        day = int(t["lands_day"]) - 7 * (week - int(t["week"]))
        if t.get("to_dc") == dc and day >= 1:
            arrivals.append({"day": day, "cases": float(t["cases"]),
                             "source": f"transfer {t['from_dc']}->{dc} {t.get('transfer_id', '')}".strip()})
        if t.get("from_dc") == dc and int(t["week"]) == week:
            gone += float(t["cases"])          # picked and shipped after the snapshot was taken
    for r in store.extra.get("reservations", []):
        # stock a plan waiting for signatures has spoken for: not gone yet, but not spare either
        if r.get("sku") == sku and r.get("from_dc") == dc and int(r["week"]) == week and r.get("status") == "HELD":
            gone += float(r["cases"])
    return arrivals, gone


def arrival_schedule(store: Store, profile: Profile, week: int, dc: str, sku: str, horizon: int | None = None
                     ) -> tuple[list[dict], float, list[str]]:
    """(arrivals, cases committed out, notes): everything that lands at this DC over the horizon."""
    horizon = int(horizon or cfg(profile)["horizon_days"])
    inv = _last_week(store, "inventory", dc, sku, week)
    snap = inv[-1] if inv else {}
    open_receipt = float(snap.get("open_receipt", 0) or 0)
    arrivals, notes = [], []
    plant_code = store.net.parts[sku][2] if sku in store.net.parts else None
    delay, why = lane_delay(store, week, plant_code, dc) if plant_code else (0, [])
    if delay:
        notes.append(f"{plant_code}->{dc} running {delay} days late: {'; '.join(why)}")
    if open_receipt > 0:
        if snap.get("open_receipt_eta_days"):          # a carrier's own ETA for these trucks beats a lane-wide delay
            arrivals.append({"day": int(snap["open_receipt_eta_days"]), "cases": open_receipt, "source": "open receipt (carrier ETA)"})
        else:
            rolling = lane_delay(store, week, plant_code, dc, on_the_road=True)[0] if plant_code else 0
            arrivals.append({"day": RECEIPT_LANDS_IN + rolling, "cases": open_receipt, "source": "open receipt (in transit)"})
    plant, pnotes = plant_schedule(store, profile, week, dc, sku, horizon + delay, open_receipt)
    arrivals += [{**a, "day": a["day"] + delay} for a in plant if a["day"] + delay <= horizon]
    notes += pnotes
    moving, gone = inflight(store, week, dc, sku)
    arrivals += [a for a in moving if a["day"] <= horizon]
    if moving or gone:
        notes.append(f"tower transfers in flight: {sum(a['cases'] for a in moving):,.0f} cases arriving, {gone:,.0f} cases already sent")
    return sorted(arrivals, key=lambda a: a["day"]), gone, notes


def next_receipt_day(store: Store, profile: Profile, week: int, dc: str, sku: str) -> int:
    """The day the next truck of any kind lands at this DC (the rebalancing agent's clock)."""
    arrivals, _, _ = arrival_schedule(store, profile, week, dc, sku, horizon=60)
    if arrivals:
        return int(arrivals[0]["day"])
    return max(1, store.net.lead_days(sku, dc) - PROCESSING_DAYS)


def daily_demand(store: Store, profile: Profile, week: int, dc: str, sku: str, use_sensing: bool = False) -> tuple[float, str]:
    dem = _last_week(store, "demand", dc, sku, week)
    if use_sensing and dem:
        try:
            from foundry.agents.sensing import logic as sensing
            sn = sensing.sense(store, profile, dc, sku, week, horizon=1)
            if sn.decision == "adjust":
                return sn.forecasts[0]["sensed"] / 7.0, "sensed"
        except Exception:
            pass
    if dem:
        return dem[-1]["forecast"] / 7.0, "consensus"
    return store.net.daily_demand(sku, dc), "weekly mean"


def project(store: Store, profile: Profile, week: int, dc: str, sku: str, horizon: int | None = None,
            extra_arrivals: list[dict] | None = None, use_sensing: bool = False) -> Projection:
    c = cfg(profile)
    net = store.net
    horizon = int(horizon or c["horizon_days"])
    use_sensing = use_sensing or bool(c.get("use_sensing", False))     # a company whose calendar knows next week's heat (risk.use_sensing)
    inv = _last_week(store, "inventory", dc, sku, week)
    if not inv:
        raise ValueError(f"no inventory snapshot for {dc} {sku} up to week {week}")
    snap = inv[-1]
    max_age = float(profile.diagnosis.get("max_snapshot_age_h", 24))
    age = float(snap.get("snapshot_age_h", 0) or 0)
    fresh = age <= max_age
    notes = []
    start = float(snap["on_hand"])
    if not fresh:
        good = [r for r in inv[:-1] if float(r.get("snapshot_age_h", 0) or 0) <= max_age]
        if good:
            start = float(good[-1]["on_hand"])
            notes.append(f"snapshot {age:.0f} h old; projecting from the last good snapshot (week {good[-1]['week']}) for display only")
    daily, source = daily_demand(store, profile, week, dc, sku, use_sensing)
    arrivals, gone, anotes = arrival_schedule(store, profile, week, dc, sku, horizon)
    notes += anotes
    start = max(0.0, start - gone)
    for a in extra_arrivals or []:
        arrivals.append({"day": int(a["day"]), "cases": float(a["cases"]), "source": a.get("source", "what-if")})
    landing = {}
    for a in arrivals:
        landing[a["day"]] = landing.get(a["day"], 0.0) + a["cases"]
    bal = start
    days, short_total, first_short, min_bal = [], 0.0, None, start
    for d in range(1, horizon + 1):
        served = min(bal, daily)
        short = daily - served
        end = bal - served + landing.get(d, 0.0)
        if short > 1e-9 and first_short is None:
            first_short = d
        short_total += short
        min_bal = min(min_bal, bal - served)
        days.append({"day": d, "start": round(bal, 1), "demand": round(daily, 1), "short": round(short, 1),
                     "arrives": round(landing.get(d, 0.0), 1), "end": round(end, 1)})
        bal = end
    ss = net.safety_stock(sku, dc)
    rop = net.reorder_point(sku, dc)
    dos0 = start / daily if daily else float("inf")
    contrib = net.contribution_per_case(sku)
    money = round(short_total * contrib)
    red_line = float(c["red_min_share_of_day"]) * daily
    if not fresh:
        risk, reason, money = "GREY", f"snapshot {age:.0f} h old against a {max_age:.0f} h limit: refresh before acting", 0
    elif short_total >= red_line:
        risk, reason = "RED", f"short from day {first_short}: {short_total:,.0f} cases over {horizon} days, {cur()} {money:,} of margin"
    elif min_bal < ss:
        risk, reason = "AMBER", f"no stockout, but the balance dips to {min_bal:,.0f} against safety stock {ss:,.0f}"
    else:
        risk, reason = "GREEN", f"covered: the balance never falls under safety stock {ss:,.0f}"
    long = dos0 > float(profile.kpis["max_dos_days"]) and fresh
    hours = 0.0 if start <= 0 else round(dos0 * 24, 1)
    from foundry.agents.rca_exception.triage import classify
    prio = classify(profile, money, hours) if risk == "RED" else "P3"
    return Projection(dc, sku, week, horizon, risk, long, round(start), age, fresh, round(daily, 2), source, round(dos0, 1),
                      round(ss), round(rop), first_short, round(short_total, 1), money, round(min_bal, 1), hours, prio,
                      profile.sla_hours(prio), sorted(arrivals, key=lambda a: a["day"]), days, notes, reason)


def what_if(store: Store, profile: Profile, week: int, dc: str, sku: str, cases: float, lane_days: int,
            source: str = "transfer", horizon: int | None = None) -> dict:
    """The same projection with a transfer that lands at the end of day lane_days."""
    before = project(store, profile, week, dc, sku, horizon)
    after = project(store, profile, week, dc, sku, horizon, extra_arrivals=[{"day": int(lane_days), "cases": cases, "source": source}])
    return {"dc": dc, "sku": sku, "week": week, "cases": round(cases), "lands_end_of_day": int(lane_days),
            "before": {"risk": before.risk, "cases_short": before.cases_short, "money_at_risk": before.money_at_risk, "first_short_day": before.first_short_day},
            "after": {"risk": after.risk, "cases_short": after.cases_short, "money_at_risk": after.money_at_risk, "first_short_day": after.first_short_day},
            "money_avoided": before.money_at_risk - after.money_at_risk, "days_after": after.days}


def earliest_rush_day(store: Store, profile: Profile, src: str, dst: str
                      ) -> tuple[int | None, str]:
    """The end of the first day a truck sent now could land: the fastest send-now carrier
    quote for the lane (carbon.expedite, wait_days 0), else the network's lane time.
    None when neither is known."""
    day0 = int(getattr(store, "dispatch_day", 0) or 0)
    quotes = (profile.section("carbon") or {}).get("expedite") or {}
    quotes = quotes.get(f"{src}-{dst}") or {}
    now = [int(q["days"]) for q in quotes.values() if not int(q.get("wait_days", 0) or 0)]
    if now:
        return day0 + min(now), f"fastest send-now quote {src}->{dst}: {min(now)} days"
    lane = store.net.lane(src, dst)
    if lane:
        return day0 + int(lane[0]), f"lane time {src}->{dst}: {int(lane[0])} days"
    return None, f"no quote and no lane {src}->{dst}"


def rescue(store: Store, profile: Profile, week: int, dc: str, sku: str,
           extra_arrivals: list[dict] | None = None) -> dict:
    """Can a rush beat the late load already on the road? (The Green Ledger's finding F1.)

    A rescue does not add stock: it lands the late load's cases earlier. So the test moves
    those cases from the day the load lands to the day the fastest rush could land, and asks
    the shared projection whether any margin is saved. helps is None when the lane's timing
    is unknown (keep the step, a person judges); False drops the step with the reason; True
    keeps it, with the money it saves."""
    before = project(store, profile, week, dc, sku, extra_arrivals=extra_arrivals)
    on_road = ("open receipt", "transfer")
    road = [a for a in before.arrivals if a["source"].startswith(on_road)]
    out = {"dc": dc, "sku": sku, "week": week, "helps": False, "money_avoided": 0,
           "late_day": None, "rush_day": None}
    if before.money_at_risk <= 0:
        return {**out, "why": "no shortage left in the projection: nothing to rescue"}
    if not road:
        return {**out, "why": "no load on the road into this DC: nothing to rescue "
                              "(a plant run or a transfer is the lever)"}
    late = road[0]
    if late["source"].startswith("transfer"):
        src = late["source"].split()[1].split("->")[0]      # "transfer KOL->CHN TR-..."
    else:
        part = store.net.parts.get(sku)
        src = part[2] if part else None              # an open receipt comes from the plant
    rush, basis = (earliest_rush_day(store, profile, src, dc) if src
                   else (None, "source unknown"))
    out.update({"late_day": int(late["day"]), "rush_day": rush, "basis": basis,
                "cases": round(late["cases"])})
    if rush is None:
        return {**out, "helps": None,
                "why": f"cannot time a rush ({basis}): a person judges"}
    if rush >= late["day"]:
        why = (f"the earliest rush lands end of day {rush} ({basis}), no earlier than the "
               f"{late['cases']:,.0f} cases already on the road (end of day "
               f"{late['day']}): no rush can help")
        return {**out, "why": why}
    moved = list(extra_arrivals or []) + [
        {"day": rush, "cases": late["cases"], "source": "rush"},
        {"day": late["day"], "cases": -late["cases"], "source": "rush (moved)"}]
    after = project(store, profile, week, dc, sku, extra_arrivals=moved)
    avoided = before.money_at_risk - after.money_at_risk
    if avoided <= 0:
        return {**out, "why": f"a rush landing end of day {rush} instead of day "
                              f"{late['day']} saves no margin: the shortage is elsewhere"}
    return {**out, "helps": True, "money_avoided": avoided,
            "why": f"a rush landing end of day {rush} instead of day {late['day']} "
                   f"saves {cur()} {avoided:,} of margin"}


def radar(store: Store, profile: Profile, week: int, classes: tuple[str, ...] = ("A", "B"), use_sensing: bool = False) -> dict:
    rows = []
    for sku, part in store.net.parts.items():
        if part[1] not in classes:
            continue
        for dc in store.net.dcs:
            if not store.rows("inventory", week=week, dc=dc, sku=sku):
                continue
            p = project(store, profile, week, dc, sku, use_sensing=use_sensing)
            rows.append(p.as_dict(with_days=False))
    order = {"RED": 0, "GREY": 1, "AMBER": 2, "GREEN": 3}
    rows.sort(key=lambda r: (order[r["risk"]], -r["money_at_risk"], r["hours_to_impact"]))
    counts = {k: sum(1 for r in rows if r["risk"] == k) for k in order}
    c = cfg(profile)
    pre_empt = [r for r in rows if r["risk"] == "RED" and r["money_at_risk"] >= float(c["pre_empt_min_inr"])]
    return {"week": week, "counts": counts, "money_at_risk": sum(r["money_at_risk"] for r in rows if r["risk"] == "RED"),
            "pre_empt": [{"dc": r["dc"], "sku": r["sku"], "money_at_risk": r["money_at_risk"], "first_short_day": r["first_short_day"]} for r in pre_empt],
            "long": [{"dc": r["dc"], "sku": r["sku"], "dos0": r["dos0"]} for r in rows if r["long"]], "rows": rows}
