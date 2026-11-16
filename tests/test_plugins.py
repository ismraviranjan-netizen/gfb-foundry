"""Part 6: the plug-in points. A company whose difference is not a number adds a cause, a step or
a clock through its profile, and the GFB defaults do not move."""
from foundry.profile import load_profile
from foundry.agents.rca_exception import diagnosis, triage


def test_gfb_has_no_plugins(profile):
    assert profile.plugins.causes == [] and profile.plugins.steps == [] and profile.plugins.urgency_clock is None


def test_pharma_qa_hold_is_a_cause_and_qa_release_comes_first(store):
    pharma = load_profile("pharma")
    assert [c.name for c in pharma.plugins.causes] == ["QA_HOLD"]
    store.extra["qa_holds"] = [{"week": 12, "dc": "CHN", "sku": "MN-1L", "cases_held": 1800, "reason": "retest pending"}]
    d = diagnosis.gather(store, pharma, 12, "CHN", "MN-1L", "OTIF")
    causes = {e.cause: e.score for e in d.evidence}
    assert causes["QA_HOLD"] == 1.0
    t = triage.triage(store, pharma, d)
    # the late trucks still win on OTIF; the transfer in the playbook now needs QA release first
    assert d.primary == "TRANSPORT_DELAY"
    assert t.actions[0].code == "qa_release" and "Shalini" in t.actions[0].approver
    store.extra.pop("qa_holds")


def test_pharma_qa_hold_can_be_primary(store):
    pharma = load_profile("pharma")
    pharma.diagnosis["max_snapshot_age_h"] = 24      # GFB's snapshots are hours old; pharma's limit is 2 h
    store.extra["qa_holds"] = [{"week": 12, "dc": "BHW", "sku": "MN-1L", "cases_held": 3000, "reason": "sterility retest"}]
    d = diagnosis.gather(store, pharma, 12, "BHW", "MN-1L", "OTIF")     # nothing else happened at BHW MN-1L
    assert d.primary == "QA_HOLD"
    t = triage.triage(store, pharma, d)
    assert [a.code for a in t.actions][:2] == ["qa_release", "expedite_qa_retest"]
    store.extra.pop("qa_holds")


def test_retailer_clock_is_hours_to_the_next_wave(store):
    retail = load_profile("retailer")
    retail.diagnosis["max_snapshot_age_h"] = 24      # GFB's snapshots are 1-6 h old; the retailer reads hourly
    d = diagnosis.gather(store, retail, 12, "BHW", "GC-200", "DOS")        # 1.3 days of cover at GFB thresholds
    assert d.primary == "DEMAND_SPIKE"
    t = triage.triage(store, retail, d)
    assert t.hours_to_impact == 2.0          # 04:00 now, first wave at 06:00
    assert t.priority == "P1"


def test_bakery_returns_cause_reads_its_own_table(store):
    bakery = load_profile("bakery")
    store.extra["returns"] = [{"week": w, "dc": "GGN", "sku": "GC-200", "delivered": 2000, "returned": 120 if w < 12 else 420} for w in range(4, 13)]
    d = diagnosis.gather(store, bakery, 12, "GGN", "GC-200", "OTIF")
    ev = {e.cause: e for e in d.evidence}["STALE_RETURNS"]
    assert ev.facts["factor"] == 3.5 and ev.score >= 0.9
    store.extra.pop("returns")


def test_unknown_plugin_fails_loudly(tmp_path):
    import pytest
    from foundry import plugins
    with pytest.raises(ModuleNotFoundError):
        plugins.load(["foundry.plugin_lib.does_not_exist"])


def test_network_yaml_round_trip(tmp_path):
    from foundry.network import GFB, Network
    y = GFB.to_yaml(tmp_path / "network.yaml")
    n = Network.from_yaml(y)
    assert n.weekly_mean("MN-1L", "CHN") == 2600 and n.lane("KOL", "CHN") == (4, 22.0) and n.reorder_point("MN-1L", "CHN") == GFB.reorder_point("MN-1L", "CHN")
