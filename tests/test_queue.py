"""Part 4: exception management as a queue. Deterministic grouping, ordering, the clock, the
state machine, and the digest agent on the scripted brain."""
import copy

import pytest

from foundry.data.generate import canonical_store
from foundry.profile import load_profile
from foundry.agents.rca_exception import queue as Q
from foundry.agents.rca_exception import queue_agent
from foundry.agents.rca_exception.queue_fake import FakeQueueClient


@pytest.fixture(scope="module")
def qstore():
    return canonical_store()


@pytest.fixture(scope="module")
def week12(qstore, profile):
    return Q.build_queue(qstore, profile, 12)


def keys(q):
    return [it.key for it in q.items]


def test_fourteen_deviations_become_six_exceptions_in_order(week12):
    assert week12.built_from == 14 and len(week12.items) == 6
    assert keys(week12) == ["CHN:MN-1L", "BHW:MP-1KG", "BHW:GC-200", "GGN:ALL", "KOL:MN-1L", "CHN:MP-1KG"]
    assert [it.priority for it in week12.items] == ["P1", "P1", "P1", "P1", "P3", "P3"]
    assert [it.impact_inr for it in week12.items[:4]] == sorted([it.impact_inr for it in week12.items[:4]], reverse=True)
    assert week12.items[0].id == "EXC-w12-001"


def test_dc_wide_cause_owns_its_sku_children(week12):
    ggn = week12.get("EXC-w12-004")
    assert ggn.key == "GGN:ALL" and ggn.root_cause == "DOCK_CONGESTION"
    assert [g["key"] for g in ggn.grouped] == ["GGN:LJ-1L", "GGN:MP-1KG", "GGN:GC-200", "GGN:MN-200"]
    assert len(ggn.children) == 4 and ggn.grouped_impact_inr == sum(g["impact_inr"] for g in ggn.grouped)
    assert ggn.impact_inr == 64354            # the DC-level number already covers every late case; children are not added twice


def test_same_dc_and_sku_is_one_exception_with_the_surest_headline(week12):
    chn = week12.items[0]
    assert chn.key == "CHN:MN-1L" and chn.kpi == "OTIF" and chn.root_cause == "TRANSPORT_DELAY" and chn.confidence == 0.95
    assert chn.kpis_moved == ["DOS", "OTIF", "FILL_RATE@DC", "OTIF@DC"]
    assert sorted(chn.children) == ["DOS:CHN:MN-1L:w12", "FILL_RATE:CHN:ALL:w12", "OTIF:CHN:ALL:w12"]
    assert chn.approvers[0].startswith("Priya Nambiar")


def test_stale_data_and_no_cause_are_never_urgent(week12):
    kol = week12.get("EXC-w12-005")
    assert kol.root_cause == "DATA_STALE" and kol.priority == "P3" and kol.impact_inr == 0 and kol.sla_hours == 72
    assert any("Rohan Patil" in a for a in kol.approvers) and kol.state == "PROPOSED"
    watch = week12.get("EXC-w12-006")
    assert watch.root_cause == "NO_CLEAR_CAUSE" and watch.state == "CLOSED"


def test_the_clock_reminds_then_escalates(week12, profile):
    q = copy.deepcopy(week12)
    r3 = Q.tick(q, profile, 3.0)
    assert sorted(r3["reminded"]) == ["EXC-w12-001", "EXC-w12-002", "EXC-w12-003", "EXC-w12-004"] and r3["escalated"] == []
    r5 = Q.tick(q, profile, 5.0)
    assert sorted(r5["escalated"]) == ["EXC-w12-001", "EXC-w12-002", "EXC-w12-003", "EXC-w12-004"]
    assert all("Ananya Kulkarni" in q.get(i).approvers[-1] for i in r5["escalated"])
    assert q.get("EXC-w12-001").approvers[0].startswith("Priya Nambiar")       # the original approver stays
    assert not q.get("EXC-w12-005").escalated                                   # the P3 clock runs to 72 h
    r5b = Q.tick(q, profile, 5.5)
    assert r5b["escalated"] == [] and r5b["reminded"] == []                    # idempotent


