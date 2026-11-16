"""The rebalancing arithmetic. Everything a planner would do on paper for one SKU:

position    on hand, open receipt, days of supply, reorder point, short / long / ok, fresh or stale
need        cases to top the short DC up to target cover (open receipts count as cover)
surplus     cases a long DC can spare: above its long line without a co-sign, down to its ROP with one
lane        cheapest inter-DC lane that lands before the receiver runs dry (plus a tolerance), else the fastest
economics   lane cost + holding against the margin saved on the days of shortage avoided

Reproduces F.11 on the opening position: KOL -> CHN, 3,720 cases, 4 days, 81,840 lane cost,
184,229 margin saved. The model never computes any of this; it reads the result and explains it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field

from foundry.profile import Profile
from foundry.store import Store
from foundry.risk import projection as clock

PROCESSING_DAYS = 2      # the plant's own truck lands two days before the quoted lead time ends
RECEIPT_LANDS_IN = 2     # an open receipt (a slipped truck) lands first thing: two days


def _cfg(profile: Profile) -> dict:
    base = {"target_cover_days": 14, "min_transfer_cases": 200, "arrival_tolerance_days": 1, "sender_floor": "rop", "fresh_only": True}
    base.update(profile.raw.get("rebalance", {}))
    return base


def position(store: Store, profile: Profile, week: int, sku: str) -> list[dict]:
    """One row per DC for this SKU in this week, from the inventory table and the network."""
    net = store.net
    max_age = float(profile.diagnosis.get("max_snapshot_age_h", 24))
    lo, hi = float(profile.kpis["min_dos_days"]), float(profile.kpis["max_dos_days"])
    rows = []
    for dc in net.dcs:
        inv = store.rows("inventory", week=week, dc=dc, sku=sku)
        if not inv:
            continue
        r = inv[-1]
        daily = net.daily_demand(sku, dc)
        on_hand, open_receipt, age = float(r["on_hand"]), float(r.get("open_receipt", 0) or 0), float(r.get("snapshot_age_h", 0) or 0)
        moving, gone = clock.inflight(store, week, dc, sku)       # the tower's own transfers, not yet in a snapshot
        on_hand = max(0.0, on_hand - gone)
        open_receipt += sum(a["cases"] for a in moving)
        dos = on_hand / daily if daily else float("inf")
        rop = net.reorder_point(sku, dc)
        ss = net.safety_stock(sku, dc)
        status = "short" if dos < lo else ("long" if dos > hi else "ok")
        # one clock: the radar's arrival schedule (open receipt or carrier ETA, the plant's plan, tower transfers)
        days_to_next_receipt = clock.next_receipt_day(store, profile, week, dc, sku)
        rows.append({
            "dc": dc, "sku": sku, "week": week, "on_hand": round(on_hand), "open_receipt": round(open_receipt),
            "snapshot_age_h": age, "fresh": age <= max_age, "daily": round(daily, 2), "dos": round(dos, 1),
            "rop": round(rop), "ss": round(ss), "status": status, "days_to_next_receipt": days_to_next_receipt,
            "long_line_cases": round(hi * daily), "short_line_cases": round(lo * daily),
        })
    return rows


def need(row: dict, profile: Profile) -> float:
    """Cases to bring the receiver up to target cover, counting what is already on its way."""
    cfg = _cfg(profile)
    target = cfg["target_cover_days"] * row["daily"]
    return max(0.0, target - row["on_hand"] - row["open_receipt"])


def surplus(row: dict, profile: Profile) -> tuple[float, float]:
    """(soft, hard): soft = above the long line, no co-sign; hard = down to the reorder point, with one."""
    hi = float(profile.kpis["max_dos_days"])
    soft = max(0.0, row["on_hand"] - hi * row["daily"])
    hard = max(0.0, row["on_hand"] - row["rop"])
    return soft, hard


@dataclass
class Transfer:
    from_dc: str
    to_dc: str
    sku: str
    week: int
    cases: int
    lane_days: int
    cost_per_case: float
    lane_cost: int
    holding_cost: int
    days_short_avoided: float
    margin_saved: int
    pays: bool
    arrives_in_time: bool
    receiver_dos_before: float
    receiver_dos_after: float
    leaves_sender_cases: int
    leaves_sender_dos: float
    co_sign_needed: bool
    need_cases: int
    surplus_cases: int           # the soft surplus the book quotes (6,300 in F.11)
    surplus_hard_cases: int
    summary: str
    approvals: list[dict] = field(default_factory=list)
    lands_end_of_day: int = 0    # dispatch day (the tower's clock) + lane days: the day the cases can be sold

    def as_dict(self) -> dict:
        return asdict(self)


def economics(store: Store, profile: Profile, week: int, sku: str, from_dc: str, to_dc: str, cases: int,
              rows: list[dict] | None = None) -> dict:
    """Cost of the move against the margin it saves. The margin is priced on the shared projection
    (risk.projection.what_if): the money the receiver stops losing, day by day, when the cases land
    at the end of day `lands`. Before 8 Nov it was priced on one rule (days to the next receipt less
    days of cover), which missed a shortage that comes back after a small slipped truck runs out
    (The Control Room, "what integration found"). F.11 is the same either way: INR 1,84,229."""
    net = store.net
    rows = rows or position(store, profile, week, sku)
    by = {r["dc"]: r for r in rows}
    if from_dc not in by or to_dc not in by:
        return {"error": f"no position for {from_dc} or {to_dc}"}
    lane = net.lane(from_dc, to_dc)
    if not lane:
        return {"error": f"no lane {from_dc}->{to_dc}"}
    rec, snd = by[to_dc], by[from_dc]
    transit, cpc = lane
    cpc = cpc * net.space(sku)                    # a keg pays for the truck room it takes (1 for every GFB SKU)
    transit += clock.lane_delay(store, week, from_dc, to_dc)[0]
    lands = int(getattr(store, "dispatch_day", 0) or 0) + transit     # a truck sent on Tuesday lands a day later
    cases = int(round(cases))
    lane_cost = cases * cpc
    holding = cases * net.holding_cost_per_case_day(sku) * transit
    contrib = net.contribution_per_case(sku)
    # a transfer only helps from the day it can be on the shelf, and only against shortage that is
    # still there after every receipt the schedule knows about: both come out of the projection
    wi = clock.what_if(store, profile, week, to_dc, sku, cases, lands, source=f"transfer {from_dc}->{to_dc}")
    daily = rec["daily"] or 1.0
    days_short = wi["before"]["cases_short"] / daily
    days_avoided = max(0.0, (wi["before"]["cases_short"] - wi["after"]["cases_short"]) / daily)
    margin = max(0.0, float(wi["money_avoided"]))
    leaves = snd["on_hand"] - cases
    cfg = _cfg(profile)
    soft, hard = surplus(snd, profile)
    co_sign = cases > soft or leaves < snd["rop"]
    arrives = transit <= rec["dos"] + cfg["arrival_tolerance_days"]
    return {
        "from_dc": from_dc, "to_dc": to_dc, "sku": sku, "week": week, "cases": cases, "lane_days": transit, "lands_end_of_day": lands,
        "cost_per_case": cpc, "lane_cost": round(lane_cost), "holding_cost": round(holding),
        "days_short_if_nothing_done": round(days_short, 1), "days_short_avoided": round(days_avoided, 1),
        "margin_saved": round(margin), "pays": margin > lane_cost + holding, "arrives_in_time": arrives,
        "receiver_dos_before": rec["dos"], "receiver_dos_after": round((rec["on_hand"] + rec["open_receipt"] + cases) / rec["daily"], 1) if rec["daily"] else None,
        "leaves_sender_cases": round(leaves), "leaves_sender_dos": round(leaves / snd["daily"], 1) if snd["daily"] else None,
        "co_sign_needed": bool(co_sign), "sender_surplus_cases": round(soft), "sender_surplus_hard_cases": round(hard),
        "sender_rop": snd["rop"], "contribution_per_case": contrib,
        "radar_before_inr": wi["before"]["money_at_risk"], "radar_after_inr": wi["after"]["money_at_risk"],
    }


def lane_options(store: Store, profile: Profile, week: int, sku: str, to_dc: str, rows: list[dict] | None = None) -> dict:
    """Candidate senders for one receiver: fresh, long, with a lane; what each can spare and when it lands."""
    net = store.net
    cfg = _cfg(profile)
    rows = rows or position(store, profile, week, sku)
    by = {r["dc"]: r for r in rows}
    rec = by.get(to_dc)
    if not rec:
        return {"to_dc": to_dc, "candidates": [], "excluded": [{"dc": to_dc, "why": "no position"}]}
    cands, excluded = [], []
    for from_dc, transit, cpc in net.dc_lanes_into(to_dc):
        cpc = cpc * net.space(sku)
        transit += clock.lane_delay(store, week, from_dc, to_dc)[0]     # a delay the listener heard about
        lands = int(getattr(store, "dispatch_day", 0) or 0) + transit
        snd = by.get(from_dc)
        if not snd:
            continue
        soft, hard = surplus(snd, profile)
        if cfg["fresh_only"] and not snd["fresh"]:
            excluded.append({"dc": from_dc, "why": f"snapshot {snd['snapshot_age_h']} h old (limit {profile.diagnosis.get('max_snapshot_age_h', 24)} h)"})
            continue
        if snd["status"] != "long":
            excluded.append({"dc": from_dc, "why": f"not long: {snd['dos']} days of cover (long above {profile.kpis['max_dos_days']})"})
            continue
        if hard < cfg["min_transfer_cases"]:
            excluded.append({"dc": from_dc, "why": f"can spare only {round(hard)} cases (minimum {cfg['min_transfer_cases']})"})
            continue
        cands.append({"from_dc": from_dc, "lane_days": transit, "cost_per_case": cpc, "sender_dos": snd["dos"],
                      "surplus_cases": round(soft), "surplus_hard_cases": round(hard),
                      "arrives_in_time": lands <= rec["dos"] + cfg["arrival_tolerance_days"],
                      "lands_before_next_receipt": lands < rec["days_to_next_receipt"], "lands_end_of_day": lands})
    for dc, r in by.items():
        if dc != to_dc and dc not in {c["from_dc"] for c in cands} and dc not in {e["dc"] for e in excluded}:
            excluded.append({"dc": dc, "why": f"no lane {dc}->{to_dc}"})
    if cands:
        # what the shared projection says is still short after each lane could be on the shelf
        base = clock.project(store, profile, week, to_dc, sku)
        red_line = float(clock.cfg(profile)["red_min_share_of_day"]) * rec["daily"]
        for c in cands:
            c["short_after_landing"] = round(sum(d["short"] for d in base.days if d["day"] > c["lands_end_of_day"]), 1)
            c["useful"] = c["short_after_landing"] >= red_line
    cands.sort(key=lambda c: (c["cost_per_case"], c["lane_days"]))
    return {"to_dc": to_dc, "receiver_dos": rec["dos"], "receiver_status": rec["status"], "receiver_fresh": rec["fresh"],
            "days_to_next_receipt": rec["days_to_next_receipt"], "need_cases": round(need(rec, profile)),
            "candidates": cands, "excluded": excluded}


def plan_transfer(store: Store, profile: Profile, week: int, sku: str, to_dc: str | None = None) -> dict:
    """The whole decision for one SKU: {"transfer": Transfer-as-dict | None, "reason": str, "position": rows, ...}."""
    cfg = _cfg(profile)
    rows = position(store, profile, week, sku)
    if not rows:
        return {"transfer": None, "reason": f"no inventory rows for {sku} in week {week}", "position": rows}
    by = {r["dc"]: r for r in rows}
    if to_dc is None:
        shorts = [r for r in rows if r["status"] == "short"]
        if not shorts:
            return {"transfer": None, "reason": f"no DC is short of {sku}: nothing to rebalance", "position": rows, "alternative": None}
        rec = min(shorts, key=lambda r: r["dos"])
        to_dc = rec["dc"]
    rec = by.get(to_dc)
    if rec is None:
        return {"transfer": None, "reason": f"no position for {to_dc}", "position": rows}
    if cfg["fresh_only"] and not rec["fresh"]:
        return {"transfer": None, "reason": f"{to_dc}'s snapshot is {rec['snapshot_age_h']} h old: refresh it before moving stock towards it",
                "position": rows, "alternative": "refresh_snapshot"}
    if rec["status"] != "short":
        return {"transfer": None, "reason": f"{to_dc} is not short: {rec['dos']} days of cover (short below {profile.kpis['min_dos_days']})",
                "position": rows, "alternative": None}
    nd = need(rec, profile)
    if nd < 1:
        return {"transfer": None, "reason": f"{to_dc} is short on hand but the open receipt of {rec['open_receipt']} cases lands in {rec['days_to_next_receipt']} days and covers target cover",
                "position": rows, "alternative": None}
    opts = lane_options(store, profile, week, sku, to_dc, rows)
    cands = opts["candidates"]
    if not cands:
        stale = [e for e in opts["excluded"] if "snapshot" in e["why"]]
        shortish = [r["dc"] for r in rows if r["status"] == "short"]
        reason = (f"no fresh DC is long of {sku} with a lane into {to_dc}: " + "; ".join(f"{e['dc']} {e['why']}" for e in opts["excluded"])
                  if opts["excluded"] else f"no other DC carries {sku}: it is sold from {to_dc} alone (a pack for one market)")
        alt = "refresh_snapshot" if stale else "expedite_plant_run"
        return {"transfer": None, "reason": reason, "position": rows, "alternative": alt, "short_dcs": shortish, "excluded": opts["excluded"]}
    landing_first = [c for c in cands if c["useful"]]           # something is still short after it lands
    if not landing_first:
        first = min(c["lands_end_of_day"] for c in cands)
        return {"transfer": None, "reason": f"{to_dc}'s receipts cover every day after the earliest a lane could land (end of day {first}); "
                                            f"next truck in {rec['days_to_next_receipt']} days: wait for the truck",
                "position": rows, "alternative": "expedite_alternate_carrier" if rec["open_receipt"] else "expedite_plant_run", "candidates": cands}
    in_time = [c for c in landing_first if c["arrives_in_time"]]
    pick = in_time[0] if in_time else min(landing_first, key=lambda c: (c["lane_days"], c["cost_per_case"]))
    # one clock (8 Nov, found running a brewer): need() counts an open receipt as if it were on the shelf;
    # when that receipt lands after the transfer could (a ferry shut for a week), the projection's gap is
    # the real need, rounded up to 50
    gap = int(math.ceil(pick["short_after_landing"] / 50.0) * 50)
    if gap > nd:
        nd = float(gap)
    cases = int(round(min(nd, pick["surplus_hard_cases"])))
    if cases < cfg["min_transfer_cases"]:
        return {"transfer": None, "reason": f"the move would be {cases} cases, below the minimum of {cfg['min_transfer_cases']}", "position": rows, "alternative": None, "candidates": cands}
    eco = economics(store, profile, week, sku, pick["from_dc"], to_dc, cases, rows)
    snd = by[pick["from_dc"]]
    if eco["leaves_sender_dos"] is not None and eco["leaves_sender_dos"] < float(profile.kpis["min_dos_days"]):
        return {"transfer": None, "reason": f"the move would leave {pick['from_dc']} at {eco['leaves_sender_dos']} days, below the short line", "position": rows, "alternative": None, "candidates": cands}
    sizing = "target cover"
    if not eco["pays"]:
        # Topping up to target cover can cost more than it saves when the plant's truck is close: only
        # the days between the transfer's arrival and the next receipt can be saved. Try the smallest
        # move that saves all of them (rounded up to 50 cases) before giving up.
        gap_cases = int(math.ceil(pick["short_after_landing"] / 50.0) * 50)
        if cfg["min_transfer_cases"] <= gap_cases < cases:
            eco_gap = economics(store, profile, week, sku, pick["from_dc"], to_dc, gap_cases, rows)
            if eco_gap["pays"]:
                cases, eco, sizing = gap_cases, eco_gap, "the gap to the next receipt"
    if not eco["pays"]:
        return {"transfer": None, "reason": f"the move does not pay: margin saved {eco['margin_saved']} against lane {eco['lane_cost']} + holding {eco['holding_cost']}",
                "position": rows, "alternative": None, "economics": eco, "candidates": cands}
    approvals = [{"code": "inter_dc_transfer", "approver": profile.approver_for("inter_dc_transfer", to_dc)}]
    if eco["co_sign_needed"]:
        approvals.append({"code": "co_sign_transfer", "approver": profile.approver_for("co_sign_transfer", pick["from_dc"])})
    for ls in profile.plugins.lane_steps(store.net, profile, pick["from_dc"], to_dc):     # paperwork this lane needs (a plug-in)
        approvals.append({"code": ls["code"], "approver": profile.approver_for(ls["code"], pick["from_dc"]), "text": ls["text"]})
    approvals.append({"code": "book_truck", "approver": profile.approver_for("book_truck", to_dc)})
    t = Transfer(
        from_dc=pick["from_dc"], to_dc=to_dc, sku=sku, week=week, cases=cases, lane_days=eco["lane_days"],
        cost_per_case=eco["cost_per_case"], lane_cost=eco["lane_cost"], holding_cost=eco["holding_cost"],
        days_short_avoided=eco["days_short_avoided"], margin_saved=eco["margin_saved"], pays=eco["pays"],
        arrives_in_time=eco["arrives_in_time"], receiver_dos_before=rec["dos"], receiver_dos_after=eco["receiver_dos_after"],
        leaves_sender_cases=eco["leaves_sender_cases"], leaves_sender_dos=eco["leaves_sender_dos"], co_sign_needed=eco["co_sign_needed"],
        need_cases=round(nd), surplus_cases=eco["sender_surplus_cases"], surplus_hard_cases=eco["sender_surplus_hard_cases"],
        summary=(f"Move {cases} cases of {sku} {pick['from_dc']}->{to_dc} ({eco['lane_days']} days, {store.net.currency} {eco['lane_cost']:,}): "
                 f"{to_dc} holds {rec['dos']} days, {pick['from_dc']} holds {snd['dos']}; saves {store.net.currency} {eco['margin_saved']:,} "
                 f"over {eco['days_short_avoided']} short days" + (("; sender co-signs (it goes below its reorder point)" if eco["leaves_sender_cases"] < eco["sender_rop"]
                                                      else "; sender co-signs (it goes below its long line)") if eco["co_sign_needed"] else "")),
        approvals=approvals, lands_end_of_day=eco["lands_end_of_day"],
    )
    reason = "short here, long there, the lane lands in time and the move pays"
    if sizing != "target cover":
        reason += f" (sized to {sizing}: topping up to target cover would not pay)"
    return {"transfer": t.as_dict(), "reason": reason, "sizing": sizing, "position": rows, "candidates": cands}


def list_shortages(store: Store, profile: Profile, week: int, classes: tuple[str, ...] = ("A", "B")) -> list[dict]:
    """Sweep: every A/B SKU with a short DC, worst first, with whether a fresh long partner exists."""
    out = []
    for sku, spec in store.net.parts.items():
        if spec[1] not in classes:
            continue
        rows = position(store, profile, week, sku)
        shorts = [r for r in rows if r["status"] == "short"]
        if not shorts:
            continue
        rec = min(shorts, key=lambda r: r["dos"])
        plan = plan_transfer(store, profile, week, sku, rec["dc"])
        t = plan.get("transfer")
        out.append({"sku": sku, "dc": rec["dc"], "dos": rec["dos"], "need_cases": round(need(rec, profile)),
                    "partner": t["from_dc"] if t else None, "cases": t["cases"] if t else 0,
                    "margin_saved": t["margin_saved"] if t else 0, "reason": plan["reason"]})
    out.sort(key=lambda r: (-r["margin_saved"], r["dos"]))
    return out
