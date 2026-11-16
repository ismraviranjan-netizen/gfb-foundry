"""The risk radar and the one clock it shares with the rebalancing agent (The Control Room, Parts I-II)."""
from foundry.data.generate import canonical_store
from foundry.risk import projection as risk
from foundry.store import Store


def test_radar_on_the_opening_position_matches_f11(profile):
    r = risk.radar(Store.from_position(), profile, 1)
    assert r["counts"]["RED"] == 2 and [(x["dc"], x["sku"]) for x in r["pre_empt"]] == [("CHN", "MN-1L")]
    chn = next(x for x in r["rows"] if x["dc"] == "CHN" and x["sku"] == "MN-1L")
    assert chn["first_short_day"] == 4 and round(chn["cases_short"]) == 2977 and chn["money_at_risk"] == 184583
    w = risk.what_if(Store.from_position(), profile, 1, "CHN", "MN-1L", 3720, 4)
    assert w["after"]["money_at_risk"] == 354 and w["money_avoided"] == 184229        # F.11's margin, to the rupee


def test_stale_snapshot_is_grey_and_refresh_reveals_a_long_dc(profile):
    assert risk.project(canonical_store(), profile, 12, "KOL", "MN-1L").risk == "GREY"
    p = risk.project(canonical_store(refreshed=True), profile, 12, "KOL", "MN-1L")
    assert p.risk == "GREEN" and p.long and p.money_at_risk == 0


def test_one_clock_next_receipt_comes_from_the_arrival_schedule(profile):
    s = canonical_store(refreshed=True)
    assert risk.next_receipt_day(s, profile, 12, "CHN", "MN-1L") == 2            # the slipped trucks
    for r in s.inventory:
        if r["week"] == 12 and r["dc"] == "CHN" and r["sku"] == "MN-1L":
            r["open_receipt_eta_days"] = 8
    assert risk.next_receipt_day(s, profile, 12, "CHN", "MN-1L") == 4            # Thursday's plant truck comes first
    assert risk.project(s, profile, 12, "CHN", "MN-1L").money_at_risk == 92114


def test_a_strike_holds_new_dispatches_but_not_trucks_on_the_road(profile):
    s = canonical_store()
    s.extra["lane_delays"] = [{"week": 12, "lane_from": "NSK", "lane_to": "*", "delay_days": 4, "reason": "strike", "applies_to": "new"}]
    arr, _, _ = risk.arrival_schedule(s, profile, 12, "CHN", "MN-1L")
    assert [(a["day"], a["source"].split(" (")[0]) for a in arr][:2] == [(2, "open receipt"), (8, "plant plan")]
    s.extra["lane_delays"][0]["applies_to"] = "all"
    arr, _, _ = risk.arrival_schedule(s, profile, 12, "CHN", "MN-1L")
    assert arr[0]["day"] == 6                                                        # a road closure holds them too


def test_transfers_in_flight_and_holds_move_stock_in_the_projection(profile):
    s = Store.from_position()
    s.extra["transfers_inflight"] = [{"transfer_id": "TR-1", "week": 1, "from_dc": "KOL", "to_dc": "CHN", "sku": "MN-1L", "cases": 3720, "lands_day": 4, "status": "IN_TRANSIT"}]
    assert risk.project(s, profile, 1, "CHN", "MN-1L").money_at_risk == 354
    assert risk.project(s, profile, 1, "KOL", "MN-1L").start_on_hand == 9450 - 3720
    s.extra["reservations"] = [{"plan_id": "P", "week": 1, "sku": "MN-1L", "from_dc": "KOL", "to_dc": "CHN", "cases": 1000, "status": "HELD"}]
    assert risk.project(s, profile, 1, "KOL", "MN-1L").start_on_hand == 9450 - 3720 - 1000


# ------------------------------------------------------------------ finding F1: a rush must beat the trucks on the road
def test_no_rush_can_beat_trucks_that_land_first(profile):
    rs = risk.rescue(canonical_store(), profile, 12, "CHN", "MN-1L")
    assert rs["helps"] is False and (rs["late_day"], rs["rush_day"]) == (2, 3)
    assert "no rush can help" in rs["why"] and rs["money_avoided"] == 0


def test_a_rush_helps_when_the_carrier_says_the_trucks_are_days_away(profile):
    can = canonical_store()
    snap = [r for r in can.rows("inventory", dc="CHN", sku="MN-1L") if r["week"] <= 12][-1]
    snap["open_receipt_eta_days"] = 7                          # the carrier: the two trucks broke down, day 7
    rs = risk.rescue(can, profile, 12, "CHN", "MN-1L")
    assert rs["helps"] is True and (rs["late_day"], rs["rush_day"]) == (7, 3) and rs["money_avoided"] > 0
