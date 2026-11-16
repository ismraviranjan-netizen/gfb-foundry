"""Exception management as a queue: the week's deviations become a short, ordered list of
exceptions, each with a cause, the money, a priority, an SLA clock, named approvers, a state and a
history. Everything in this module is deterministic: grouping, ordering, reminders, escalation and
the digest numbers are arithmetic and lookups from the profile. The model (queue_agent.py) only
writes the digest's words and chooses the order of the three headlines.

Grouping rules
  dedupe    the same DC and SKU moving on several KPIs is one exception; the headline KPI is the one
            the tree is surest about (highest confidence), the other KPIs ride along in kpis_moved
  DC-wide   a DC-level cause in profile.queue.group_dc_level_causes (dock, stale data) owns the
            SKU-level exceptions at that DC with the same cause as children
  roll-up   a DC-level deviation explained by one SKU's cause (late trucks for mango nectar) rolls
            up into that SKU's exception as a child

Order: priority (P1 first), then money at stake, then hours to impact.

States: NEW -> DIAGNOSED -> PROPOSED -> APPROVED | REJECTED -> APPLIED | CLOSED. ESCALATED is an
overlay: the SLA passed with no answer, the escalation role from the profile joins the approvers,
the original approver stays.

Clock: hours since the sweep (Monday 06:30 is hour 0). tick(now) sends the reminder at
reminder_at_pct x SLA and escalates at 1.0 x SLA; both are written into the item's history.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from foundry.profile import Profile
from foundry.store import Store
from foundry.approval import _system_for
from foundry.agents.rca_exception import diagnosis, kpis, triage

PRIORITY_RANK = {"P1": 0, "P2": 1, "P3": 2}
OPEN_STATES = ("NEW", "DIAGNOSED", "PROPOSED")
TERMINAL_STATES = ("APPLIED", "CLOSED", "REJECTED")
TRANSITIONS = {
    "NEW": {"DIAGNOSED"},
    "DIAGNOSED": {"PROPOSED", "CLOSED"},
    "PROPOSED": {"APPROVED", "REJECTED"},
    "APPROVED": {"APPLIED", "CLOSED"},
    "REJECTED": {"CLOSED"},
    "APPLIED": set(),
    "CLOSED": set(),
}


@dataclass
class ExceptionItem:
    """One line in the planner's queue."""
    id: str
    week: int
    dc: str
    sku: str | None
    kpi: str                      # the headline KPI
    kpis_moved: list[str]
    root_cause: str
    confidence: float
    priority: str
    impact_inr: int
    hours_to_impact: float
    sla_hours: int
    cases_affected: int
    evidence: list[str]
    actions: list[dict]           # [{code, text, approver}]
    approvers: list[str]
    children: list[str] = field(default_factory=list)      # every deviation key this exception explains
    grouped: list[dict] = field(default_factory=list)      # SKU-level exceptions folded under a DC-wide cause
    grouped_impact_inr: int = 0
    state: str = "NEW"
    escalated: bool = False
    opened_at: float = 0.0
    due_at: float = 0.0
    reminded_at: float | None = None
    escalated_at: float | None = None
    decided_by: str | None = None
    decision_note: str = ""
    proposal_id: str | None = None
    history: list[dict] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.dc}:{self.sku or 'ALL'}"

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_STATES

    def overdue_hours(self, now_hours: float) -> float:
        return round(max(0.0, now_hours - self.due_at), 1) if self.is_open else 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d

    def line(self) -> str:
        who = self.approvers[0] if self.approvers else "nobody"
        return (f"{self.id} {self.priority} {self.dc} {self.sku or 'all SKUs'}: {self.root_cause.replace('_', ' ').lower()}, "
                f"{self.impact_inr:,} at stake, {self.hours_to_impact:.0f} h to impact, SLA {self.sla_hours} h, {who}")


