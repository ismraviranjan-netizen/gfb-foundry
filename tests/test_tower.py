"""Part 5: the tower routes, merges and proposes; it never executes and never re-prices."""
import subprocess
import sys
from pathlib import Path

from foundry.data.generate import canonical_store
from foundry.store import Store
from foundry.tower import agent, routing
from foundry.tower.fake import FakeTowerClient

ROOT = Path(__file__).resolve().parent.parent


def test_route_table():
    assert routing.route("TRANSPORT_DELAY", [], 12, 12) == ["rebalancing"]
    assert routing.route("DOCK_CONGESTION", ["INVENTORY_IMBALANCE"], 12, 12) == ["rebalancing"]
    assert routing.route("DEMAND_SPIKE", [], 12, 12) == ["rebalancing", "sensing"]
    assert routing.route("DEMAND_SPIKE", [], 8, 8) == ["rebalancing"]          # too few weeks for sensing
    assert routing.route("DATA_STALE", [], 12, 12) == []


def test_specialist_verdict_beats_generic_step():
    diag = {"actions_detail": [{"code": "inter_dc_transfer", "approver": "Priya", "text": "generic"}, {"code": "inform_customers", "approver": "Farhan", "text": "tell"}]}
    m = routing.merge(diag, {"transfer": None, "reason": "wait for the truck", "alternative": "expedite_alternate_carrier"}, None)
    assert [s["code"] for s in m["plan"]] == ["expedite_alternate_carrier", "inform_customers"]
    assert m["dropped"][0]["by"] == "rebalancing"
    m2 = routing.merge(diag, {"transfer": {"cases": 3720, "from_dc": "KOL", "to_dc": "CHN", "lane_days": 4, "lane_cost": 81840, "margin_saved": 184229, "summary": "move"},
                              "approvals": [{"code": "inter_dc_transfer", "approver": "Priya Nambiar (dc_manager)"}, {"code": "book_truck", "approver": "Farhan Qureshi (logistics_lead)"}],
                              "proposal_id": "PRP-x"}, None)
    codes = [s["code"] for s in m2["plan"]]
    assert codes == ["inter_dc_transfer", "book_truck", "inform_customers"]
    assert m2["plan"][0]["cases"] == 3720 and m2["plan"][0]["source"] == "rebalancing"


def test_sarah_flow_on_the_position(profile, levers, proposals):
    r = agent.run({"mode": "breach", "dc": "CHN", "sku": "MN-1L", "week": 1, "kpi": "DOS"}, Store.from_position(), profile, FakeTowerClient(), levers, proposals)
    assert r.stop_reason == "submitted"
    assert r.plan["specialists_called"] == ["rca", "rebalancing"]
    t = r.plan["plan"][0]
    assert t["code"] == "inter_dc_transfer" and t["cases"] == 3720 and t["from_dc"] == "KOL"
    assert {a["approver"] for a in r.plan["approvals"]} == {"Priya Nambiar (dc_manager)", "Farhan Qureshi (logistics_lead)"}
    assert r.plan["proposal_id"] and proposals.get(r.plan["proposal_id"])["kind"] == "plan"
    assert r.total_meter["calls"] == r.meter["calls"] + sum(m["calls"] for m in r.specialists.values())


def test_stale_kolkata_drops_the_transfer(store, profile, levers, proposals):
    r = agent.run({"mode": "breach", "dc": "CHN", "sku": "MN-1L", "week": 12, "kpi": "OTIF"}, store, profile, FakeTowerClient(), levers, proposals)
    assert [d["code"] for d in r.plan["dropped"]] == ["inter_dc_transfer", "expedite_alternate_carrier"]
    assert "36.0 h old" in r.plan["dropped"][0]["reason"]
    assert "no rush can help" in r.plan["dropped"][1]["reason"]          # finding F1: the late trucks land first
    assert [s["code"] for s in r.plan["plan"]] == ["refresh_snapshot", "inform_customers"]


def test_tower_never_sees_a_write_tool_of_a_specialist():
    from foundry.tower.tools import TOOL_NAMES
    for forbidden in ("propose_transfer", "propose_actions", "propose_adjustment", "execute_action", "create_sto", "write_forecast"):
        assert forbidden not in TOOL_NAMES


def test_disabled_specialist_is_reported_not_replaced(profile, levers, proposals):
    levers.set(disabled_tools=["call_rebalancing"])
    r = agent.run({"mode": "breach", "dc": "CHN", "sku": "MN-1L", "week": 1, "kpi": "DOS"}, Store.from_position(), profile, FakeTowerClient(), levers, proposals)
    assert r.plan["specialists_called"] == ["rca"]
    assert "disabled" in r.plan["plan"][0]["note"]


def test_kill_switch_kills_the_tower_and_its_specialists(store, profile, levers, proposals):
    levers.set(kill_switch=True)
    r = agent.run({"mode": "breach", "dc": "CHN", "sku": "MN-1L", "week": 12, "kpi": "OTIF"}, store, profile, FakeTowerClient(), levers, proposals)
    assert r.stop_reason == "killed" and r.plan is None and proposals.pending() == []


def test_quiet_week_sweep_is_empty(store, profile, levers, proposals):
    r = agent.run({"mode": "sweep", "week": 10}, store, profile, FakeTowerClient(), levers, proposals)
    assert r.plan["plan"] == [] and r.plan["specialists_called"] == [] and r.plan["proposal_id"] is None


def test_tower_eval_gate_passes():
    out = subprocess.run([sys.executable, "evals/run_tower_evals.py", "--out", "/tmp/tower_results_test.json"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout[-800:]
