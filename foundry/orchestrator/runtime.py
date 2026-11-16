"""The tower's runtime: one object that owns the store, the agents, the plans and the clock, and
reacts to events. Drivers (the Monday cron, a carrier feed, a person in the console) only publish
events; the handlers below do the work, in order, and the bus writes every step into the journal.

    schedule.monday    watcher: the exception queue          -> breach.opened (one per exception)
                       radar: 14-day projections             -> risk.flagged (RED, above the pre-empt line)
    breach.opened      orchestrator: the tower, breach mode  -> a PROPOSED plan
    risk.flagged       orchestrator: fold into the live plan for that DC x SKU, or pre-empt
    eta.updated, lane.delayed, transfer.delayed, snapshot.refreshed
                       listener: change the store, re-project, decide whether it matters
                                                             -> replan.requested | risk.flagged | a note
    replan.requested   orchestrator again; the new plan supersedes the old one
    plans.settle       reconcile: one live plan per DC x SKU x week, and two plans never spend the
                       same spare stock (earliest stockout first, then the money)  -> plan.ready
    plan.ready         notifier: checks every step has a named person
    digest.compose     coworker: one message per person, earliest clock first
    approval.decided   approvals: all steps signed -> apply (stubs for TMS/ERP); a transfer goes in
                       flight and the radar counts it from then on -> plan.applied | plan.closed
    clock.tick         sla: a reminder at half the SLA, escalation at the SLA

The clock is hours since Monday 06:30 IST (the cron). Plans are plain dicts so the console, the
journal and a JSON state file can carry them without translation.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Callable

from foundry.agents.rca_exception import queue as Q
from foundry.agents.rebalancing import logic as rbl
from foundry.approval import ProposalStore
from foundry.data.generate import refresh_snapshot
from foundry.levers import Levers
from foundry.network import GFB, Network
from foundry.notify import Outbox, approval_digest, clock_label, name_of, vp_digest
from foundry.notify.coworker import LOCALE, inr, set_brand, set_clock, set_currency, set_greeting
from foundry.orchestrator.bus import Bus, Event
from foundry.profile import Profile, load_profile
from foundry.risk import projection as risk
from foundry.store import TABLES, Store
from foundry.tower import agent as tower_agent
from foundry.tower.fake import FakeTowerClient

LIVE = ("PROPOSED", "RECONCILING", "READY", "PARTIAL")
SIGNABLE = ("READY", "PARTIAL")
NO_ACTION = {"no_operational_action"}
COMPANIONS = ("book_truck", "co_sign_transfer")       # steps that only exist because a transfer does


def clean(text, limit: int = 120) -> str:
    """Free text from outside (a carrier's reason, a note, a name) on its way into the store and, from there,
    into a model's tool results: printable words only, one line, short. The prompt treats it as data; this keeps
    it from looking like anything else (found by The Agent Desk, 8 Nov: a lane delay's reason reached the model)."""
    s = re.sub(r"[^\w\s.,;:'()/&+%@#₹-]", " ", str(text or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def key_of(week: int, dc: str, sku: str | None) -> str:
    return f"{week}:{dc}:{sku or 'ALL'}"


class TowerRuntime:
    def __init__(self, store: Store, profile: Profile, client_factory: Callable[[], object] | None = None,
                 folder: str | Path | None = None, console_url: str | None = None, now: float = 0.0):
        self.store, self.profile = store, profile
        self.client_factory = client_factory or (lambda: FakeTowerClient())
        self.folder = Path(folder or os.environ.get("FOUNDRY_TOWER_DIR", "runs/tower"))
        self.folder.mkdir(parents=True, exist_ok=True)
        self.console_url = console_url or os.environ.get("FOUNDRY_CONSOLE_URL", "http://localhost:8080")
        self.tcfg = {"monday_run_hour_ist": 6.5, "replan_on_eta_change_days": 1, "notify_channels": ["email"]}
        self.tcfg.update(profile.section("tower"))
        set_currency(profile.currency)
        set_clock(float(self.tcfg.get("monday_run_hour_local", self.tcfg["monday_run_hour_ist"])))
        set_greeting(self.tcfg.get("greet", "first"))
        self.rcfg = risk.cfg(profile)
        set_brand(profile.raw.get("brand") or "GFB", int(self.rcfg.get("horizon_days", 14)))
        if os.environ.get("FOUNDRY_PANEL_TABLE"):          # on AWS the operator's switch is the DynamoDB panel
            from foundry.levers_dynamo import DynamoLevers
            self.levers = DynamoLevers(audit_path=self.folder / "audit.jsonl")
        else:
            self.levers = Levers(self.folder / "panel.json", self.folder / "audit.jsonl")
        self.proposals = ProposalStore(self.folder / "proposals.jsonl")
        dom = re.sub(r"[^a-z0-9]", "", str(LOCALE.get("brand", "GFB")).lower()) or "gfb"
        self.outbox = Outbox(self.folder / "outbox.jsonl", channels=self.tcfg["notify_channels"], domain=f"{dom}.example")
        self.now = now
        self.bus = Bus(self.folder / "bus.jsonl", clock=lambda: self.now)
        self.plans: dict[str, dict] = {}
        self.queues: dict[int, Q.Queue] = {}
        self.radars: dict[int, dict] = {}
        self.views: dict[str, dict] = {}          # what the tower last believed about each DC x SKU
        self.runs: list[dict] = []
        self._seq = 0
        self._register()

    # ================================================================ wiring
    def _register(self) -> None:
        on = self.bus.on
        on("schedule.monday", self._watch, "watcher")
        on("schedule.monday", self._scan, "radar")
        on("breach.opened", self._on_breach, "orchestrator")
        on("risk.flagged", self._on_risk, "orchestrator")
        on("replan.requested", self._replan, "orchestrator")
        on("eta.updated", self._eta, "listener")
        on("lane.delayed", self._lane, "listener")
        on("transfer.delayed", self._transfer_delay, "listener")
        on("snapshot.refreshed", self._refreshed, "listener")
        on("plans.settle", self._settle, "reconcile")
        on("plan.ready", self._check_ready, "notifier")
        on("digest.compose", self._compose, "coworker")
        on("approval.decided", self._decide, "approvals")
        on("plan.applied", self._tell_applied, "notifier")
        on("plan.closed", self._tell_closed, "notifier")
        on("clock.tick", self._sla, "sla")

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    # ================================================================ drivers (what the outside world calls)
    def _advance(self, at: float | None) -> None:
        if at is not None:
            if at < self.now:
                raise ValueError(f"the clock cannot go back: now {self.now} h, asked {at} h")
            self.now = float(at)

    def _run(self, first: Event | None) -> dict:
        rows = self.bus.drain()
        rounds = 0
        while any(p["status"] == "PROPOSED" for p in self.plans.values()) and rounds < 5:
            rounds += 1                       # a reconciled plan comes back re-planned: settle again
            self.bus.publish("plans.settle", {"vp": bool(first and first.type == "schedule.monday") and rounds == 1}, key=f"settle:{self._next()}")
            rows += self.bus.drain()
        return {"at": self.now, "clock": clock_label(self.now), "accepted": first is not None,
                "events": [{k: r.get(k) for k in ("id", "type", "status", "handled_by", "notes")} for r in rows if "id" in r]}

    def monday(self, week: int, at: float | None = None) -> dict:
        self._advance(at)
        return self._run(self.bus.publish("schedule.monday", {"week": week}, key=f"monday:{week}", source="eventbridge.cron"))

    def eta(self, week: int, dc: str, sku: str, eta_days: int, carrier: str = "", truck_id: str = "", at: float | None = None) -> dict:
        self._advance(at)
        carrier, truck_id = clean(carrier, 60), clean(truck_id, 40)
        return self._run(self.bus.publish("eta.updated", {"week": week, "dc": dc, "sku": sku, "eta_days": int(eta_days), "carrier": carrier, "truck_id": truck_id},
                                          key=f"eta:{week}:{dc}:{sku}:{eta_days}:{truck_id}", source="carrier.feed"))

    def lane_delay(self, week: int, lane_from: str, lane_to: str, delay_days: int, reason: str, carrier: str = "",
                   applies_to: str = "all", at: float | None = None) -> dict:
        """applies_to "all": every truck on the lane is held (a road closure); "new": only new dispatches (a strike)."""
        self._advance(at)
        reason, carrier = clean(reason), clean(carrier, 60)
        applies_to = applies_to if applies_to in ("all", "new") else "all"
        return self._run(self.bus.publish("lane.delayed", {"week": week, "lane_from": lane_from, "lane_to": lane_to, "delay_days": int(delay_days),
                                                           "reason": reason, "carrier": carrier, "applies_to": applies_to},
                                          key=f"lane:{week}:{lane_from}:{lane_to}:{delay_days}:{reason}", source="carrier.feed"))

    def transfer_delay(self, transfer_id: str, lands_day: int, reason: str = "", at: float | None = None) -> dict:
        self._advance(at)
        reason = clean(reason)
        return self._run(self.bus.publish("transfer.delayed", {"transfer_id": transfer_id, "lands_day": int(lands_day), "reason": reason},
                                          key=f"tdelay:{transfer_id}:{lands_day}", source="carrier.feed"))

    def refresh(self, week: int, dc: str, sku: str, by: str = "", at: float | None = None) -> dict:
        self._advance(at)
        by = clean(by, 60) or name_of(self.profile.approver_for("refresh_snapshot", dc))
        return self._run(self.bus.publish("snapshot.refreshed", {"week": week, "dc": dc, "sku": sku, "by": by},
                                          key=f"refresh:{week}:{dc}:{sku}:{self.now}", source="console"))

    def decide(self, plan_id: str, approver: str, decision: str, note: str = "", step: int | None = None, at: float | None = None) -> dict:
        self._advance(at)
        decision = decision.upper()
        if decision in ("APPROVE", "YES"):
            decision = "APPROVED"
        if decision in ("REJECT", "NO"):
            decision = "REJECTED"
        return self._run(self.bus.publish("approval.decided", {"plan_id": clean(plan_id, 60), "approver": clean(name_of(approver), 60), "decision": decision,
                                                               "note": clean(note, 200), "step": step},
                                          key=f"decide:{plan_id}:{name_of(approver)}:{decision}:{step}", source="console"))

    def tick(self, at: float) -> dict:
        self._advance(at)
        return self._run(self.bus.publish("clock.tick", {}, key=f"tick:{self.now}", source="eventbridge.rate"))

    # ================================================================ helpers
    def live(self, key: str) -> dict | None:
        rows = [p for p in self.plans.values() if p["key"] == key and p["status"] in LIVE]
        return rows[-1] if rows else None

    def _project(self, week: int, dc: str, sku: str) -> dict:
        p = risk.project(self.store, self.profile, week, dc, sku)
        v = {"risk": p.risk, "first_short_day": p.first_short_day, "cases_short": p.cases_short, "money_at_risk": p.money_at_risk,
             "dos0": p.dos0, "priority": p.priority, "reason": p.reason, "notes": p.notes,
             "arrivals": [{"day": a["day"], "cases": round(a["cases"]), "source": a["source"]} for a in p.arrivals]}
        self.views[key_of(week, dc, sku)] = v
        return v

    def _what_if(self, week: int, dc: str, sku: str, step: dict) -> dict:
        w = risk.what_if(self.store, self.profile, week, dc, sku, float(step["cases"]), int(step.get("lands_end_of_day") or step["lane_days"]),
                         source=f"transfer {step.get('from_dc')}->{dc}")
        return {k: w[k] for k in ("cases", "lands_end_of_day", "before", "after", "money_avoided")}

    def _queue_item(self, rec: dict):
        q = self.queues.get(rec["week"])
        if q and rec.get("exception_id"):
            try:
                return q.get(rec["exception_id"])
            except KeyError:
                return None
        return None

    def _why(self, rec: dict) -> str:
        v = rec.get("radar") or {}
        cause = rec["root_cause"].replace("_", " ").lower()
        if v.get("risk") == "RED":
            return f"{cause}; the radar has {rec['dc']} short from day {v['first_short_day']} ({v['cases_short']:,.0f} cases, {inr(v['money_at_risk'])} of margin over {LOCALE['horizon_days']} days)"
        return f"{cause}; {inr(rec.get('impact_inr'))} at stake this week"

    def _headline(self, rec: dict, step: dict) -> str:
        """What the person is asked to do, in one line (the specialist's summary is for the console)."""
        if step["code"] == "inter_dc_transfer" and step.get("cases"):
            return (f"Move {step['cases']:,} cases of {rec['sku']} {step['from_dc']}->{rec['dc']}, "
                    f"on the shelf end of day {step.get('lands_end_of_day') or step.get('lane_days')}")
        if step["code"] == "book_truck":
            t = next((x for x in rec["steps"] if x["code"] == "inter_dc_transfer" and x.get("cases")), None)
            return f"Book the truck for the {t['from_dc']}->{rec['dc']} transfer ({t['cases']:,} cases)" if t else "Book the truck"
        return f"{rec['dc']} {rec.get('sku') or 'all SKUs'}: {step['text']}"

    def _money_line(self, rec: dict, step: dict) -> str:
        if step["code"] == "inter_dc_transfer" and step.get("cases"):
            line = f"saves {inr(step.get('margin_saved_inr'))} of margin for {inr(step.get('cost_inr'))} of lane cost"
            w = rec.get("what_if")
            if w:
                line += f"; radar {inr(w['before']['money_at_risk'])} at risk -> {inr(w['after']['money_at_risk'])} after the move"
            return line
        return f"{inr(rec.get('impact_inr'))} at stake ({rec['priority']})"

    # ================================================================ watcher and radar
    def _watch(self, ev: Event) -> str:
        week = int(ev.detail["week"])
        q = Q.build_queue(self.store, self.profile, week, opened_at=self.now, propose=False)
        self.queues[week] = q
        opened = 0
        for it in q.items:
            if {a["code"] for a in it.actions} <= NO_ACTION:
                Q._transition(it, "CLOSED", self.now, "nothing to act on; watch next week")
                continue
            self.bus.publish("breach.opened", {"week": week, "exception_id": it.id, "dc": it.dc, "sku": it.sku, "kpi": it.kpi,
                                               "root_cause": it.root_cause, "priority": it.priority, "impact_inr": it.impact_inr},
                             key=f"breach:{key_of(week, it.dc, it.sku)}")
            opened += 1
        return f"{len(q.items)} exceptions from {q.built_from} deviations: {opened} opened, {len(q.items) - opened} closed"

    def _scan(self, ev: Event) -> str:
        week = int(ev.detail["week"])
        rad = risk.radar(self.store, self.profile, week)
        self.radars[week] = rad
        for r in rad["rows"]:
            self.views[key_of(week, r["dc"], r["sku"])] = {k: r[k] for k in ("risk", "first_short_day", "cases_short", "money_at_risk", "dos0", "priority", "reason")}
        for r in rad["pre_empt"]:
            self.bus.publish("risk.flagged", {"week": week, "dc": r["dc"], "sku": r["sku"], "money_at_risk": r["money_at_risk"],
                                              "first_short_day": r["first_short_day"], "why": "Monday radar"},
                             key=f"risk:{key_of(week, r['dc'], r['sku'])}:monday")
        c = rad["counts"]
        return f"RED {c['RED']}, AMBER {c['AMBER']}, GREY {c['GREY']}, GREEN {c['GREEN']}; {len(rad['pre_empt'])} above the pre-empt line"

    # ================================================================ orchestrator
    def _on_breach(self, ev: Event) -> str:
        d = ev.detail
        k = key_of(d["week"], d["dc"], d["sku"])
        if self.live(k):
            return f"{k} already has live plan {self.live(k)['plan_id']}"
        rec = self._make_plan("breach", d["week"], d["dc"], d["sku"], d["kpi"], ev, exception_id=d["exception_id"])
        return f"{rec['plan_id']}: {rec['status']}, {len(rec['steps'])} steps"

    def _on_risk(self, ev: Event) -> str:
        d = ev.detail
        k = key_of(d["week"], d["dc"], d["sku"])
        live = self.live(k)
        if live:
            v = self._project(d["week"], d["dc"], d["sku"])
            live["radar"] = v
            live["history"].append({"at": self.now, "event": "radar", "note": f"radar agrees: {v['risk']} from day {v['first_short_day']}, {inr(v['money_at_risk'])} over {LOCALE['horizon_days']} days"})
            return f"folded into {live['plan_id']}: the breach is already being worked"
        rec = self._make_plan("pre_empt", d["week"], d["dc"], d["sku"], "RISK", ev)
        return f"{rec['plan_id']}: {rec['status']}, {len(rec['steps'])} steps (pre-empt: no KPI has moved yet)"

    def _replan(self, ev: Event) -> str:
        old = self.plans.get(ev.detail["plan_id"])
        if not old or old["status"] not in LIVE:
            return "nothing live to re-plan"
        self._release(old, "RELEASED")
        new = self._make_plan(old["mode"], old["week"], old["dc"], old["sku"], old["kpi"], ev, exception_id=old.get("exception_id"), supersedes=old)
        old["status"] = "SUPERSEDED"
        old["superseded_by"] = new["plan_id"]
        old["history"].append({"at": self.now, "event": "superseded", "note": f"by {new['plan_id']}: {ev.detail.get('reason', '')}"})
        self.proposals.supersede(old["proposal_id"], by=new["proposal_id"], note=ev.detail.get("reason", ""))
        for spid in self._specialist_pids(old):
            self.proposals.supersede(spid, by=new["proposal_id"], note="the tower re-planned")
        signed = sorted({s["decided_by"] for s in old["steps"] if s["decision"] == "APPROVED"})
        if signed:
            new["history"].append({"at": self.now, "event": "note", "note": f"{', '.join(signed)} had signed v{old['version']}; the numbers changed, so v{new['version']} needs fresh signatures"})
        return f"{old['plan_id']} -> {new['plan_id']} (v{new['version']}): {ev.detail.get('reason', '')}"

    def _make_plan(self, mode: str, week: int, dc: str, sku: str | None, kpi: str, ev: Event, exception_id: str | None = None,
                   supersedes: dict | None = None) -> dict:
        task = {"mode": mode, "dc": dc, "sku": sku, "week": week}
        self.store.dispatch_day = int(self.now // 24)     # a truck the tower sends on Tuesday leaves on Tuesday
        if mode == "breach":
            task["kpi"] = kpi
        r = tower_agent.run(task, self.store, self.profile, self.client_factory(), self.levers, self.proposals)
        self.runs.append({"at": self.now, "mode": mode, "key": key_of(week, dc, sku), "stop_reason": r.stop_reason, "turns": r.turns,
                          "meter": r.total_meter, "run_id": r.run_id})
        sub = r.plan or {}
        steps = []
        stale = _stale_dc(sub.get("dropped", []))
        for st in sub.get("plan", []):
            if st["code"] in NO_ACTION:
                continue                     # "watch next week" is a note, not a step anyone signs
            n = len(steps) + 1
            step = {"n": n, "code": st["code"], "text": st.get("text") or st["code"], "approver": st.get("approver") or "unassigned",
                    "source": st.get("source"), "decision": None, "decided_by": None, "decided_at": None, "notified_at": None,
                    "revised": bool(supersedes)}
            for f in ("cases", "from_dc", "to_dc", "lane_days", "lands_end_of_day", "cost_inr", "margin_saved_inr"):
                if st.get(f) is not None:
                    step[f] = st[f]
            step["about"] = stale if (st["code"] == "refresh_snapshot" and stale) else dc
            tr = next((x for x in sub.get("plan", []) if x["code"] == "inter_dc_transfer" and x.get("cases")), None)
            if st["code"] in ("book_truck", "co_sign_transfer") and tr and step["text"] in (st["code"], "", None):
                step["text"] = (f"Book the truck for the {tr['from_dc']}->{dc} transfer ({tr['cases']:,} cases)" if st["code"] == "book_truck"
                                else f"Co-sign: {tr['from_dc']} sends {tr['cases']:,} cases and goes below its long line")
            if st["code"] == "refresh_snapshot":
                step["text"] = f"Refresh the {step['about']} {sku or ''} stock snapshot" + (" (it blocks a transfer)" if step["about"] != dc else "")
            steps.append(step)
        done = {}                     # the tower remembers what it already did for this DC x SKU this week
        for old in self.plans.values():
            if old["key"] == key_of(week, dc, sku) and old["status"] == "APPLIED":
                for s in old["steps"]:
                    done.setdefault(s["code"], (old["plan_id"], s.get("decided_by")))
        dropped_done = []
        for s in list(steps):
            if s["code"] in done and s["code"] not in ("inter_dc_transfer", "book_truck", "co_sign_transfer"):
                steps.remove(s)
                dropped_done.append({"code": s["code"], "by": "memory", "reason": f"already applied this week in {done[s['code']][0]} (signed by {done[s['code']][1]})"})
        for i, s in enumerate(steps, 1):
            s["n"] = i
        submitted = r.stop_reason == "submitted"
        status = "PROPOSED" if (submitted and steps) else ("WATCH" if submitted else "FAILED")
        pid = r.proposal_id or sub.get("proposal_id") or f"{status}-{week:02d}-{dc}-{sku or 'ALL'}-{self._next():03d}"
        prio = sub.get("priority") or "P3"
        sla = self.profile.sla_hours(prio) if prio in ("P1", "P2", "P3") else 72
        rec = {"plan_id": pid, "proposal_id": pid, "key": key_of(week, dc, sku), "week": week, "dc": dc, "sku": sku, "mode": mode, "kpi": kpi,
               "root_cause": sub.get("root_cause", "UNKNOWN"), "priority": prio, "sla_hours": sla, "status": status,
               "version": (supersedes["version"] + 1) if supersedes else 1, "supersedes": supersedes["plan_id"] if supersedes else None,
               "superseded_by": None, "created_at": self.now, "due_at": self.now + sla, "trigger": ev.id, "trigger_type": ev.type,
               "exception_id": exception_id, "steps": steps, "dropped": sub.get("dropped", []) + dropped_done, "impact_inr": sub.get("total_impact_inr", 0),
               "specialists": sub.get("specialists_called", []), "summary": sub.get("summary", r.error or r.stop_reason),
               "meter": r.total_meter, "radar": self._project(week, dc, sku) if sku else None, "what_if": None,
               "reconcile": [], "reminded_at": None, "escalated_at": None, "escalated_to": [],
               "history": [{"at": self.now, "event": "made", "note": f"{mode} run on {ev.type} ({ev.id}); tower stop: {r.stop_reason}"}]}
        t = next((s for s in steps if s["code"] == "inter_dc_transfer" and s.get("cases")), None)
        if t and sku:
            rec["what_if"] = self._what_if(week, dc, sku, t)         # the runtime re-checks the move against the radar
        if status == "WATCH" and dropped_done:
            owners = sorted({w for _, w in done.values() if w})
            for who in owners:
                self.outbox.send(who, f"FYI {dc} {sku or ''}: what you signed is still the only lever",
                                 f"{self._why(rec)}. The tower looked again ({ev.type}): " + "; ".join(x["reason"] for x in dropped_done)
                                 + ". Nothing new to sign; chase the step you already approved.", "fyi", refs=[pid], at=self.now, priority=prio)
            rec["history"].append({"at": self.now, "event": "fyi", "note": f"nothing new to sign; told {', '.join(owners)}"})
        it = self._queue_item(rec)
        if it is not None:
            if it.state == "DIAGNOSED" and status == "PROPOSED":
                Q._transition(it, "PROPOSED", self.now, f"tower plan {pid} to {', '.join(sorted({name_of(s['approver']) for s in steps}))}")
            elif supersedes:
                it.history.append({"at": self.now, "event": "replanned", "note": f"tower plan {pid} supersedes {supersedes['plan_id']}"})
            it.proposal_id = pid
        self.plans[pid] = rec
        return rec

    def _specialist_pids(self, rec: dict) -> list[str]:
        row = self.proposals.get(rec["proposal_id"])
        return [p for p in ((row or {}).get("payload", {}).get("specialist_proposals") or []) if p]

    # ================================================================ listener
    def _reassess(self, week: int, dc: str, sku: str, before: dict, after: dict, cause: str, small: bool = False) -> str:
        k = key_of(week, dc, sku)
        live = self.live(k)
        moved = (before["risk"] != after["risk"]) or abs(after["money_at_risk"] - before["money_at_risk"]) >= max(0.1 * before["money_at_risk"], 5000)
        shift = f"{before['risk']} {inr(before['money_at_risk'])} -> {after['risk']} {inr(after['money_at_risk'])}"
        if live:
            live["radar"] = after
        if small:
            return f"{k}: {cause}; under the {self.tcfg['replan_on_eta_change_days']}-day re-plan threshold, projection updated ({shift}), plan kept"
        if not moved:
            return f"{k}: {cause}; the projection barely moves ({shift}), plan kept"
        if live:
            self.bus.publish("replan.requested", {"plan_id": live["plan_id"], "reason": f"{cause}: {shift}"}, key=f"replan:{live['plan_id']}:{self._next()}")
            return f"{k}: {cause}; {shift}: re-plan"
        if after["risk"] == "RED" and after["money_at_risk"] >= float(self.rcfg["pre_empt_min_inr"]):
            self.bus.publish("risk.flagged", {"week": week, "dc": dc, "sku": sku, "money_at_risk": after["money_at_risk"],
                                              "first_short_day": after["first_short_day"], "why": cause}, key=f"risk:{k}:{self._next()}")
            return f"{k}: {cause}; {shift}: flagged for pre-emption"
        return f"{k}: {cause}; {shift}: nothing to do"

    def _snap(self, week: int, dc: str, sku: str) -> dict | None:
        rows = [r for r in self.store.inventory if r["week"] == week and r["dc"] == dc and r["sku"] == sku]
        return rows[-1] if rows else None

    def _eta(self, ev: Event) -> str:
        d = ev.detail
        row = self._snap(d["week"], d["dc"], d["sku"])
        if not row or not row.get("open_receipt"):
            return f"{d['dc']} {d['sku']}: no open receipt to re-time"
        before = self._project(d["week"], d["dc"], d["sku"])
        old = int(row.get("open_receipt_eta_days") or risk.RECEIPT_LANDS_IN)
        row["open_receipt_eta_days"] = int(d["eta_days"])
        after = self._project(d["week"], d["dc"], d["sku"])
        small = abs(int(d["eta_days"]) - old) < float(self.tcfg["replan_on_eta_change_days"])
        return self._reassess(d["week"], d["dc"], d["sku"], before, after, f"{d.get('carrier') or 'carrier'} ETA day {old} -> day {d['eta_days']}", small)

    def _lane(self, ev: Event) -> str:
        d = ev.detail
        net = self.store.net
        week = int(d["week"])
        pairs = []
        if d["lane_from"] in net.plants:
            dcs = net.dcs if d["lane_to"] == "*" else [d["lane_to"]]
            skus = [s for s, part in net.parts.items() if part[2] == d["lane_from"] and part[1] in ("A", "B")]
            pairs = [(dc, s) for dc in dcs for s in skus if self._snap(week, dc, s)]
        before = {p: self._project(week, *p) for p in pairs}
        self.store.extra.setdefault("lane_delays", []).append({k: d.get(k) for k in ("week", "lane_from", "lane_to", "delay_days", "reason", "carrier", "applies_to")})
        notes = []
        for p in pairs:
            after = self._project(week, *p)
            notes.append(self._reassess(week, p[0], p[1], before[p], after, f"{d['lane_from']}->{p[0]} +{d['delay_days']} days ({d['reason']})"))
        # an inter-DC lane: every live plan that moves stock on it is re-priced
        for rec in list(self.plans.values()):
            if rec["status"] in LIVE and rec["week"] == week and any(s["code"] == "inter_dc_transfer" and s.get("from_dc") == d["lane_from"]
                                                                       and rec["dc"] == d["lane_to"] for s in rec["steps"]):
                self.bus.publish("replan.requested", {"plan_id": rec["plan_id"], "reason": f"lane {d['lane_from']}->{d['lane_to']} +{d['delay_days']} days ({d['reason']})"},
                                 key=f"replan:{rec['plan_id']}:{self._next()}")
                notes.append(f"{rec['plan_id']}: its transfer uses the delayed lane: re-plan")
        acted = [n for n in notes if n.endswith(("re-plan", "pre-emption"))]
        return f"{len(pairs)} DC x SKU re-projected; {len(acted)} need action" + (": " + " | ".join(acted) if acted else "")

    def _transfer_delay(self, ev: Event) -> str:
        d = ev.detail
        rows = [t for t in self.store.extra.get("transfers_inflight", []) if t["transfer_id"] == d["transfer_id"]]
        if not rows:
            return f"no transfer {d['transfer_id']} in flight"
        t = rows[0]
        before = self._project(t["week"], t["to_dc"], t["sku"])
        old = t["lands_day"]
        t["lands_day"] = int(d["lands_day"])
        after = self._project(t["week"], t["to_dc"], t["sku"])
        return self._reassess(t["week"], t["to_dc"], t["sku"], before, after, f"transfer {t['transfer_id']} lands day {old} -> day {d['lands_day']} ({d.get('reason') or 'carrier'})")

    def _refreshed(self, ev: Event) -> str:
        d = ev.detail
        week, dc, sku = int(d["week"]), d["dc"], d["sku"]
        before = self._project(week, dc, sku)
        res = refresh_snapshot(self.store, week, dc, sku)
        if "error" in res:
            return res["error"]
        after = self._project(week, dc, sku)
        notes = [f"{dc} {sku} refreshed by {d['by']}: on hand {res['before']['on_hand']:,} -> {res['after']['on_hand']:,}, {after['risk']}"]
        live = self.live(key_of(week, dc, sku))
        if live and any(s["code"] == "refresh_snapshot" for s in live["steps"]):
            for s in live["steps"]:
                if s["decision"] is None:
                    s.update(decision="APPROVED", decided_by=d["by"], decided_at=self.now)
            self._apply(live, d["by"], note="the refresh was the action")
            notes.append(f"{live['plan_id']} done")
        # a stale sender blocked other plans: give them another look
        for rec in list(self.plans.values()):
            if rec["status"] in LIVE and rec["week"] == week and rec["sku"] == sku and rec["dc"] != dc and \
                    any(f"{dc} snapshot" in (x.get("reason") or "") for x in rec["dropped"]):
                self.bus.publish("replan.requested", {"plan_id": rec["plan_id"], "reason": f"{dc}'s snapshot was refreshed: it may send stock now"},
                                 key=f"replan:{rec['plan_id']}:{self._next()}")
                notes.append(f"{rec['plan_id']} was blocked by the stale {dc} snapshot: re-plan")
        return "; ".join(notes)

    # ================================================================ reconcile
    def _settle(self, ev: Event) -> str:
        notes = self._reconcile()
        batch = [p for p in self.plans.values() if p["status"] == "PROPOSED"]
        for p in sorted(batch, key=lambda p: (p["priority"], p["due_at"])):
            p["status"] = "READY"
            self.bus.publish("plan.ready", {"plan_id": p["plan_id"]}, key=f"ready:{p['plan_id']}")
        self.bus.publish("digest.compose", {"batch": [p["plan_id"] for p in batch], "vp": ev.detail.get("vp", False)}, key=f"digest:{self._next()}")
        return f"{len(batch)} plan(s) settled; " + ("; ".join(notes) if notes else "no two plans compete for the same stock")

    def _reconcile(self) -> list[str]:
        """Two plans may each be right alone and wrong together: both want the same spare cases.
        The reconciler never invents a transfer. It holds stock for the most urgent claim (earliest
        stockout, then the money) and sends the others back to the specialist with that stock
        already spoken for; the specialist re-routes, shrinks the move or falls back to the plant."""
        notes = self._dedupe_actions()
        claims: dict[tuple, list] = {}
        for p in self.plans.values():
            if p["status"] != "PROPOSED":
                continue
            for s in p["steps"]:
                if s["code"] == "inter_dc_transfer" and s.get("cases") and s["decision"] is None:
                    claims.setdefault((p["week"], p["sku"], s["from_dc"]), []).append((p, s))
        for (week, sku, donor), cl in claims.items():
            def rank(c):
                p, _ = c
                v = p.get("radar") or self._project(week, p["dc"], sku)
                return (v.get("first_short_day") or 99, -(v.get("money_at_risk") or 0))
            cl.sort(key=rank)
            order = ", ".join(f"{p['dc']} (short from day {(p.get('radar') or {}).get('first_short_day')})" for p, _ in cl)
            for p, s in cl:
                rows = {r["dc"]: r for r in rbl.position(self.store, self.profile, week, sku)}     # holds made so far count
                soft, spare = rbl.surplus(rows[donor], self.profile) if donor in rows else (0.0, 0.0)
                if s["cases"] <= spare:
                    self._hold(p, s)
                    needs_cosign = s["cases"] > soft and not any(x["code"] == "co_sign_transfer" for x in p["steps"])
                    if needs_cosign:
                        # alone each move stayed above the sender's long line; together they do not
                        who = self.profile.approver_for("co_sign_transfer", donor)
                        p["steps"].insert(s["n"], {"n": 0, "code": "co_sign_transfer", "text": f"Co-sign: {donor} goes below its long line",
                                                   "approver": who, "source": "reconcile", "decision": None, "decided_by": None,
                                                   "decided_at": None, "notified_at": None, "revised": True, "about": donor})
                        for i, x in enumerate(p["steps"], 1):
                            x["n"] = i
                        note = (f"{p['plan_id']}: with the more urgent claims held ({order}), {s['cases']:,} more cases take {donor} "
                                f"below its long line ({soft:,.0f} spare above it): {name_of(who)} co-signs")
                        p["reconcile"].append(note)
                        p["history"].append({"at": self.now, "event": "reconciled", "note": note})
                        self._revise_proposal(p, note)
                        notes.append(note)
                    continue
                note = (f"{p['plan_id']}: {donor} has {spare:,.0f} spare cases of {sku} left after more urgent claims "
                        f"({order}); {s['cases']:,} asked; back to the specialist with that stock held")
                p["reconcile"].append(note)
                p["history"].append({"at": self.now, "event": "reconciled", "note": note})
                p["status"] = "RECONCILING"
                self.bus.publish("replan.requested", {"plan_id": p["plan_id"], "reason": f"{donor}'s spare {sku} is held for a DC that runs dry first"},
                                 key=f"replan:{p['plan_id']}:{self._next()}")
                notes.append(note)
        return notes

    def _hold(self, rec: dict, step: dict) -> None:
        self.store.extra.setdefault("reservations", []).append(
            {"plan_id": rec["plan_id"], "week": rec["week"], "sku": rec["sku"], "from_dc": step["from_dc"], "to_dc": rec["dc"],
             "cases": step["cases"], "status": "HELD", "at": self.now})

    def _release(self, rec: dict, status: str) -> None:
        for r in self.store.extra.get("reservations", []):
            if r["plan_id"] == rec["plan_id"] and r["status"] == "HELD":
                r["status"] = status

    def _dedupe_actions(self) -> list[str]:
        """The same action asked of the same person in two plans is one action. It stays in the plan
        that owns the DC it is about; if the other plan is more urgent, the owner inherits its clock
        (priority inheritance: a P3 refresh that blocks a P1 transfer is a P1 refresh)."""
        notes = []
        live = [p for p in self.plans.values() if p["status"] in LIVE]
        rank = {"P1": 0, "P2": 1, "P3": 2}
        for p in live:
            for s in list(p["steps"]):
                if s["decision"] is not None or s.get("about") in (None, p["dc"]):
                    continue
                owner = self.live(key_of(p["week"], s["about"], p["sku"]))
                if not owner or owner is p:
                    continue
                twin = next((x for x in owner["steps"] if x["code"] == s["code"] and x["decision"] is None), None)
                if not twin:
                    continue
                p["steps"].remove(s)
                for i, x in enumerate(p["steps"], 1):
                    x["n"] = i
                p["dropped"].append({"code": s["code"], "by": "reconcile", "reason": f"the same action is step {twin['n']} of {owner['plan_id']}"})
                note = f"{p['plan_id']}: '{s['code']}' for {s['about']} is already {owner['plan_id']} step {twin['n']}; kept once"
                if rank.get(p["priority"], 9) < rank.get(owner["priority"], 9) or p["due_at"] < owner["due_at"]:
                    old = (owner["priority"], clock_label(owner["due_at"]))
                    owner["priority"] = min(owner["priority"], p["priority"], key=lambda x: rank.get(x, 9))
                    owner["due_at"] = min(owner["due_at"], p["due_at"])
                    owner["sla_hours"] = round(owner["due_at"] - owner["created_at"], 1)
                    twin["notified_at"] = None
                    twin["revised"] = True
                    owner["history"].append({"at": self.now, "event": "inherited", "note": f"{old[0]} due {old[1]} -> {owner['priority']} due {clock_label(owner['due_at'])}: it blocks {p['plan_id']}"})
                    note += f"; {owner['plan_id']} inherits {p['priority']} and the {clock_label(p['due_at'])} clock (it blocks {p['plan_id']})"
                p["history"].append({"at": self.now, "event": "reconciled", "note": note})
                self._revise_proposal(p, note)
                notes.append(note)
        return notes

    def _revise_proposal(self, rec: dict, note: str) -> None:
        """The audit must show what will actually be sent: the reconciled plan is a new proposal."""
        actions = [{k: s.get(k) for k in ("code", "text", "approver", "source", "cases", "from_dc", "to_dc", "lane_days") if s.get(k) is not None} for s in rec["steps"]]
        row = self.proposals.propose(rec["dc"], rec["sku"], rec["week"], rec["root_cause"], actions, f"revised by the reconciler: {note}", "reconcile",
                                     kind="plan", payload={"plan": rec["steps"], "revises": rec["proposal_id"], "plan_id": rec["plan_id"]}, agent="tower.reconcile")
        self.proposals.supersede(rec["proposal_id"], by=row["proposal_id"], note="reconciled")
        rec["proposal_id"] = row["proposal_id"]

    # ================================================================ notifier and coworker
    def _check_ready(self, ev: Event) -> str:
        rec = self.plans[ev.detail["plan_id"]]
        fixed = 0
        for s in rec["steps"]:
            if s["approver"] in ("unassigned", None, ""):
                s["approver"] = self.profile.approver_for("escalate", rec["dc"])
                fixed += 1
        return f"{rec['plan_id']} ready for {', '.join(sorted({name_of(s['approver']) for s in rec['steps']}))}" + (f"; {fixed} unassigned step(s) routed to the VP" if fixed else "")

    def _compose(self, ev: Event) -> str:
        by_person: dict[str, list[dict]] = {}
        for p in self.plans.values():
            if p["status"] not in SIGNABLE:
                continue
            for s in p["steps"]:
                if s["decision"] is None and s["notified_at"] is None:
                    by_person.setdefault(name_of(s["approver"]), []).append(
                        {"plan_id": p["plan_id"], "step": s["n"], "code": s["code"], "text": self._headline(p, s), "priority": p["priority"],
                         "due_at": p["due_at"], "why": self._why(p), "money_line": self._money_line(p, s), "revised": s.get("revised")})
                    s["notified_at"] = self.now
        for name, items in by_person.items():
            subject, body = approval_digest(name, items, self.now, self.console_url)
            self.outbox.send(name, subject, body, "approval_request", refs=sorted({i["plan_id"] for i in items}), at=self.now,
                             priority=min(i["priority"] for i in items))
        sent_vp = ""
        if ev.detail.get("vp") and self.plans:
            week = max(p["week"] for p in self.plans.values())
            vp = self.profile.approver_for("escalate")
            subject, body = vp_digest(name_of(vp), week, [p for p in self.plans.values() if p["week"] == week], self.radars.get(week), self.now, self.cost_inr())
            self.outbox.send(vp, subject, body, "vp_digest", at=self.now)
            sent_vp = f" + the VP digest to {name_of(vp)}"
        return f"{len(by_person)} message(s): {', '.join(sorted(by_person)) or 'nobody new'}{sent_vp}"

    def _tell_applied(self, ev: Event) -> str:
        rec = self.plans[ev.detail["plan_id"]]
        people = sorted({name_of(s["approver"]) for s in rec["steps"]})
        for who in people:
            self.outbox.send(who, f"Done: {rec['plan_id']} applied ({rec['dc']} {rec.get('sku') or 'all SKUs'})",
                             f"All {len(rec['steps'])} steps were signed; the tower sent them: " + "; ".join(f"{x['system']}: {x['code']}" for x in ev.detail.get("sent", [])),
                             "applied", refs=[rec["plan_id"]], at=self.now)
        return f"told {', '.join(people)}"

    def _tell_closed(self, ev: Event) -> str:
        rec = self.plans[ev.detail["plan_id"]]
        others = sorted({name_of(s["approver"]) for s in rec["steps"]} - {ev.detail["by"]})
        for who in others:
            self.outbox.send(who, f"Cancelled: {rec['plan_id']} rejected by {ev.detail['by']}",
                             f"{ev.detail['by']} rejected the plan ({ev.detail.get('note') or 'no reason given'}). Nothing was sent; your steps are cancelled.",
                             "closed", refs=[rec["plan_id"]], at=self.now)
        return f"told {', '.join(others) or 'nobody'}"

    # ================================================================ approvals
    def _decide(self, ev: Event) -> str:
        d = ev.detail
        rec = self.plans.get(d["plan_id"])
        if not rec:
            return f"refused: no plan {d['plan_id']}"
        if rec["status"] not in SIGNABLE:
            return f"refused: {rec['plan_id']} is {rec['status']}" + (f" (see {rec['superseded_by']})" if rec.get("superseded_by") else "")
        who = d["approver"]
        vp = name_of(self.profile.approver_for("escalate", rec["dc"]))
        mine = [s for s in rec["steps"] if s["decision"] is None and (name_of(s["approver"]) == who or (rec["escalated_at"] is not None and who in rec["escalated_to_names"]))
                and (d.get("step") in (None, s["n"]))] if rec.get("escalated_to_names") else \
               [s for s in rec["steps"] if s["decision"] is None and name_of(s["approver"]) == who and (d.get("step") in (None, s["n"]))]
        if not mine:
            return f"refused: {who} has no open step on {rec['plan_id']} (waiting on {', '.join(sorted({name_of(s['approver']) for s in rec['steps'] if s['decision'] is None}))})"
        for s in mine:
            s.update(decision=d["decision"], decided_by=who, decided_at=self.now)
        if d["decision"] == "REJECTED":
            rec["status"] = "REJECTED"
            self._release(rec, "RELEASED")
            self.proposals.decide(rec["proposal_id"], who, "REJECTED", d.get("note", ""), enforce=False)
            for spid in self._specialist_pids(rec):
                if (self.proposals.get(spid) or {}).get("status") == "PENDING_HUMAN":
                    self.proposals.decide(spid, who, "REJECTED", f"rejected inside tower plan {rec['plan_id']}", enforce=False)
            it = self._queue_item(rec)
            if it is not None and it.state == "PROPOSED":
                Q._transition(it, "REJECTED", self.now, f"rejected by {who}: {d.get('note', '')}")
                Q._transition(it, "CLOSED", self.now, "closed after rejection")
            rec["history"].append({"at": self.now, "event": "rejected", "note": f"{who}: {d.get('note', '')}"})
            self.bus.publish("plan.closed", {"plan_id": rec["plan_id"], "by": who, "note": d.get("note", "")}, key=f"closed:{rec['plan_id']}")
            return f"{rec['plan_id']} rejected by {who}"
        rec["history"].append({"at": self.now, "event": "signed", "note": f"{who} signed step(s) {', '.join(str(s['n']) for s in mine)}"})
        if all(s["decision"] == "APPROVED" for s in rec["steps"]):
            sent = self._apply(rec, who)
            return f"{rec['plan_id']} fully signed and applied: {len(sent)} action(s) sent"
        rec["status"] = "PARTIAL"
        waiting = sorted({name_of(s["approver"]) for s in rec["steps"] if s["decision"] is None})
        return f"{rec['plan_id']}: {who} signed; waiting on {', '.join(waiting)}"

    def _apply(self, rec: dict, who: str, note: str = "") -> list[dict]:
        signers = sorted({s["decided_by"] for s in rec["steps"] if s["decided_by"]})
        self.proposals.decide(rec["proposal_id"], who, "APPROVED", note or f"all {len(rec['steps'])} steps signed by {', '.join(signers)}", enforce=False)
        row = self.proposals.apply(rec["proposal_id"], only={s["code"] for s in rec["steps"]})   # what the plan still holds
        for spid in self._specialist_pids(rec):
            if (self.proposals.get(spid) or {}).get("status") == "PENDING_HUMAN":
                self.proposals.decide(spid, who, "APPROVED", f"signed inside tower plan {rec['plan_id']}", enforce=False)
        self._release(rec, "SHIPPED")
        for s in rec["steps"]:
            if s["code"] == "inter_dc_transfer" and s.get("cases"):
                self.store.extra.setdefault("transfers_inflight", []).append(
                    {"transfer_id": f"TR-{rec['week']:02d}-{s['from_dc']}-{rec['dc']}-{len(self.store.extra.get('transfers_inflight', [])) + 1:03d}",
                     "week": rec["week"], "from_dc": s["from_dc"], "to_dc": rec["dc"], "sku": rec["sku"], "cases": s["cases"],
                     "lands_day": int(s.get("lands_end_of_day") or s["lane_days"]), "status": "IN_TRANSIT", "plan_id": rec["plan_id"]})
        it = self._queue_item(rec)
        if it is not None and it.state == "PROPOSED":
            Q._transition(it, "APPROVED", self.now, f"signed via tower plan {rec['plan_id']} by {', '.join(signers)}")
            Q._transition(it, "APPLIED", self.now, f"sent {len(row.get('sent', []))} action(s) via the tower")
        rec["status"] = "APPLIED"
        rec["applied_at"] = self.now
        rec["history"].append({"at": self.now, "event": "applied", "note": f"sent: {', '.join(x['system'] + ':' + x['payload']['code'] for x in row.get('sent', []))}"})
        if rec["sku"]:
            rec["radar_after"] = self._project(rec["week"], rec["dc"], rec["sku"])
        self.bus.publish("plan.applied", {"plan_id": rec["plan_id"], "sent": [{"system": x["system"], "code": x["payload"]["code"]} for x in row.get("sent", [])]},
                         key=f"applied:{rec['plan_id']}")
        return row.get("sent", [])

    # ================================================================ SLA
    def _sla(self, ev: Event) -> str:
        qcfg = self.profile.raw.get("queue", {})
        pct = float(qcfg.get("reminder_at_pct", 0.5))
        ladder = qcfg.get("escalation", {"P1": ["escalate"], "P2": ["escalate"], "P3": []})
        reminded, escalated = [], []
        for p in self.plans.values():
            if p["status"] not in SIGNABLE:
                continue
            waiting = sorted({name_of(s["approver"]) for s in p["steps"] if s["decision"] is None})
            if not waiting:
                continue
            if p["reminded_at"] is None and p["created_at"] + pct * p["sla_hours"] <= self.now < p["due_at"]:
                p["reminded_at"] = self.now
                for who in waiting:
                    self.outbox.send(who, f"Reminder: {p['plan_id']} is due {clock_label(p['due_at'])}", f"{self._why(p)}. Open: {self.console_url}/#plan/{p['plan_id']}",
                                     "reminder", refs=[p["plan_id"]], at=self.now, priority=p["priority"])
                reminded.append(p["plan_id"])
            if self.now >= p["due_at"] and p["escalated_at"] is None:
                roles = ladder.get(p["priority"], [])
                names = [name_of(self.profile.approver_for(r, p["dc"])) for r in roles]
                if not names:
                    continue
                p["escalated_at"] = self.now
                p["escalated_to"] = [self.profile.approver_for(r, p["dc"]) for r in roles]
                p["escalated_to_names"] = names
                p["history"].append({"at": self.now, "event": "escalated", "note": f"SLA passed at {clock_label(p['due_at'])} waiting on {', '.join(waiting)}; {', '.join(names)} may now sign"})
                for who in names:
                    self.outbox.send(who, f"Escalation: {p['plan_id']} passed its {p['sla_hours']} h SLA",
                                     f"{p['priority']} {p['dc']} {p.get('sku') or 'all SKUs'}: {self._why(p)}. Waiting on {', '.join(waiting)} since {clock_label(p['created_at'])}. "
                                     f"You may sign for them: {self.console_url}/#plan/{p['plan_id']}", "escalation", refs=[p["plan_id"]], at=self.now, priority=p["priority"])
                it = self._queue_item(p)
                if it is not None:
                    it.escalated, it.escalated_at = True, self.now
                    it.history.append({"at": self.now, "event": "escalation", "note": f"via tower plan {p['plan_id']}: {', '.join(names)}"})
                escalated.append(p["plan_id"])
        return f"reminded {len(reminded)}, escalated {len(escalated)}" + (f": {', '.join(escalated)}" if escalated else "")

    # ================================================================ reading
    def radar_now(self, week: int) -> dict:
        """The radar as it stands now (holds, transfers in flight, delays), not as it stood at 06:30."""
        return risk.radar(self.store, self.profile, week)

    def cost_inr(self) -> float:
        return round(sum(r["meter"].get("cost_inr", 0) for r in self.runs), 2)

    def plan_view(self, rec: dict) -> dict:
        return {k: rec.get(k) for k in ("plan_id", "proposal_id", "key", "week", "dc", "sku", "mode", "kpi", "root_cause", "priority", "status", "version",
                                         "supersedes", "superseded_by", "created_at", "due_at", "trigger", "trigger_type", "exception_id", "steps", "dropped",
                                         "impact_inr", "specialists", "summary", "radar", "radar_after", "what_if", "reconcile", "escalated_at",
                                         "escalated_to", "history", "meter")} | {"due_label": clock_label(rec["due_at"]), "created_label": clock_label(rec["created_at"])}

    def state(self, journal_tail: int = 400) -> dict:
        weeks = sorted(set(self.queues) | set(self.radars))
        return {
            "company": self.profile.company, "currency": self.profile.currency, "brand": self.profile.raw.get("brand", ""), "horizon_days": LOCALE.get("horizon_days", 14),
            "now": self.now, "clock": clock_label(self.now), "weeks": weeks,
            "plans": [self.plan_view(p) for p in self.plans.values()],
            "radar": {str(w): self.radars[w] for w in self.radars},
            "radar_now": {str(w): self.radar_now(w) for w in self.radars},
            "queue": {str(w): self.queues[w].table(self.now) for w in self.queues},
            "inflight": self.store.extra.get("transfers_inflight", []), "lane_delays": self.store.extra.get("lane_delays", []),
            "reservations": self.store.extra.get("reservations", []),
            "outbox": self.outbox.items, "journal": self.bus.journal[-journal_tail:], "dead": self.bus.dead,
            "meter": {"tower_runs": len(self.runs), "cost_inr": self.cost_inr(),
                      "calls": sum(r["meter"].get("calls", 0) for r in self.runs)},
            "counts": {s: sum(1 for p in self.plans.values() if p["status"] == s) for s in
                       ("PROPOSED", "RECONCILING", "READY", "PARTIAL", "APPLIED", "REJECTED", "SUPERSEDED", "WATCH", "FAILED")},
        }

    # ================================================================ saving (a Lambda keeps its state in S3 between calls)
    def save(self, path: str | Path | None = None) -> Path:
        path = Path(path or self.folder / "state.json")
        s = self.store
        doc = {"profile": self.profile.raw.get("_name") or _profile_name(self.profile), "now": self.now, "seq": self._seq,
               "net": "GFB" if s.net is GFB else s.net.to_dict(), "weeks": s.weeks,
               "tables": {t: getattr(s, t) for t in TABLES}, "extra": s.extra,
               "plans": self.plans, "queues": {str(w): q.as_dict() for w, q in self.queues.items()}, "radars": {str(w): r for w, r in self.radars.items()},
               "views": self.views, "runs": self.runs, "outbox": self.outbox.items,
               "bus": {"journal": self.bus.journal, "seen": sorted(self.bus.seen), "dead": self.bus.dead, "n": self.bus._n}}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, default=str))
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: str | Path, client_factory=None, folder: str | Path | None = None, console_url: str | None = None) -> "TowerRuntime":
        doc = json.loads(Path(path).read_text())
        if doc["net"] == "GFB":
            net = GFB
        else:
            import yaml
            with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
                yaml.safe_dump(doc["net"], f)
            net = Network.from_yaml(f.name)
        store = Store(weeks=doc["weeks"], net=net, extra=doc["extra"], **doc["tables"])
        rt = cls(store, load_profile(doc["profile"]), client_factory, folder or Path(path).parent, console_url, now=doc["now"])
        rt._seq = doc["seq"]
        rt.plans = doc["plans"]
        for w, qd in doc["queues"].items():
            items = []
            for row in qd["items"]:
                row = dict(row)
                row.pop("key", None)
                items.append(Q.ExceptionItem(**row))
            rt.queues[int(w)] = Q.Queue(week=qd["week"], company=qd["company"], items=items, built_from=qd["built_from"], log=qd.get("log", []))
        rt.radars = {int(w): r for w, r in doc["radars"].items()}
        rt.views, rt.runs, rt.outbox.items = doc["views"], doc["runs"], doc["outbox"]
        rt.bus.journal, rt.bus.seen, rt.bus.dead, rt.bus._n = doc["bus"]["journal"], set(doc["bus"]["seen"]), doc["bus"]["dead"], doc["bus"]["n"]
        return rt


def _stale_dc(dropped: list[dict]) -> str | None:
    """Which DC's stale snapshot blocked a move, from the specialist's reason ("KOL snapshot 36.0 h old")."""
    for d in dropped:
        m = re.search(r"\b([A-Z]{3}) snapshot [0-9.]+ h old", d.get("reason") or "")
        if m:
            return m.group(1)
    return None


def _profile_name(profile: Profile) -> str:
    if profile.raw.get("_name"):
        return str(profile.raw["_name"])          # a company pack sets the path of its profile.yaml
    return "brewer" if "Brewing" in profile.company else "gfb"