@dataclass
class Queue:
    week: int
    company: str
    items: list[ExceptionItem]
    built_from: int               # deviations the monitor raised
    log: list[dict] = field(default_factory=list)

    def get(self, item_id: str) -> ExceptionItem:
        for it in self.items:
            if it.id == item_id:
                return it
        raise KeyError(item_id)

    def open_items(self) -> list[ExceptionItem]:
        return [it for it in self.items if it.is_open]

    def as_dict(self) -> dict:
        return {"week": self.week, "company": self.company, "built_from": self.built_from,
                "items": [it.as_dict() for it in self.items], "log": self.log}

    def table(self, now_hours: float | None = None) -> list[dict]:
        rows = []
        for it in self.items:
            rows.append({"id": it.id, "dc": it.dc, "sku": it.sku, "kpi": it.kpi, "root_cause": it.root_cause,
                         "priority": it.priority, "impact_inr": it.impact_inr, "hours_to_impact": it.hours_to_impact,
                         "due_at": it.due_at, "state": it.state, "escalated": it.escalated, "children": len(it.children), "grouped": len(it.grouped),
                         "approvers": it.approvers,
                         **({"overdue_hours": it.overdue_hours(now_hours)} if now_hours is not None else {})})
        return rows


# --- building ----------------------------------------------------------------------------------
def _transition(it: ExceptionItem, new_state: str, now_hours: float, note: str) -> None:
    if new_state not in TRANSITIONS[it.state]:
        raise ValueError(f"{it.id}: cannot go from {it.state} to {new_state}")
    it.history.append({"at": now_hours, "from": it.state, "to": new_state, "note": note})
    it.state = new_state


