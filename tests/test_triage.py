from foundry.agents.rca_exception import diagnosis as dx, triage as tr


def test_priority_and_sla_come_from_the_profile(store, profile):
    d = dx.gather(store, profile, 12, "CHN", "MN-1L", "OTIF")
    t = tr.triage(store, profile, d)
    assert t.priority == "P1" and t.sla_hours == profile.sla_hours("P1") == 4
    assert t.actions[0].code == "inter_dc_transfer"            # surplus exists: transfer first
    assert "Priya Nambiar" in t.actions[0].approver             # Chennai's DC manager signs


def test_stale_data_gets_no_operational_action(store, profile):
    d = dx.gather(store, profile, 12, "KOL", "MN-1L", "DOS")
    t = tr.triage(store, profile, d)
    assert t.root_cause == "DATA_STALE" and t.impact_inr == 0 and t.priority == "P3"
    assert [a.code for a in t.actions] == ["refresh_snapshot", "no_operational_action"]


def test_impact_uses_canon_contribution(store, profile):
    imp = tr.estimate_impact(store, profile, 12, "CHN", "MN-1L")
    assert imp["contribution_per_case"] == 62
    assert imp["impact_inr"] > 0 and imp["hours_to_impact"] == 0.0


def test_brewer_profile_changes_approvers_and_sla(store):
    from foundry.profile import load_profile
    b = load_profile("brewer")
    d = dx.gather(store, b, 12, "CHN", "MN-1L", "OTIF")
    t = tr.triage(store, b, d)
    assert t.sla_hours == 2 and "Lotte de Vries" in t.actions[0].approver
