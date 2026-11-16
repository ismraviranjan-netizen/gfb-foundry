"""The rebalancing agent: the F.11 numbers exactly, the guards, the loop, the brewer profile."""
import json

import pytest

from foundry.store import Store
from foundry.data.generate import canonical_store
from foundry.profile import load_profile
from foundry.levers import Levers
from foundry.approval import ProposalStore
from foundry.agents.rebalancing import logic, agent
from foundry.agents.rebalancing.fake import FakeRebalancingClient


def opening(overrides=None):
    """The opening position (F.5) with inventory rows overridden: overrides = {("KOL","MN-1L"): (on_hand, open_receipt, age)}."""
    s = Store.from_position()
    for (dc, sku), (oh, orc, age) in (overrides or {}).items():
        for r in s.inventory:
            if r["dc"] == dc and r["sku"] == sku:
                r["on_hand"], r["open_receipt"], r["snapshot_age_h"] = oh, orc, age
    return s


def test_f11_numbers_are_reproduced_exactly(profile):
    t = logic.plan_transfer(opening(), profile, 1, "MN-1L")["transfer"]
    assert (t["from_dc"], t["to_dc"]) == ("KOL", "CHN")
    assert t["cases"] == 3720 and t["need_cases"] == 3720 and t["surplus_cases"] == 6300
    assert t["lane_days"] == 4 and t["lane_cost"] == 81840 and t["margin_saved"] == 184229
    assert t["pays"] and not t["co_sign_needed"] and t["days_short_avoided"] == 8
    assert [a["approver"] for a in t["approvals"]] == ["Priya Nambiar (dc_manager)", "Farhan Qureshi (logistics_lead)"]


def test_position_rows_carry_the_three_formulas(profile):
    rows = {r["dc"]: r for r in logic.position(opening(), profile, 1, "MN-1L")}
    assert rows["CHN"]["dos"] == 4.0 and rows["KOL"]["dos"] == 63.0 and rows["BHW"]["dos"] == 9.0 and rows["GGN"]["dos"] == 10.5
    assert rows["CHN"]["rop"] == 6247 and rows["CHN"]["ss"] == 1047 and rows["KOL"]["rop"] == 1395
    assert rows["CHN"]["status"] == "short" and rows["KOL"]["status"] == "long" and rows["BHW"]["status"] == "ok"
    assert rows["CHN"]["days_to_next_receipt"] == 12 and rows["BHW"]["days_to_next_receipt"] == 2


def test_stale_sender_is_excluded_and_the_agent_says_refresh(profile):
    plan = logic.plan_transfer(canonical_store(), profile, 12, "MN-1L")      # week 12: KOL's snapshot is 36 h old
    assert plan["transfer"] is None and plan["alternative"] == "refresh_snapshot"
    assert "KOL snapshot 36.0 h old" in plan["reason"]
    # a fresh long sender elsewhere is taken even when a stale one is cheaper
    t = logic.plan_transfer(opening({("BHW", "MN-1L"): (17000, 0, 30.0)}), profile, 1, "MN-1L")["transfer"]
    assert t["from_dc"] == "KOL"


def test_both_short_means_no_transfer(profile):
    plan = logic.plan_transfer(opening({("CHN", "MN-1L"): (1000, 0, 2.0), ("GGN", "MN-1L"): (1000, 0, 2.0), ("KOL", "MN-1L"): (3000, 0, 2.0)}), profile, 1, "MN-1L")
    assert plan["transfer"] is None and plan["alternative"] == "expedite_plant_run"


def test_minimum_transfer_and_wait_for_the_truck(profile):
    small = logic.plan_transfer(opening({("CHN", "TG-250"): (130, 0, 2.0), ("KOL", "TG-250"): (600, 0, 2.0)}), profile, 1, "TG-250")
    assert small["transfer"] is None and "below the minimum" in small["reason"]
    wait = logic.plan_transfer(opening({("CHN", "MN-1L"): (0, 5000, 2.0)}), profile, 1, "MN-1L")
    assert wait["transfer"] is None and "wait for the truck" in wait["reason"]


def test_a_small_slipped_truck_does_not_end_the_shortage(profile):
    """The Control Room, what integration found: 2,000 slipped cases land on day 2 and run out on day 7;
    the plant's truck lands on day 12. The one-receipt rule said wait; the shared projection prices the gap."""
    t = logic.plan_transfer(opening({("CHN", "MN-1L"): (0, 2000, 2.0)}), profile, 1, "MN-1L")["transfer"]
    assert t["from_dc"] == "KOL" and t["cases"] == 3200 and t["pays"] and t["margin_saved"] == 106286


def test_lane_choice_cheapest_in_time_else_fastest_that_lands_first(profile):
    both_long = logic.plan_transfer(opening({("BHW", "MN-1L"): (17000, 0, 2.0)}), profile, 1, "MN-1L")["transfer"]
    assert both_long["from_dc"] == "BHW" and both_long["lane_days"] == 3          # 18/case beats 22/case, and 3 days lands in time
    dry = logic.plan_transfer(opening({("CHN", "MN-1L"): (0, 0, 2.0)}), profile, 1, "MN-1L")["transfer"]
    assert dry["from_dc"] == "KOL" and not dry["arrives_in_time"] and dry["cases"] == 5200 and dry["pays"]


