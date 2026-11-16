"""The three storylines The Control Room is taught on, scripted so that a test, the book and the
console demo all see the same events at the same clock times (hour 0 = Monday 06:30 IST).

  f11_monday    week 1, the opening position (F.5/F.11): the radar and the KPI monitor agree; Priya
                and Farhan sign; the transfer goes in flight; on Wednesday its truck breaks down and
                the tower asks Farhan to rescue the load. The tower remembers what it did.
  week12        the incident week: six exceptions, five plans, the radar folding into them; Rohan's
                refresh; a refused signature, a double click, an escalation, the VP signing for a DC
                manager; on Tuesday a Nashik transporters' strike, a re-plan that now moves 500 cases,
                and a pre-empt that finds the only lever already pulled.
  one_sender    two DCs short of MN-1L, one long DC that can feed both: the reconciler holds
                Bhiwandi's spare for Gurgaon (dry first) and adds Neha's co-sign to Chennai's move.

Each storyline returns (runtime, log); log is one entry per step with what happened and the state.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from foundry.data.generate import canonical_store
from foundry.orchestrator.runtime import TowerRuntime
from foundry.profile import load_profile
from foundry.store import Store


def _live_id(rt: TowerRuntime, dc: str, sku: str | None) -> str:
    rows = [p for p in rt.plans.values() if p["dc"] == dc and p["sku"] == sku and p["status"] in ("READY", "PARTIAL")]
    return rows[-1]["plan_id"]


def _step(rt: TowerRuntime, log: list, label: str, fn: Callable[[], dict], said: str = "") -> dict:
    seen_msgs = len(rt.outbox.items)
    out = fn()
    log.append({"label": label, "said": said, "at": out["at"], "clock": out["clock"], "accepted": out["accepted"],
                "events": out["events"], "messages": rt.outbox.items[seen_msgs:],
                "counts": rt.state()["counts"], "cost_inr": rt.cost_inr()})
    return out


def f11_monday(folder: str | Path, client_factory=None) -> tuple[TowerRuntime, list]:
    rt = TowerRuntime(Store.from_position(), load_profile("gfb"), client_factory, folder)
    log: list = []
    _step(rt, log, "Monday 06:30: the cron fires", lambda: rt.monday(1), "EventBridge, cron(0 1 ? * MON *)")
    pid = _live_id(rt, "CHN", "MN-1L")
    _step(rt, log, "Priya signs the transfer", lambda: rt.decide(pid, "Priya Nambiar", "APPROVED", at=2.67), "from the link in her email")
    _step(rt, log, "Farhan books the truck", lambda: rt.decide(pid, "Farhan Qureshi", "APPROVED", at=3.17), "the plan is applied")
    tid = rt.store.extra["transfers_inflight"][0]["transfer_id"]
    _step(rt, log, "Wednesday 11:00: the truck breaks down", lambda: rt.transfer_delay(tid, 6, "truck broke down near Vijayawada", at=52.5),
          "the carrier's feed: lands day 6, not day 4")
    rescue = _live_id(rt, "CHN", "MN-1L")
    _step(rt, log, "Farhan sends a relief truck", lambda: rt.decide(rescue, "Farhan Qureshi", "APPROVED", at=53.5),
          "the tower does not assume the rescue worked; it waits for the feed")
    _step(rt, log, "Thursday 08:00: the relief truck's ETA", lambda: rt.transfer_delay(tid, 5, "relief truck from Vijayawada", at=73.5),
          "lands day 5: one short day left, under the pre-empt line")
    return rt, log


def week12(folder: str | Path, client_factory=None) -> tuple[TowerRuntime, list]:
    rt = TowerRuntime(canonical_store(), load_profile("gfb"), client_factory, folder)
    log: list = []
    _step(rt, log, "Monday 06:30: the cron fires", lambda: rt.monday(12), "fourteen deviations, six exceptions")
    _step(rt, log, "09:00 Rohan refreshes Kolkata", lambda: rt.refresh(12, "KOL", "MN-1L", at=2.5), "the stale snapshot was blocking Chennai")
    mp = _live_id(rt, "BHW", "MP-1KG")
    _step(rt, log, "09:30 Meera signs the plant expedite", lambda: rt.decide(mp, "Meera Joshi", "APPROVED", at=3.0))
    _step(rt, log, "09:42 Kabir signs fair-share allocation", lambda: rt.decide(mp, "Kabir Shaikh", "APPROVED", at=3.2))
    _step(rt, log, "09:48 Kabir clicks Approve again", lambda: rt.decide(mp, "Kabir Shaikh", "APPROVED", at=3.3), "the key was seen: ignored")
    chn = _live_id(rt, "CHN", "MN-1L")
    _step(rt, log, "09:54 Priya tries to sign Chennai", lambda: rt.decide(chn, "Priya Nambiar", "APPROVED", at=3.4), "she has no step on this plan")
    gc = _live_id(rt, "BHW", "GC-200")
    _step(rt, log, "10:15 Kabir rejects the promo cap", lambda: rt.decide(gc, "Kabir Shaikh", "REJECTED", "the Diwali promo is contracted with the retailer; capping it breaches the deal", at=3.75))
    _step(rt, log, "11:00 the SLA clock ticks", lambda: rt.tick(at=4.5), "P1 plans unsigned at 10:30 escalate")
    ggn = _live_id(rt, "GGN", None)
    _step(rt, log, "11:30 Ananya signs for Vikram", lambda: rt.decide(ggn, "Ananya Kulkarni", "APPROVED", at=5.0), "escalated plans can be signed by the VP")
    _step(rt, log, "Tuesday 14:00: the Nashik strike", lambda: rt.lane_delay(12, "NSK", "*", 4, "Nashik transporters' strike: no new dispatches",
                                                                              carrier="all Nashik carriers", applies_to="new", at=31.5),
          "trucks on the road keep going; nothing new leaves Nashik for four days")
    chn = _live_id(rt, "CHN", "MN-1L")
    _step(rt, log, "15:00 Priya signs the 500-case move", lambda: rt.decide(chn, "Priya Nambiar", "APPROVED", at=32.5))
    _step(rt, log, "15:20 Farhan signs the rest", lambda: rt.decide(chn, "Farhan Qureshi", "APPROVED", at=32.83))
    return rt, log


def one_sender(folder: str | Path, client_factory=None) -> tuple[TowerRuntime, list]:
    s = Store.from_position()
    for r in s.inventory:
        if r["sku"] == "MN-1L" and r["dc"] == "BHW":
            r["on_hand"] = 17000          # the plant pushed a big run into Bhiwandi
        if r["sku"] == "MN-1L" and r["dc"] == "GGN":
            r["on_hand"] = 1000           # Gurgaon sold through a weekend promotion
    rt = TowerRuntime(s, load_profile("gfb"), client_factory, folder)
    log: list = []
    _step(rt, log, "Monday 06:30: two DCs short, one sender", lambda: rt.monday(1), "both specialists pick Bhiwandi")
    ggn = _live_id(rt, "GGN", "MN-1L")
    chn = _live_id(rt, "CHN", "MN-1L")
    _step(rt, log, "Vikram signs Gurgaon's move", lambda: rt.decide(ggn, "Vikram Sethi", "APPROVED", at=1.0))
    _step(rt, log, "Farhan books Gurgaon's truck", lambda: rt.decide(ggn, "Farhan Qureshi", "APPROVED", at=1.2))
    _step(rt, log, "Neha co-signs for Bhiwandi", lambda: rt.decide(chn, "Neha Rane", "APPROVED", at=1.5), "the reconciler added her step")
    _step(rt, log, "Priya signs Chennai's move", lambda: rt.decide(chn, "Priya Nambiar", "APPROVED", at=1.7))
    _step(rt, log, "Farhan books Chennai's truck", lambda: rt.decide(chn, "Farhan Qureshi", "APPROVED", at=1.9))
    return rt, log


STORYLINES = {"f11_monday": ("The F.11 Monday", f11_monday), "week12": ("Week 12, everything at once", week12),
              "one_sender": ("Two DCs, one sender", one_sender)}
