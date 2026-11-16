"""The plug-in points a second company needed (8 Nov): a route for a plug-in cause, paperwork on a lane,
the system an action is sent to, and a SKU that only some DCs carry. Toy plug-ins, GFB's data."""
from __future__ import annotations

from foundry import approval
from foundry.agents.rebalancing import logic as rbl
from foundry.data.generate import build
from foundry.network import GFB, Network
from foundry.plugins import Plugins
from foundry.profile import load_profile
from foundry.store import Store
from foundry.tower import routing


def _one_sender_store() -> Store:
    s = Store.from_position()
    for r in s.inventory:
        if r["sku"] == "MN-1L" and r["dc"] == "BHW":
            r["on_hand"] = 17000
    return s


def test_a_lane_step_rides_with_the_transfer():
    prof = load_profile("gfb")
    prof.plugins.add_lane_step("raise_ead", "Raise the excise document before the truck leaves",
                               lambda net, profile, a, b: "BHW is a tax warehouse" if a == "BHW" else None)
    plan = rbl.plan_transfer(_one_sender_store(), prof, 1, "MN-1L", "CHN")
    codes = [a["code"] for a in plan["transfer"]["approvals"]]
    assert plan["transfer"]["from_dc"] == "BHW"
    assert codes[0] == "inter_dc_transfer" and codes[-1] == "book_truck" and "raise_ead" in codes
    step = next(a for a in plan["transfer"]["approvals"] if a["code"] == "raise_ead")
    assert "BHW is a tax warehouse" in step["text"]
    # and the tower's merge carries it, in the specialist's order
    merged = routing.merge({"actions_detail": [{"code": "inter_dc_transfer", "approver": "x", "text": "move"}]},
                           {"transfer": plan["transfer"], "approvals": plan["transfer"]["approvals"], "proposal_id": "PRP-1"}, None)
    assert [s["code"] for s in merged["plan"]][:1] == ["inter_dc_transfer"] and "raise_ead" in [s["code"] for s in merged["plan"]]


def test_no_lane_step_when_the_rule_says_no():
    prof = load_profile("gfb")
    prof.plugins.add_lane_step("raise_ead", "x", lambda net, profile, a, b: None)
    plan = rbl.plan_transfer(_one_sender_store(), prof, 1, "MN-1L", "CHN")
    assert [a["code"] for a in plan["transfer"]["approvals"]] in (["inter_dc_transfer", "book_truck"],
                                                                 ["inter_dc_transfer", "co_sign_transfer", "book_truck"])


def test_plugin_route_wins_for_its_cause():
    assert routing.route("CO2_SHORT", [], 12, 26) == []
    assert routing.route("CO2_SHORT", [], 12, 26, extra={"CO2_SHORT": ["rebalancing"]}) == ["rebalancing"]
    assert routing.route("SUPPLY_MISS", [], 12, 26, extra={"CO2_SHORT": ["rebalancing"]}) == ["rebalancing"]
    p = Plugins()
    p.add_route("CO2_SHORT", ["rebalancing"])
    assert p.routes == {"CO2_SHORT": ["rebalancing"]}


def test_a_plugin_code_goes_to_its_own_system():
    assert approval._system_for("book_truck") == "TMS"
    approval.register_system("raise_ead_test", "EMCS")
    try:
        assert approval._system_for("raise_ead_test") == "EMCS"
    finally:
        approval.EXTRA_SYSTEMS.pop("raise_ead_test", None)


def test_a_sku_stocked_in_one_market_only(tmp_path):
    doc = GFB.to_dict()
    doc["code"] = "TST"
    doc["sku_dc_share"] = {"GC-200": {"BHW": 0.6, "GGN": 0.4}}        # the chakli sells in two DCs only
    path = tmp_path / "n.yaml"
    import yaml
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    net = Network.from_yaml(path)
    assert net.stocked("GC-200", "BHW") and not net.stocked("GC-200", "CHN") and net.stocked("MN-1L", "CHN")
    assert net.weekly_mean("GC-200", "CHN") == 0
    s = build([], seed=5, weeks=3, net=net)
    assert not [r for r in s.inventory if r["sku"] == "GC-200" and r["dc"] in ("KOL", "CHN")]
    assert [r for r in s.inventory if r["sku"] == "GC-200" and r["dc"] == "BHW"]
    assert not [r for r in Store.from_position(net).inventory if r["sku"] == "GC-200" and r["dc"] == "CHN"]
    assert all(GFB.stocked(k, d) for k in GFB.parts for d in GFB.dcs)