def build_queue(store: Store, profile: Profile, week: int, opened_at: float = 0.0, propose: bool = True,
                proposals=None, run_id: str = "queue") -> Queue:
    """Monitor -> diagnose -> triage every deviation, then dedupe, group, order, and propose."""
    devs = kpis.deviations(store, profile, week)
    # 1. diagnose and triage each deviation once
    diag_of: dict[str, diagnosis.Diagnosis] = {}
    tri_of: dict[str, triage.Triage] = {}
    for d in devs:
        g = diagnosis.gather(store, profile, week, d.dc, d.sku, d.kpi)
        diag_of[d.key] = g
        tri_of[d.key] = triage.triage(store, profile, g)

    # 2. dedupe by (dc, sku): the headline is the KPI the tree is surest about; ties keep the monitor's order
    by_key: dict[str, list] = {}
    for d in devs:
        by_key.setdefault(f"{d.dc}:{d.sku or 'ALL'}", []).append(d)
    items: dict[str, ExceptionItem] = {}
    for key, ds in by_key.items():
        head = max(ds, key=lambda d: (diag_of[d.key].confidence, -ds.index(d)))
        g, t = diag_of[head.key], tri_of[head.key]
        items[key] = ExceptionItem(
            id="", week=week, dc=head.dc, sku=head.sku, kpi=head.kpi, kpis_moved=[d.kpi for d in ds],
            root_cause=g.primary, confidence=g.confidence, priority=t.priority, impact_inr=t.impact_inr,
            hours_to_impact=t.hours_to_impact, sla_hours=t.sla_hours, cases_affected=t.cases_affected,
            evidence=[e.summary for e in g.evidence if e.score >= 0.4][:4] or [g.evidence[0].summary if g.evidence else "nothing scored"],
            actions=[asdict(a) for a in t.actions], approvers=_unique(a.approver for a in t.actions),
            children=[d.key for d in ds if d.key != head.key], opened_at=opened_at, due_at=opened_at + t.sla_hours,
        )
        items[key].history.append({"at": opened_at, "from": None, "to": "NEW", "note": f"raised by the monitor on {', '.join(d.kpi for d in ds)}"})

    # 3. group: DC-wide causes own their SKU-level children; SKU-specific causes absorb the DC-level KPI moves
    dc_wide = set(profile.raw.get("queue", {}).get("group_dc_level_causes", ["DOCK_CONGESTION", "DATA_STALE"]))
    absorbed: set[str] = set()
    for key, it in list(items.items()):
        if it.sku is not None:
            continue
        if it.root_cause in dc_wide:
            for k2, child in items.items():
                if k2 != key and child.dc == it.dc and child.sku is not None and child.root_cause == it.root_cause and k2 not in absorbed:
                    it.children += [f"{child.kpi}:{child.dc}:{child.sku}:w{week}"] + child.children
                    it.grouped.append({"key": k2, "sku": child.sku, "kpi": child.kpi, "impact_inr": child.impact_inr, "priority": child.priority})
                    it.grouped_impact_inr += child.impact_inr
                    absorbed.add(k2)
        else:
            named = _named_sku(diag_of, it)
            host = None
            if named and f"{it.dc}:{named}" in items and items[f"{it.dc}:{named}"].root_cause == it.root_cause:
                host = items[f"{it.dc}:{named}"]
            else:
                for k2, cand in items.items():
                    if cand.dc == it.dc and cand.sku is not None and cand.root_cause == it.root_cause and k2 not in absorbed:
                        host = cand
                        break
            if host is not None:
                host.children += [f"{it.kpi}:{it.dc}:ALL:w{week}"] + it.children
                host.kpis_moved = _unique(host.kpis_moved + [f"{k}@DC" for k in it.kpis_moved])
                absorbed.add(key)
    ordered = [it for k, it in items.items() if k not in absorbed]

    # 4. order and number
    ordered.sort(key=lambda it: (PRIORITY_RANK.get(it.priority, 9), -it.impact_inr, it.hours_to_impact))
    for n, it in enumerate(ordered, 1):
        it.id = f"EXC-w{week}-{n:03d}"
        _transition(it, "DIAGNOSED", opened_at, f"{it.root_cause} at confidence {it.confidence}")

    # 5. propose: every exception with an operational action is a proposal waiting for a named person
    q = Queue(week=week, company=profile.company, items=ordered, built_from=len(devs))
    if propose:
        for it in ordered:
            codes = [a["code"] for a in it.actions]
            if codes == ["no_operational_action"]:
                _transition(it, "CLOSED", opened_at, "nothing to act on; watch next week")
                continue
            if proposals is not None:
                row = proposals.propose(it.dc, it.sku, week, it.root_cause, it.actions, "; ".join(it.evidence), run_id,
                                        kind="exception", payload={"exception_id": it.id, "priority": it.priority, "due_at": it.due_at},
                                        agent="rca_exception.queue")
                it.proposal_id = row["proposal_id"]
            else:
                it.proposal_id = f"PRP-{week:02d}-{it.dc}-{it.id[-3:]}"
            _transition(it, "PROPOSED", opened_at, f"proposed to {', '.join(it.approvers)}; due at {it.due_at:.0f} h")
    q.log.append({"at": opened_at, "event": "built", "deviations": len(devs), "exceptions": len(ordered)})
    return q


def _named_sku(diag_of: dict, it: ExceptionItem) -> str | None:
    for key, g in diag_of.items():
        if g.dc == it.dc and g.sku is None and g.primary == it.root_cause:
            ev = next((e for e in g.evidence if e.cause == g.primary), None)
            if ev and ev.facts.get("sku"):
                return ev.facts["sku"]
    return None


def _unique(xs) -> list:
    out = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


