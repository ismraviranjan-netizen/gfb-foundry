"""Exception management: how much it costs, how urgent it is, what to do, who signs.

impact      cases affected x contribution per case (+ contractual penalty per late case, if any)
urgency     hours until the DC is out: days of supply x 24 (0 if it is already out)
priority    P1 / P2 / P3 from the profile's thresholds; each carries an SLA in hours
playbook    the ordered actions for the cause; every action names its approver from the profile

The agent proposes; a named person approves; nothing is executed by code in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field

from foundry.profile import Profile
from foundry.store import Store
from foundry.agents.rca_exception.diagnosis import Diagnosis
from foundry.agents.rca_exception.kpis import days_of_supply

PLAYBOOKS = {
    "TRANSPORT_DELAY": ["expedite_alternate_carrier", "inter_dc_transfer", "inform_customers"],
    "DOCK_CONGESTION": ["reschedule_dock_slots", "extend_shift", "inform_customers"],
    "SUPPLY_MISS": ["expedite_plant_run", "allocate_fair_share", "inter_dc_transfer"],
    "DEMAND_SPIKE": ["inter_dc_transfer", "raise_production_lot", "promo_cap"],
    "INVENTORY_IMBALANCE": ["inter_dc_transfer"],
    "DATA_STALE": ["refresh_snapshot", "no_operational_action"],
    "NO_CLEAR_CAUSE": ["no_operational_action"],
}
ACTION_TEXT = {
    "expedite_alternate_carrier": "Book the slipped cases with an alternate carrier on an express lane",
    "inter_dc_transfer": "Move cases from the long DC to the short DC on the cheapest lane that arrives before the next receipt",
    "inform_customers": "Tell the affected customers the new delivery dates before they call",
    "reschedule_dock_slots": "Re-slot inbound and outbound trucks so the dock clears the backlog",
    "extend_shift": "Add a shift at the dock until dwell is back under baseline",
    "expedite_plant_run": "Ask the plant for the missing run or a partial run in the next schedule window",
    "allocate_fair_share": "Allocate what is on hand across customers by priority rather than first come first served",
    "raise_production_lot": "Raise the next MPS lot for this SKU to rebuild cover",
    "promo_cap": "Cap the promotion or move its stock from DCs with no promo",
    "refresh_snapshot": "Refresh the inventory snapshot from the source system and re-run the monitor",
    "no_operational_action": "No operational action; keep watching next week's numbers",
}


@dataclass
class Action:
    code: str
    text: str
    approver: str


@dataclass
class Triage:
    dc: str
    sku: str | None
    week: int
    root_cause: str
    cases_affected: int
    impact_inr: int
    hours_to_impact: float
    priority: str
    sla_hours: int
    actions: list[Action] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def estimate_impact(store: Store, profile: Profile, week: int, dc: str, sku: str | None) -> dict:
    """Cases affected this week (short or late), money at stake, and hours until stock runs out."""
    orders = [r for r in store.rows("orders", dc=dc, week=week) if sku is None or r["sku"] == sku]
    short = sum(max(0, r["requested"] - r["shipped"]) for r in orders)
    late = sum(r["shipped"] for r in orders if not r["on_time"])
    penalty = float(profile.triage.get("penalty_per_late_case_inr", 0))
    if sku:
        contrib = store.net.contribution_per_case(sku)
        dos = days_of_supply(store, week, dc, sku)
        inv = store.rows("inventory", week=week, dc=dc, sku=sku)
        open_receipt = inv[0]["open_receipt"] if inv else 0
        lead_days = store.net.lead_days(sku, dc)
        days_to_receipt = 2 if open_receipt > 0 else lead_days      # slipped trucks land first thing next week
        daily = store.net.daily_demand(sku, dc)
        on_hand = inv[0]["on_hand"] if inv else 0
        at_risk = max(0.0, daily * days_to_receipt - on_hand)
    else:
        contrib = sum(store.net.contribution_per_case(s) for s in store.net.parts) / len(store.net.parts)
        dos = float("nan")
        at_risk = 0.0
        days_to_receipt = 0
    cases = int(round(short + 0.5 * late + at_risk))     # a late case costs about half a lost case in margin
    impact = int(round(short * contrib + late * (0.5 * contrib + penalty) + at_risk * contrib))
    hours = 0.0 if (sku and dos <= 0) else (round(dos * 24, 1) if sku else 24.0)
    return {"cases_short": int(short), "cases_late": int(late), "cases_at_risk": int(round(at_risk)),
            "cases_affected": cases, "impact_inr": impact, "hours_to_impact": hours,
            "days_to_next_receipt": days_to_receipt, "contribution_per_case": contrib}


def classify(profile: Profile, impact_inr: float, hours_to_impact: float) -> str:
    classes = profile.triage["classes"]
    dos_days = hours_to_impact / 24.0
    for p in ("P1", "P2"):
        c = classes[p]
        if dos_days <= float(c.get("max_dos_days", 0)) or impact_inr >= float(c.get("min_impact_inr", float("inf"))):
            return p
    return "P3"


def triage(store: Store, profile: Profile, diag: Diagnosis) -> Triage:
    imp = estimate_impact(store, profile, diag.week, diag.dc, diag.sku)
    cause = diag.primary
    codes = list(profile.plugins.playbook_for(cause) or PLAYBOOKS.get(cause, ["no_operational_action"]))
    # a transport delay with a known surplus elsewhere: the transfer is the faster fix, so it goes first
    if cause == "TRANSPORT_DELAY" and "INVENTORY_IMBALANCE" in diag.contributing:
        codes = ["inter_dc_transfer", "expedite_alternate_carrier", "inform_customers"]
    if cause == "DEMAND_SPIKE" and "INVENTORY_IMBALANCE" not in diag.contributing:
        codes = ["raise_production_lot", "allocate_fair_share", "promo_cap"]
    own = (profile.section("playbooks") or {}).get(cause)                    # a company's own playbook for a core cause
    if own:
        codes = list(own)                                                     # (a brewer does not cap a promotion in a heatwave)
        if "INVENTORY_IMBALANCE" in diag.contributing and "inter_dc_transfer" not in codes:
            codes.insert(0, "inter_dc_transfer")
    codes = profile.plugins.apply_steps(cause, codes)                       # Part 6: e.g. QA release before a transfer
    text = {**ACTION_TEXT, **profile.plugins.action_text()}
    actions = [Action(c, text.get(c, c), profile.approver_for(c, diag.dc)) for c in codes]
    if cause in ("DATA_STALE", "NO_CLEAR_CAUSE"):
        prio, sla = "P3", profile.sla_hours("P3")
        hours = 72.0
    else:
        hours = profile.plugins.hours_to_impact(store, profile, diag.week, diag.dc, diag.sku, imp["hours_to_impact"])
        prio = classify(profile, imp["impact_inr"], hours)
        sla = profile.sla_hours(prio)
    notes = []
    if diag.sku and diag.contributing:
        notes.append("contributing: " + ", ".join(diag.contributing))
    if cause == "DATA_STALE":
        notes.append("impact set aside until the snapshot is refreshed; the KPI may be fine")
    return Triage(diag.dc, diag.sku, diag.week, cause, imp["cases_affected"], imp["impact_inr"] if cause not in ("DATA_STALE", "NO_CLEAR_CAUSE") else 0,
                  hours, prio, sla, actions, notes)