def test_an_answered_item_never_escalates(week12, profile):
    q = copy.deepcopy(week12)
    Q.decide(q, "EXC-w12-001", "Priya Nambiar", "APPROVED", 1.0, "transfer from KOL booked")
    r = Q.tick(q, profile, 5.0)
    assert "EXC-w12-001" not in r["escalated"] and q.get("EXC-w12-001").state == "APPROVED"
    out = Q.apply(q, "EXC-w12-001", 1.5)
    assert out["state"] == "APPLIED" and {s["system"] for s in out["sent"]} == {"TMS"}
    with pytest.raises(PermissionError):
        Q.apply(q, "EXC-w12-002", 1.5)                                          # not approved
    with pytest.raises(PermissionError):
        Q.decide(q, "EXC-w12-002", "Priya Nambiar", "APPROVED", 1.0)           # not her item
    Q.decide(q, "EXC-w12-002", "Meera Joshi", "REJECTED", 2.0, "the run is back on Thursday")
    assert q.get("EXC-w12-002").state == "CLOSED"


def test_escalate_tool_is_refused_before_the_sla(week12, profile):
    q = copy.deepcopy(week12)
    out = Q.escalate(q, profile, "EXC-w12-001", 1.0, "the VP asked")
    assert "error" in out and not q.get("EXC-w12-001").escalated
    out = Q.escalate(q, profile, "EXC-w12-001", 4.5, "SLA passed")
    assert out["escalated"] and "Ananya Kulkarni" in out["added"][0]


def test_brewer_clock_is_tighter(qstore):
    brewer = load_profile("brewer")
    q = Q.build_queue(qstore, brewer, 12)
    r = Q.tick(q, brewer, 3.0)
    assert len(r["escalated"]) >= 1 and all("Henrik Dahl" in q.get(i).approvers[-1] for i in r["escalated"])


def test_persistence_round_trip(week12, tmp_path):
    p = Q.to_json(week12, tmp_path / "q.json")
    q2 = Q.from_json(p)
    assert keys(q2) == keys(week12) and q2.get("EXC-w12-004").grouped == week12.get("EXC-w12-004").grouped
    assert q2.items[0].history[0]["to"] == "NEW"


def test_digest_agent_escalates_the_overdue_and_counts_truthfully(qstore, profile, levers, tmp_path):
    r = queue_agent.run({"mode": "digest", "week": 12, "now_hours": 6}, qstore, profile, FakeQueueClient(), levers, folder=tmp_path)
    assert r.stop_reason == "submitted"
    assert [t["tool"] for t in r.tool_calls] == ["list_queue", "next_items"] + ["escalate"] * 4 + ["write_digest"]
    assert r.digest["breaches"] == 4 and r.digest["p1_count"] == 4 and r.digest["p3_count"] == 1 and r.digest["money_at_stake_inr"] == 347373
    assert len(r.escalated) == 4 and len(r.digest["lines"]) == 5
    saved = Q.from_json(Q.queue_path(12, tmp_path))
    assert saved.get("EXC-w12-001").escalated and "Ananya Kulkarni" in saved.get("EXC-w12-001").approvers[-1]
    # the brain that ignores the clock reports no breaches: the eval trap Q12 catches it
    r2 = queue_agent.run({"mode": "digest", "week": 12, "now_hours": 6}, qstore, profile, FakeQueueClient(skip_escalation=True), levers, folder=tmp_path / "b")
    assert r2.digest["breaches"] == 0 and r2.escalated == []


def test_queue_sizing_across_the_history(qstore, profile):
    sizes = Q.size_history(qstore, profile)
    by_week = {s["week"]: s for s in sizes}
    assert by_week[12]["exceptions"] == 6 and by_week[12]["by_priority"]["P1"] == 4
    assert by_week[10]["exceptions"] == 0
    assert max(s["exceptions"] for s in sizes) == 6
