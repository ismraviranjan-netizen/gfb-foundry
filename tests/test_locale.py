"""Any company, not just GFB: money in its currency, its own clock, role titles as names, its own hauliers.
GFB's numbers and words must not move (the rest of the suite pins them); these pin the other side."""
from __future__ import annotations

import yaml

from foundry.chat.fake import FakeChatClient, _dc_sku
from foundry.data.generate import build
from foundry.network import GFB, Network
from foundry.notify import coworker as cw


def teardown_function(_fn):
    cw.reset_locale()


def test_money_follows_the_currency():
    assert cw.inr(184229) == "INR 1,84,229"          # GFB: Indian grouping, unchanged
    cw.set_currency("EUR")
    assert cw.inr(184229) == "EUR 184,229"
    assert cw.inr(-1500) == "-EUR 1,500"
    assert cw.code() == "EUR"
    cw.set_currency("gbp")
    assert cw.inr(18400.4) == "GBP 18,400"


def test_clock_starts_at_the_company_hour():
    assert cw.clock_label(0) == "Mon 06:30"          # GFB: 06:30 IST
    cw.set_clock(6.0)
    assert cw.clock_label(0) == "Mon 06:00"
    assert cw.clock_label(4) == "Mon 10:00"
    assert cw.clock_label(24 + 2.5) == "Tue 08:30"


def test_role_titles_are_greeted_whole():
    assert cw.greet("Priya Nambiar") == "Priya"
    cw.set_greeting("full")
    assert cw.greet("NL supply planner") == "NL supply planner"
    subject, body = cw.approval_digest("NL supply planner", [{"plan_id": "PRP-12-ZTW-abc123", "step": 1, "code": "inter_dc_transfer",
                                                             "text": "move", "priority": "P1", "due_at": 2.0, "why": "short", "money_line": "x"}],
                                       0.0, "http://localhost:8080")
    assert body.startswith("NL supply planner,")


def test_a_network_names_its_own_hauliers(tmp_path):
    doc = GFB.to_dict()
    doc.update({"company": "Test Brewer (fictional)", "code": "TBW", "currency": "EUR",
                "carriers": ["Polder Haulage", "Maas Freight"], "orders_per_week": {"A": 20}})
    path = tmp_path / "net.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    net = Network.from_yaml(path)
    assert net.carriers == ["Polder Haulage", "Maas Freight"] and net.orders_per_week == {"A": 20}
    s = build([], seed=3, weeks=2, net=net)
    assert {r["carrier"] for r in s.shipments} <= {"Polder Haulage", "Maas Freight"}
    a_sku = next(k for k, v in net.parts.items() if v[1] == "A")
    assert sum(1 for r in s.orders if r["week"] == 1 and r["sku"] == a_sku and r["dc"] == net.dcs[0]) == 20
    assert Network.from_yaml(path).to_dict()["carriers"] == ["Polder Haulage", "Maas Freight"]


def test_gfb_network_dump_is_unchanged_by_the_new_fields():
    assert "carriers" not in GFB.to_dict() and "orders_per_week" not in GFB.to_dict()


def test_chat_brain_reads_any_company_codes(tmp_path):
    doc = GFB.to_dict()
    doc["sites"] = [{"code": "LKS", "name": "Lakeside DC", "kind": "dc"}, {"code": "BRU", "name": "Brussels DC", "kind": "dc"},
                    {"code": "PLT", "name": "Test plant", "kind": "plant"}]
    doc["code"] = "TBW"
    path = tmp_path / "n.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    c = FakeChatClient.for_network(Network.from_yaml(path))
    assert _dc_sku("Is LKS HL-330C still at risk?", c.codes, c.dc_names, c.sku_names) == ("LKS", "HL-330C")
    assert _dc_sku("why is brussels waiting", c.codes, c.dc_names, c.sku_names)[0] == "BRU"
    assert FakeChatClient.for_network(GFB).codes == ("BHW", "GGN", "KOL", "CHN")


def test_chat_brain_knows_the_company_sku_codes(tmp_path):
    """SKU codes the GFB pattern cannot read (two-letter suffixes) come from the parts table, longest first."""
    doc = GFB.to_dict()
    doc["sites"] = [{"code": "MTW", "name": "Midtown DC", "kind": "dc"}, {"code": "PLT", "name": "Test plant", "kind": "plant"}]
    first = doc["parts"][0]
    doc["parts"] = [{**first, "code": "LG-30CR"}, {**first, "code": "SD-10C"}, {**first, "code": "SD-10CI"}]
    doc["code"] = "TBW"
    path = tmp_path / "n.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    c = FakeChatClient.for_network(Network.from_yaml(path))
    assert _dc_sku("Is MTW LG-30CR still at risk?", c.codes, c.dc_names, c.sku_names, c.sku_codes) == ("MTW", "LG-30CR")
    assert _dc_sku("Is MTW SD-10CI short?", c.codes, c.dc_names, c.sku_names, c.sku_codes)[1] == "SD-10CI"
    assert _dc_sku("Is MTW SD-10C short?", c.codes, c.dc_names, c.sku_names, c.sku_codes)[1] == "SD-10C"
