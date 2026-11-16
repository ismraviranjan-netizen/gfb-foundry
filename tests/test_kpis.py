from foundry.agents.rca_exception import kpis


def test_incident_week_raises_the_five_injected_deviations(store, profile):
    keys = {d.key for d in kpis.deviations(store, profile, 12)}
    assert "OTIF:CHN:MN-1L:w12" in keys          # late trucks
    assert "OTIF:GGN:ALL:w12" in keys            # dock congestion hits the whole DC
    assert "DOS:BHW:GC-200:w12" in keys          # promo spike
    assert "OTIF:BHW:MP-1KG:w12" in keys         # plant miss
    assert "DOS:KOL:MN-1L:w12" in keys           # the stale snapshot trap


def test_quiet_weeks_are_mostly_quiet(store, profile):
    noisy = [len(kpis.deviations(store, profile, w)) for w in range(5, 11)]
    assert sum(noisy) <= 10, noisy               # a few false alarms are normal; a flood is a bug


def test_chronic_shortage_is_not_reported_unless_worse(store, profile):
    # Chennai MN-1L sits at ~4 days by design; it must not appear every week as a DOS deviation
    for w in range(6, 11):
        assert "DOS:CHN:MN-1L:w%d" % w not in {d.key for d in kpis.deviations(store, profile, w)}


def test_otif_arithmetic(store):
    rows = store.rows("orders", dc="CHN", week=12, sku="MN-1L")
    by_hand = sum(1 for r in rows if r["on_time"] and r["in_full"]) / len(rows)
    assert kpis.otif(store, 12, "CHN", "MN-1L") == by_hand
