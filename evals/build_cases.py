"""Write evals/cases.json: 30 cases the agent must get right. Run once; edit the JSON by hand after."""
import json
from pathlib import Path

C = []
def case(id_, kind, injections, task, expect, note=""):
    C.append({"id": id_, "kind": kind, "injections": injections, "task": task, "expect": expect, "note": note})

def inj(kind, week, dc, sku=None, **params):
    return {"kind": kind, "week": week, "dc": dc, "sku": sku, "params": params}

T = lambda dc, sku, kpi="OTIF", week=12: {"mode": "diagnose", "dc": dc, "sku": sku, "week": week, "kpi": kpi}

# clear ------------------------------------------------------------------------------------------
case("C01", "clear", [inj("transport_delay", 12, "CHN", "MN-1L", late_share=0.67, late_days=3.0, carrier="Sahyadri Roadways")], T("CHN", "MN-1L"), {"root_cause": "TRANSPORT_DELAY", "priority": "P1"}, "the canonical late truck")
case("C02", "clear", [inj("transport_delay", 12, "GGN", "MN-200", late_share=0.75, late_days=3.0, carrier="Deccan Freight")], T("GGN", "MN-200"), {"root_cause": "TRANSPORT_DELAY"})
case("C03", "clear", [inj("transport_delay", 12, "BHW", "AJ-1L", late_share=1.0, late_days=4.0)], T("BHW", "AJ-1L"), {"root_cause": "TRANSPORT_DELAY"})
case("C04", "clear", [inj("dock_congestion", 12, "GGN", dwell_h=9.5, on_time_share=0.82)], T("GGN", None), {"root_cause": "DOCK_CONGESTION", "priority": "P1"}, "DC-level view")
case("C05", "clear", [inj("dock_congestion", 12, "CHN", dwell_h=8.0, on_time_share=0.80)], T("CHN", None), {"root_cause": "DOCK_CONGESTION"})
case("C06", "clear", [inj("dock_congestion", 12, "BHW", dwell_h=9.0, on_time_share=0.78)], T("BHW", "MN-1L"), {"root_cause": "DOCK_CONGESTION"}, "SKU-level view of a DC-level cause")
case("C07", "clear", [inj("demand_spike", 11, "BHW", "GC-200", pct=1.0), inj("demand_spike", 12, "BHW", "GC-200", pct=1.0)], T("BHW", "GC-200", "DOS"), {"root_cause": "DEMAND_SPIKE", "priority": "P1"}, "Diwali chakli promo")
case("C08", "clear", [inj("demand_spike", 11, "CHN", "MN-200", pct=1.2), inj("demand_spike", 12, "CHN", "MN-200", pct=1.2)], T("CHN", "MN-200", "DOS"), {"root_cause": "DEMAND_SPIKE"})
case("C09", "clear", [inj("demand_spike", 11, "KOL", "KH-250", pct=1.2), inj("demand_spike", 12, "KOL", "KH-250", pct=1.2)], T("KOL", "KH-250", "DOS"), {"root_cause": "DEMAND_SPIKE"}, "a C item with five orders a week")
case("C10", "clear", [inj("supply_miss", 11, "BHW", "MP-1KG", short_pct=0.5), inj("supply_miss", 12, "BHW", "MP-1KG", short_pct=1.0)], T("BHW", "MP-1KG"), {"root_cause": "SUPPLY_MISS", "priority": "P1"}, "pulp line down")
case("C11", "clear", [inj("supply_miss", 11, "GGN", "LJ-1L", short_pct=0.6), inj("supply_miss", 12, "GGN", "LJ-1L", short_pct=1.0)], T("GGN", "LJ-1L"), {"root_cause": "SUPPLY_MISS"})
case("C12", "clear", [inj("supply_miss", 11, "KOL", "NM-500", short_pct=0.5), inj("supply_miss", 12, "KOL", "NM-500", short_pct=1.0)], T("KOL", "NM-500", "DOS"), {"root_cause": "SUPPLY_MISS"})
case("C13", "clear", [inj("data_stale", 12, "KOL", "MN-1L", age_h=36, reported_on_hand=400)], T("KOL", "MN-1L", "DOS"), {"root_cause": "DATA_STALE", "priority": "P3"}, "the trap that looks like a stockout")
case("C14", "clear", [inj("data_stale", 12, "BHW", "MN-200", age_h=40, reported_on_hand=100)], T("BHW", "MN-200", "DOS"), {"root_cause": "DATA_STALE", "priority": "P3"})
case("C15", "clear", [inj("imbalance", 12, "CHN", "MN-1L", short_on_hand=300, long_dc="KOL", long_on_hand=9000)], T("CHN", "MN-1L", "DOS"), {"root_cause": "INVENTORY_IMBALANCE"}, "short here, long there, trucks fine")
# ambiguous --------------------------------------------------------------------------------------
case("A16", "ambiguous", [inj("transport_delay", 12, "CHN", "MN-1L", late_share=0.67, late_days=3.0), inj("imbalance", 12, "CHN", "MN-1L", short_on_hand=0, long_dc="KOL", long_on_hand=9500)], T("CHN", "MN-1L"), {"root_cause": "TRANSPORT_DELAY", "contributing": "INVENTORY_IMBALANCE"}, "late truck with a surplus elsewhere: transfer first")
case("A17", "ambiguous", [inj("dock_congestion", 12, "GGN", dwell_h=9.5, on_time_share=0.82), inj("transport_delay", 12, "GGN", "MN-1L", late_share=0.5, late_days=1.0)], T("GGN", "MN-1L"), {"root_cause": "DOCK_CONGESTION"}, "a jammed dock and one truck a day late")
case("A18", "ambiguous", [inj("demand_spike", 11, "BHW", "GC-200", pct=1.0), inj("demand_spike", 12, "BHW", "GC-200", pct=1.0), inj("supply_miss", 12, "BHW", "GC-200", short_pct=0.3)], T("BHW", "GC-200", "DOS"), {"root_cause": "DEMAND_SPIKE"}, "promo doubled sales and the plant shipped 30% short")
case("A19", "ambiguous", [inj("supply_miss", 11, "BHW", "MP-1KG", short_pct=0.5), inj("supply_miss", 12, "BHW", "MP-1KG", short_pct=1.0), inj("data_stale", 12, "BHW", "MP-1KG", age_h=30)], T("BHW", "MP-1KG"), {"root_cause": "SUPPLY_MISS"}, "stale snapshot cannot explain short orders")
case("A20", "ambiguous", [inj("data_stale", 12, "KOL", "MN-1L", age_h=36, reported_on_hand=400), inj("demand_spike", 12, "KOL", "MN-1L", pct=0.5)], T("KOL", "MN-1L", "DOS"), {"root_cause": "DATA_STALE"}, "data before chain")
case("A21", "ambiguous", [inj("transport_delay", 12, "CHN", "MN-1L", late_share=0.34, late_days=1.5), inj("dock_congestion", 12, "CHN", dwell_h=8.5, on_time_share=0.80)], T("CHN", "MN-1L"), {"root_cause": "DOCK_CONGESTION"}, "weak transport signal, strong dock signal")
case("A22", "ambiguous", [inj("demand_spike", 11, "CHN", "AJ-1L", pct=1.0), inj("demand_spike", 12, "CHN", "AJ-1L", pct=1.0), inj("transport_delay", 12, "CHN", "AJ-1L", late_share=0.67, late_days=3.0)], T("CHN", "AJ-1L"), {"root_cause": "TRANSPORT_DELAY", "contributing": "DEMAND_SPIKE"}, "tie: the planner's order of checking decides")
case("A23", "ambiguous", [inj("supply_miss", 11, "GGN", "MN-1L", short_pct=1.0), inj("supply_miss", 12, "GGN", "MN-1L", short_pct=1.0), inj("dock_congestion", 12, "GGN", dwell_h=9.5, on_time_share=0.82)], T("GGN", "MN-1L"), {"root_cause": "DOCK_CONGESTION", "contributing": "SUPPLY_MISS"})
case("A24", "ambiguous", [inj("imbalance", 12, "CHN", "MN-1L", short_on_hand=300, long_dc="KOL", long_on_hand=9000), inj("data_stale", 12, "CHN", "MN-1L", age_h=30, reported_on_hand=300)], T("CHN", "MN-1L", "DOS"), {"root_cause": "DATA_STALE"})
case("A25", "ambiguous", [inj("dock_congestion", 12, "BHW", dwell_h=9.0, on_time_share=0.78), inj("demand_spike", 11, "BHW", "GC-200", pct=1.0), inj("demand_spike", 12, "BHW", "GC-200", pct=1.0)], T("BHW", None), {"root_cause": "DOCK_CONGESTION", "contributing": "DEMAND_SPIKE"}, "DC-level view")
# traps ------------------------------------------------------------------------------------------
case("T26", "trap", [], T("CHN", "MN-1L"), {"root_cause": "NO_CLEAR_CAUSE", "priority": "P3"}, "nothing happened; the agent must say so")
case("T27", "trap", [], {"mode": "sweep", "week": 10}, {"root_cause": "NO_CLEAR_CAUSE"}, "a quiet week swept; whatever noise it picks must end in no action")
case("T28", "trap", [inj("data_stale", 12, "KOL", "MN-1L", age_h=36)], T("KOL", "MN-1L", "DOS"), {"root_cause": "DATA_STALE", "priority": "P3"}, "stale but plausible numbers: still a data problem")
case("T29", "trap", [inj("transport_delay", 12, "CHN", "MN-1L", late_share=0.67, late_days=1.0)], T("CHN", "MN-1L"), {"root_cause": "TRANSPORT_DELAY"}, "a day late: orders ship late, nothing slips")
case("T30", "trap", [inj("demand_spike", 12, "BHW", "MN-1L", pct=0.25)], T("BHW", "MN-1L"), {"root_cause": "NO_CLEAR_CAUSE"}, "a small spike absorbed by stock is not an incident")

Path("evals/cases.json").write_text(json.dumps(C, indent=1))
print(len(C), "cases written")
