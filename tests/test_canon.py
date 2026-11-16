"""The canon must reproduce Appendix F of The Planner's Engine to the case."""
from foundry import canon


def test_days_of_supply_matches_appendix_f():
    got = {dc: round(canon.days_of_supply(r["on_hand"], r["mu"]), 1) for dc, r in canon.MN1L.items()}
    assert got == {"BHW": 9.0, "GGN": 10.5, "KOL": 63.0, "CHN": 4.0}


def test_safety_stock_and_rop_at_95():
    ss = {dc: round(canon.safety_stock(r["sigma"], r["L"])) for dc, r in canon.MN1L.items()}
    rop = {dc: round(canon.reorder_point(r["mu"], r["sigma"], r["L"])) for dc, r in canon.MN1L.items()}
    assert ss == {"BHW": 822, "GGN": 691, "KOL": 345, "CHN": 1047}
    assert rop == {"BHW": 5022, "GGN": 3791, "KOL": 1395, "CHN": 6247}


def test_rebalancing_case_f11():
    r = canon.rebalancing_case()
    assert r["transfer_cases"] == 3720 and r["lane_cost_inr"] == 81840
    assert 183000 <= r["margin_saved_inr"] <= 185000      # the book says about 1.84 lakh


def test_annual_mn1l_cases():
    assert round(canon.annual_cases("MN-1L")) == 568750 or abs(canon.annual_cases("MN-1L") - 569400) < 1500