def test_co_sign_between_the_long_line_and_the_rop(profile):
    t = logic.plan_transfer(opening({("KOL", "MN-1L"): (4000, 0, 2.0), ("CHN", "MN-1L"): (0, 0, 2.0)}), profile, 1, "MN-1L")["transfer"]
    assert t["cases"] == 2605 and t["co_sign_needed"] and t["leaves_sender_cases"] == 1395
    assert [a["code"] for a in t["approvals"]] == ["inter_dc_transfer", "co_sign_transfer", "book_truck"]
    assert t["approvals"][1]["approver"] == "Arjun Dutta (dc_manager)"


def test_a_move_that_does_not_pay_is_not_proposed(profile):
    plan = logic.plan_transfer(opening({("CHN", "LJ-1L"): (100, 0, 2.0), ("KOL", "LJ-1L"): (3000, 0, 2.0)}), profile, 1, "LJ-1L")
    assert plan["transfer"] is None and "does not pay" in plan["reason"]
    assert plan["economics"]["margin_saved"] < plan["economics"]["lane_cost"] + plan["economics"]["holding_cost"]


def test_agent_runs_the_method_in_order_and_proposes(profile, levers, proposals):
    r = agent.run({"mode": "transfer", "sku": "MN-1L", "week": 1}, opening(), profile, FakeRebalancingClient(), levers, proposals)
    assert r.stop_reason == "submitted"
    assert [c["tool"] for c in r.tool_calls] == ["stock_position", "lane_options", "transfer_economics", "propose_transfer", "submit_plan"]
    assert r.plan["cases"] == 3720 and r.plan["cost_inr"] == 81840 and r.plan["margin_saved_inr"] == 184229
    row = proposals.get(r.proposal_id)
    assert row["kind"] == "rebalance" and row["status"] == "PENDING_HUMAN"
    assert proposals.approvers(r.proposal_id) == ["Farhan Qureshi (logistics_lead)", "Priya Nambiar (dc_manager)"]
    assert r.meter["calls"] == 5


def test_sweep_takes_the_worst_short_dc_with_a_partner(profile, levers, proposals):
    r = agent.run({"mode": "sweep", "week": 1}, opening(), profile, FakeRebalancingClient(), levers, proposals)
    assert r.stop_reason == "submitted" and r.tool_calls[0]["tool"] == "list_shortages"
    assert (r.plan["from_dc"], r.plan["to_dc"], r.plan["sku"], r.plan["cases"]) == ("KOL", "CHN", "MN-1L", 3720)
    quiet = agent.run({"mode": "sweep", "week": 1}, opening({("CHN", "MN-1L"): (4000, 0, 2.0)}), profile, FakeRebalancingClient(), levers, proposals)
    assert quiet.plan["cases"] == 0 and quiet.plan["from_dc"] is None


def test_propose_transfer_refuses_a_stale_sender_even_if_asked(profile, levers, proposals):
    r = agent.run({"mode": "transfer", "sku": "MN-1L", "week": 12}, canonical_store(), profile, FakeRebalancingClient(ignore_freshness=True), levers, proposals)
    refused = [c for c in r.tool_calls if c["tool"] == "propose_transfer"]
    assert refused and refused[0]["keys"] == ["error"]
    assert r.proposal_id is None and proposals.pending() == []


def test_kill_switch_and_propose_only_hold_for_this_agent(profile, levers, proposals):
    levers.set(kill_switch=True)
    r = agent.run({"mode": "transfer", "sku": "MN-1L", "week": 1}, opening(), profile, FakeRebalancingClient(), levers, proposals)
    assert r.stop_reason == "killed" and r.plan is None
    levers.set(kill_switch=False)
    assert not levers.decide("create_sto").allowed


def test_brewer_profile_changes_cover_minimum_and_names():
    brewer = load_profile("brewer")
    t = logic.plan_transfer(opening(), brewer, 1, "MN-1L")["transfer"]
    assert t["cases"] == 6320 and t["receiver_dos_after"] == 21.0          # target cover 21 days, not 14
    assert t["co_sign_needed"]                                               # 6,320 is more than KOL's 4,200 above the 35-day line
    assert [a["approver"] for a in t["approvals"]] == ["Lotte de Vries (opco_planner)", "Sven Halvorsen (site_manager)", "Marek Nowak (transport_manager)"]
    assert brewer.raw["rebalance"]["min_transfer_cases"] == 300


def test_rebalancing_eval_gate_passes_offline(tmp_path):
    import subprocess, sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    out = tmp_path / "r.json"
    res = subprocess.run([sys.executable, "evals/run_rebalancing_evals.py", "--out", str(out)], cwd=root, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout[-800:]
    summary = json.loads(out.read_text())["summary"]
    assert summary["passed"] == summary["cases"] == 21 and summary["traps_all_pass"]
