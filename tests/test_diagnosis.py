from foundry.agents.rca_exception import diagnosis as dx


def primary(store, profile, dc, sku, kpi):
    return dx.gather(store, profile, 12, dc, sku, kpi).primary


def test_each_injected_cause_is_found(store, profile):
    assert primary(store, profile, "CHN", "MN-1L", "OTIF") == "TRANSPORT_DELAY"
    assert primary(store, profile, "GGN", None, "OTIF") == "DOCK_CONGESTION"
    assert primary(store, profile, "BHW", "GC-200", "DOS") == "DEMAND_SPIKE"
    assert primary(store, profile, "BHW", "MP-1KG", "OTIF") == "SUPPLY_MISS"
    assert primary(store, profile, "KOL", "MN-1L", "DOS") == "DATA_STALE"


def test_nothing_wrong_means_no_clear_cause(store, profile):
    assert primary(store, profile, "BHW", "MN-1L", "OTIF") == "NO_CLEAR_CAUSE"


def test_evidence_carries_facts_not_adjectives(store, profile):
    d = dx.gather(store, profile, 12, "CHN", "MN-1L", "OTIF")
    t = next(e for e in d.evidence if e.cause == "TRANSPORT_DELAY")
    assert t.facts["late_trucks"] >= 2 and t.facts["cases_slipped_to_next_week"] > 0
    assert "Sahyadri" in t.summary


def test_imbalance_uses_last_good_snapshot_when_stale(store, profile):
    d = dx.gather(store, profile, 12, "CHN", "MN-1L", "OTIF")
    imb = next(e for e in d.evidence if e.cause == "INVENTORY_IMBALANCE")
    kol = next(x for x in imb.facts["long_dcs"] if x["dc"] == "KOL")
    assert kol["fresh"] is False and kol["dos"] > 50     # the 400-case stale number was not trusted
