import json

from foundry.bedrock_client import FakeBedrockClient
from foundry.agents.rca_exception import agent


def test_diagnose_runs_the_method_in_order(store, profile, levers, proposals):
    r = agent.run({"mode": "diagnose", "dc": "CHN", "sku": "MN-1L", "week": 12, "kpi": "OTIF"}, store, profile, FakeBedrockClient(), levers, proposals)
    assert r.stop_reason == "submitted"
    assert [t["tool"] for t in r.tool_calls] == ["gather_evidence", "estimate_impact", "propose_actions", "submit_diagnosis"]
    assert r.diagnosis["root_cause"] == "TRANSPORT_DELAY" and r.diagnosis["priority"] == "P1"
    assert r.proposal_id and proposals.get(r.proposal_id)["status"] == "PENDING_HUMAN"
    assert r.meter["cost_usd"] > 0


def test_sweep_picks_the_worst_deviation(store, profile, levers, proposals):
    r = agent.run({"mode": "sweep", "week": 12}, store, profile, FakeBedrockClient(), levers, proposals)
    assert r.tool_calls[0]["tool"] == "list_kpi_deviations"
    assert r.diagnosis["root_cause"] in {"SUPPLY_MISS", "TRANSPORT_DELAY", "DOCK_CONGESTION", "DEMAND_SPIKE"}


def test_kill_switch_stops_the_loop(store, profile, levers, proposals):
    levers.set(kill_switch=True)
    r = agent.run({"mode": "diagnose", "dc": "CHN", "sku": "MN-1L", "week": 12}, store, profile, FakeBedrockClient(), levers, proposals)
    assert r.stop_reason == "killed" and r.diagnosis is None and r.proposal_id is None


def test_disabled_tool_is_refused_and_loop_survives(store, profile, levers, proposals):
    levers.set(disabled_tools=["propose_actions"])
    r = agent.run({"mode": "diagnose", "dc": "CHN", "sku": "MN-1L", "week": 12}, store, profile, FakeBedrockClient(), levers, proposals)
    refused = [t for t in r.tool_calls if t["tool"] == "propose_actions"]
    assert refused and refused[0]["allowed"] is False
    assert r.proposal_id is None


def test_model_outage_fails_cleanly(store, profile, levers, proposals):
    r = agent.run({"mode": "diagnose", "dc": "CHN", "sku": "MN-1L", "week": 12}, store, profile, FakeBedrockClient(fail_at_turn=2), levers, proposals)
    assert r.stop_reason == "model_error" and "outage" in r.error


def test_budget_stops_a_runaway(store, profile, levers, proposals):
    r = agent.run({"mode": "diagnose", "dc": "CHN", "sku": "MN-1L", "week": 12}, store, profile, FakeBedrockClient(), levers, proposals, budget_usd=0.001)
    assert r.stop_reason == "budget"


def test_propose_only_the_agent_cannot_apply(store, profile, levers, proposals):
    r = agent.run({"mode": "diagnose", "dc": "CHN", "sku": "MN-1L", "week": 12}, store, profile, FakeBedrockClient(), levers, proposals)
    import pytest
    with pytest.raises(PermissionError):
        proposals.apply(r.proposal_id)                      # pending, not approved
    proposals.decide(r.proposal_id, "Priya Nambiar", "APPROVED", "go")
    applied = proposals.apply(r.proposal_id)
    assert applied["status"] == "APPLIED" and applied["sent"][0]["system"] in ("ERP", "TMS")


def test_panel_fails_closed_when_corrupt(tmp_path):
    from foundry.levers import Levers
    (tmp_path / "panel.json").write_text("{not json")
    lv = Levers(tmp_path / "panel.json", tmp_path / "audit.jsonl")
    assert lv.decide("gather_evidence").allowed is False
