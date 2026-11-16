"""The demand sensing agent: the maths on known numbers, the rule's three parts, the loop on the
scripted brain, the brewer profile, and the eval gate."""
import subprocess
import sys
from pathlib import Path

import pytest

from foundry.data.generate import Injection, build, sensing_history
from foundry.profile import load_profile
from foundry.agents.sensing import agent, logic
from foundry.agents.sensing.fake import FakeSensingClient


@pytest.fixture(scope="module")
def history():
    return sensing_history()


def test_mape_and_bias_on_known_numbers():
    assert logic.mape([100, 200], [90, 220]) == 10.0           # (10% + 10%) / 2
    assert round(logic.bias([100, 200], [90, 220]), 2) == 1.01  # (+11.1% - 9.1%) / 2: the forecast ran a little low
    assert logic.mape([], []) == 0.0 and logic.bias([0, 0], [0, 0]) == 0.0


def test_exp_smooth_on_a_known_series():
    # level starts at mean(100, 110, 120) = 110; then 0.35*130 + 0.65*110 = 117; then 0.35*140 + 0.65*117 = 125.05
    assert round(logic.exp_smooth([100, 110, 120, 130, 140], 0.35), 2) == 125.05
    assert logic.exp_smooth([], 0.35) == 0.0
    assert logic.exp_smooth([50, 50, 50, 50], 0.5) == 50.0


def test_promo_uplift_is_learned_from_history(history, profile):
    own = logic.promo_uplift(history, profile, "GGN", "GC-200", 18, 19)      # Holi and the monsoon promo are its own history
    assert own["source"] == "own history" and own["observations"] == 3
    assert own["calendar"] == 0.5 and own["realisation"] == 0.81 and own["uplift"] == 0.405
    borrowed = logic.promo_uplift(history, profile, "BHW", "GC-200", 13, 14)  # Bhiwandi has no chakli promo of its own yet
    assert borrowed["source"] == "same SKU, other DCs" and borrowed["uplift"] == 0.81
    none = logic.promo_uplift(history, profile, "BHW", "GC-200", 13, 16)      # no promo in week 16
    assert none["uplift"] is None and none["source"] == "unknown"


def test_backtest_on_chennai_picks_a_blend_that_beats_the_consensus(history, profile):
    bt = logic.backtest(history, profile, "CHN", "MN-1L", 17)
    assert bt["ok"] and bt["holdout_weeks"] == [10, 11, 12, 13, 14, 15, 16, 17]
    assert bt["alpha"] == 0.2 and bt["w"] == 0.0
    assert bt["mape_consensus"] == 8.65 and bt["mape_blend"] == 7.45 and bt["improvement_pct"] == 0.139
    assert bt["wins"] == 5 and bt["mape_blend"] < bt["mape_consensus"]
    assert any(g["w"] == 1.0 and g["mape"] == bt["mape_consensus"] for g in bt["grid"])   # w = 1 is "keep the consensus"


def test_sense_is_silent_on_pure_noise(profile):
    noise = build([], seed=101, weeks=20)
    sn = logic.sense(noise, profile, "CHN", "MN-1L", 16)
    assert sn.decision == "silent" and sn.co_sign_needed is False and sn.confidence == 0.35
    assert sn.backtest["w"] == 1.0 and sn.backtest["improvement_pct"] == 0.0
    assert all(f["adjustment_pct"] == 0.0 and f["sensed"] == f["consensus"] for f in sn.forecasts)


def test_a_lone_jump_is_not_chased(profile):
    spike = build([Injection("demand_spike", 16, "BHW", "MN-1L", {"pct": 1.0})], seed=104, weeks=20)
    sn = logic.sense(spike, profile, "BHW", "MN-1L", 16)
    assert sn.decision == "silent"
    assert sn.run_rule["rule"] == "spike (unconfirmed)" and sn.run_rule["jump_pct"] == 1.0


def test_confirm_rule_needs_two_weeks_on_the_same_side(history, profile):
    sn = logic.sense(history, profile, "KOL", "MN-1L", 16)
    assert sn.decision == "silent"
    assert sn.reasons[0].startswith("the series has sat above the consensus for 1 week(s); the rule asks for 2")
    assert sn.backtest["improvement_pct"] == 0.203          # the back-test alone would have said yes


