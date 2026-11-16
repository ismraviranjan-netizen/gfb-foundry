"""Regression tests for the defects The Agent Desk's writer found while writing exercises (8 Nov)."""
import pytest

from foundry.agents.rca_exception import queue as Q
from foundry.agents.rebalancing.tools import Dispatcher as RblDispatcher
from foundry.approval import ProposalStore
from foundry.data.generate import canonical_store
from foundry.levers import Levers, register_tools
from foundry.loop import run_loop
from foundry.scripted import ScriptedClient
from foundry.store import Store
from foundry.tower import routing


def test_a_proposal_is_decided_once_and_applied_once(tmp_path):
    ps = ProposalStore(tmp_path / "p.jsonl")
    row = ps.propose("CHN", "MN-1L", 1, "X", [{"code": "inter_dc_transfer", "approver": "Priya Nambiar (dc_manager)"}], "r", "run")
    with pytest.raises(PermissionError):
        ps.decide(row["proposal_id"], "Meera Joshi", "APPROVED")              # not her proposal
    ps.decide(row["proposal_id"], "Priya Nambiar", "APPROVED")
    ps.apply(row["proposal_id"])
    with pytest.raises(ValueError):
        ps.decide(row["proposal_id"], "Priya Nambiar", "APPROVED")            # re-opening an applied proposal
    with pytest.raises(PermissionError):
        ps.apply(row["proposal_id"])                                          # sending the actions twice


def test_a_tool_that_raises_does_not_crash_the_loop(tmp_path):
    register_tools(read={"boom", "finish"})
    class Brain(ScriptedClient):
        def next_call(self, task, results, seen):
            return ("boom", {}) if "boom" not in seen else ("finish", {"answer": results[0]["content"]["error"]})

    def dispatch(name, inp):
        raise KeyError("week")
    spec = [{"toolSpec": {"name": n, "description": n, "inputSchema": {"json": {"type": "object"}}}} for n in ("boom", "finish")]
    r = run_loop({}, "s", spec, dispatch, Brain(), Levers(tmp_path / "panel.json", tmp_path / "a.jsonl"), finish_tool="finish", required=("answer",))
    assert r.stop_reason == "submitted" and "KeyError" in r.final["answer"]


def test_propose_transfer_refuses_a_move_the_maths_did_not_price(tmp_path, profile):
    d = RblDispatcher(Store.from_position(), profile, ProposalStore(tmp_path / "p.jsonl"), "run")
    out = d.run("propose_transfer", {"from_dc": "KOL", "to_dc": "CHN", "sku": "MN-1L", "cases": 7000, "week": 1, "rationale": "more is safer"})
    assert "refused" in out["error"] and "3720" in out["error"]
    ok = d.run("propose_transfer", {"from_dc": "KOL", "to_dc": "CHN", "sku": "MN-1L", "cases": 3720, "week": 1, "rationale": "F.11"})
    assert ok["proposal_id"]


def test_a_p3_past_its_sla_is_breached_not_escalated(profile):
    q = Q.build_queue(canonical_store(), profile, 12, opened_at=0.0, propose=True)
    p3 = [it for it in q.items if it.priority == "P3" and it.state == "PROPOSED"]
    assert p3
    Q.tick(q, profile, 100.0)
    Q.tick(q, profile, 101.0)
    for it in p3:
        assert not it.escalated and sum(h.get("event") == "breach" for h in it.history) == 1


def test_a_consulted_specialist_verdict_is_never_silently_lost():
    diag = {"actions_detail": [{"code": "raise_production_lot", "approver": "Meera Joshi (plant_planner)", "text": "raise the lot"}]}
    out = routing.merge(diag, {"transfer": None, "reason": "no fresh DC is long of GC-200 with a lane into BHW"}, None)
    assert any(d["by"] == "rebalancing" and "considered" in d["reason"] for d in out["dropped"])