# --- the clock ------------------------------------------------------------------------------------
def tick(queue: Queue, profile: Profile, now_hours: float) -> dict:
    """Advance the clock: reminders at reminder_at_pct x SLA, escalation at the SLA. Idempotent."""
    qcfg = profile.raw.get("queue", {})
    remind_pct = float(qcfg.get("reminder_at_pct", 0.5))
    ladder = qcfg.get("escalation", {"P1": ["escalate"], "P2": ["escalate"], "P3": []})
    reminded, escalated, breached = [], [], []
    for it in queue.items:
        if it.state != "PROPOSED":
            continue
        remind_at = it.opened_at + remind_pct * it.sla_hours
        if it.reminded_at is None and now_hours >= remind_at and now_hours < it.due_at:
            it.reminded_at = now_hours
            it.history.append({"at": now_hours, "event": "reminder", "note": f"nudged {', '.join(it.approvers)}: {it.due_at - now_hours:.1f} h left"})
            reminded.append(it.id)
        if now_hours >= it.due_at:
            breached.append(it.id)
            if not it.escalated:
                roles = ladder.get(it.priority, [])
                added = [profile.approver_for(role, it.dc) for role in roles]
                if added:
                    it.escalated = True
                    it.escalated_at = now_hours
                    it.approvers = _unique(it.approvers + added)
                    it.history.append({"at": now_hours, "event": "escalation", "note": f"SLA passed with no answer; escalated to {', '.join(added)}"})
                    escalated.append(it.id)
                elif not any(h.get("event") == "breach" for h in it.history):
                    # nobody to escalate to: it is breached, not "escalated" (The Agent Desk, 8 Nov)
                    it.history.append({"at": now_hours, "event": "breach", "note": "SLA passed with no answer; no escalation role for this priority, it stays with its approvers"})
    queue.log.append({"at": now_hours, "event": "tick", "reminded": reminded, "escalated": escalated, "breached": breached})
    return {"now_hours": now_hours, "reminded": reminded, "escalated": escalated, "breached": breached}


def escalate(queue: Queue, profile: Profile, item_id: str, now_hours: float, reason: str = "") -> dict:
    """The agent's write action: escalate one overdue item. Refused before the SLA has passed."""
    it = queue.get(item_id)
    if not it.is_open:
        return {"error": f"{item_id} is {it.state}; nothing to escalate"}
    if now_hours < it.due_at:
        return {"error": f"{item_id} is not overdue: due at {it.due_at:.0f} h, now {now_hours:.0f} h; the SLA decides, not the agent"}
    if it.escalated:
        return {"item_id": item_id, "escalated": True, "approvers": it.approvers, "note": "already escalated"}
    roles = profile.raw.get("queue", {}).get("escalation", {}).get(it.priority, [])
    added = [profile.approver_for(role, it.dc) for role in roles]
    it.escalated = True
    it.escalated_at = now_hours
    it.approvers = _unique(it.approvers + added)
    it.history.append({"at": now_hours, "event": "escalation", "note": f"escalated by the agent: {reason or 'SLA passed'}; now with {', '.join(it.approvers)}"})
    queue.log.append({"at": now_hours, "event": "escalate", "item": item_id})
    return {"item_id": item_id, "escalated": True, "approvers": it.approvers, "added": added}


# --- people --------------------------------------------------------------------------------------
def decide(queue: Queue, item_id: str, approver: str, decision: str, now_hours: float, note: str = "") -> dict:
    it = queue.get(item_id)
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError("decision must be APPROVED or REJECTED")
    if it.state != "PROPOSED":
        raise ValueError(f"{item_id} is {it.state}; only a PROPOSED item can be decided")
    names = [a.split(" (")[0] for a in it.approvers]
    if approver not in names and approver not in it.approvers:
        raise PermissionError(f"{approver} is not an approver of {item_id}; approvers: {', '.join(it.approvers)}")
    _transition(it, decision, now_hours, f"{decision.lower()} by {approver}" + (f": {note}" if note else ""))
    it.decided_by = approver
    it.decision_note = note
    if decision == "REJECTED":
        _transition(it, "CLOSED", now_hours, "closed after rejection")
    queue.log.append({"at": now_hours, "event": decision.lower(), "item": item_id, "by": approver})
    return it.as_dict()