def test_chennai_at_origin_17_adjusts_for_the_promo_and_the_trend(history, profile):
    sn = logic.sense(history, profile, "CHN", "MN-1L", 17)
    assert sn.decision == "adjust" and sn.co_sign_needed and sn.confidence == 0.65
    assert sn.consensus == [2600.0, 2600.0] and sn.sensed == [4270.3, 2875.6] and sn.adjustment_pct == [0.642, 0.106]
    assert sn.forecasts[0]["promo"] == "Pongal pack" and sn.forecasts[0]["uplift"] == 0.485
    assert sn.run_rule == {"rule": "trend up", "weeks_above": 3}


def test_agent_run_submits_and_proposes_to_kabir_and_devika(history, profile, levers, proposals):
    res = agent.run({"mode": "sense", "dc": "CHN", "sku": "MN-1L", "origin_week": 17, "horizon": 2}, history, profile, FakeSensingClient(), levers, proposals)
    assert res.stop_reason == "submitted" and res.turns == 5
    assert [t["tool"] for t in res.tool_calls] == ["get_series", "forecast_error", "blend", "propose_adjustment", "submit_sensing"]
    assert res.sensing["decision"] == "adjust" and res.proposal_id == res.sensing["proposal_id"]
    row = proposals.get(res.proposal_id)
    assert [(a["code"], a["approver"]) for a in row["actions"]] == [("adjust_forecast", "Kabir Shaikh (demand_planner)"), ("co_sign_forecast", "Devika Rao (sop_lead)")]
    assert row["kind"] == "sensing" and row["payload"]["backtest"]["mape_blend"] == 7.45
    assert res.meter["calls"] == 5 and res.meter["cost_usd"] == 0.0375


def test_silent_run_writes_no_proposal_and_the_chaser_is_refused(history, profile, levers, proposals):
    quiet = agent.run({"mode": "sense", "dc": "KOL", "sku": "MN-1L", "origin_week": 16}, history, profile, FakeSensingClient(), levers, proposals)
    assert quiet.stop_reason == "submitted" and quiet.proposal_id is None and quiet.sensing["decision"] == "silent"
    chaser = agent.run({"mode": "sense", "dc": "KOL", "sku": "MN-1L", "origin_week": 16}, history, profile, FakeSensingClient(chase_spikes=True), levers, proposals)
    assert chaser.proposal_id is None                      # the dispatcher refused the proposal
    assert chaser.sensing["decision"] == "adjust"          # the brain lied anyway: the eval trap catches this
    assert proposals.pending() == []


def test_sweep_takes_the_first_pair_that_passes(history, profile, levers, proposals):
    res = agent.run({"mode": "sweep", "origin_week": 16, "horizon": 2}, history, profile, FakeSensingClient(), levers, proposals)
    assert res.stop_reason == "submitted" and res.tool_calls[0]["tool"] == "scan_pairs"
    assert (res.sensing["dc"], res.sensing["sku"], res.sensing["decision"]) == ("CHN", "MN-1L", "adjust")


def test_brewer_profile_changes_the_rule_and_the_names(history, levers, proposals):
    brewer = load_profile("brewer")
    assert logic.sense(history, brewer, "CHN", "MN-1L", 16).reasons[0].startswith("series too short")   # 13 weeks of history
    gfb = logic.sense(history, load_profile("gfb"), "CHN", "MN-1L", 17)
    bre = logic.sense(history, brewer, "CHN", "MN-1L", 17)
    assert gfb.adjustment_pct == [0.642, 0.106] and bre.adjustment_pct == [0.524, 0.0]    # week 19: +2.7% is under the 3% bar
    res = agent.run({"mode": "sense", "dc": "CHN", "sku": "MN-1L", "origin_week": 17}, history, brewer, FakeSensingClient(), levers, proposals)
    row = proposals.get(res.proposal_id)
    assert [a["approver"] for a in row["actions"]] == ["Pieter Jansen (demand_planner)", "Camille Roux (sop_lead)"]


def test_sensing_eval_gate_passes_offline(tmp_path):
    out = tmp_path / "results.json"
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "evals" / "run_sensing_evals.py"), "--out", str(out)], capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, r.stdout[-2000:]
    assert '"gate": "PASS"' in r.stdout