def apply(queue: Queue, item_id: str, now_hours: float) -> dict:
    """Write back to the operational systems. A stub that records what would have been sent."""
    it = queue.get(item_id)
    if it.state != "APPROVED":
        raise PermissionError(f"only an APPROVED exception can be applied; {item_id} is {it.state}")
    sent = [{"system": _system_for(a["code"]), "payload": a} for a in it.actions if a["code"] != "no_operational_action"]
    _transition(it, "APPLIED", now_hours, f"sent {len(sent)} action(s) to {', '.join(sorted({s['system'] for s in sent}))}")
    queue.log.append({"at": now_hours, "event": "applied", "item": item_id, "sent": sent})
    return {"item_id": item_id, "state": it.state, "sent": sent}


# --- the digest -----------------------------------------------------------------------------------
def digest(queue: Queue, now_hours: float) -> dict:
    """The numbers the Monday digest is written from. The model turns them into a paragraph."""
    open_items = queue.open_items()
    by_p = {p: sum(1 for it in queue.items if it.priority == p) for p in ("P1", "P2", "P3")}
    open_by_p = {p: sum(1 for it in open_items if it.priority == p) for p in ("P1", "P2", "P3")}
    by_state = {}
    for it in queue.items:
        by_state[it.state] = by_state.get(it.state, 0) + 1
    breaches = [it.id for it in open_items if now_hours >= it.due_at]
    escalated = [it.id for it in queue.items if it.escalated]
    upcoming = sorted((it for it in open_items if now_hours < it.due_at), key=lambda it: it.due_at)
    next_due = {"id": upcoming[0].id, "due_at": upcoming[0].due_at, "hours_left": round(upcoming[0].due_at - now_hours, 1)} if upcoming else None
    money = sum(it.impact_inr for it in open_items)
    heads = open_items[:3]
    return {
        "week": queue.week, "now_hours": now_hours, "exceptions": len(queue.items), "built_from": queue.built_from,
        "open": len(open_items), "by_priority": by_p, "open_by_priority": open_by_p, "by_state": by_state,
        "breaches": breaches, "breach_count": len(breaches), "escalated": escalated, "next_due": next_due,
        "money_at_stake_inr": money, "headlines": [it.line() for it in heads],
        "headline_ids": [it.id for it in heads],
    }


# --- sizing ----------------------------------------------------------------------------------------
def size_week(store: Store, profile: Profile, week: int) -> dict:
    q = build_queue(store, profile, week, propose=False)
    return {"week": week, "deviations": q.built_from, "exceptions": len(q.items),
            "by_priority": {p: sum(1 for it in q.items if it.priority == p) for p in ("P1", "P2", "P3")}}


def size_history(store: Store, profile: Profile, first_week: int = 3) -> list[dict]:
    """How big the queue is each week of the history: the number the staffing conversation needs."""
    return [size_week(store, profile, w) for w in range(first_week, store.weeks + 1)]


# --- persistence ---------------------------------------------------------------------------------
def queue_path(week: int, folder: str | Path | None = None) -> Path:
    folder = Path(folder or os.environ.get("FOUNDRY_QUEUE_DIR", "runs"))
    return folder / f"queue-w{week}.json"


def to_json(queue: Queue, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(queue.as_dict(), indent=1))
    os.replace(tmp, path)
    return path


def from_json(path: str | Path) -> Queue:
    d = json.loads(Path(path).read_text())
    items = []
    for row in d["items"]:
        row = dict(row)
        row.pop("key", None)
        items.append(ExceptionItem(**row))
    return Queue(week=d["week"], company=d["company"], items=items, built_from=d["built_from"], log=d.get("log", []))


def load_or_build(store: Store, profile: Profile, week: int, folder: str | Path | None = None, proposals=None) -> Queue:
    p = queue_path(week, folder)
    if p.exists():
        return from_json(p)
    q = build_queue(store, profile, week, proposals=proposals)
    to_json(q, p)
    return q
