"""Write evals/queue_cases.json: 12 cases the queue (and its digest agent) must get right. Run once."""
import json
from pathlib import Path

C = []


def case(id_, kind, check, expect, week=12, profile="gfb", now_hours=None, pre=None, fake=None, note=""):
    C.append({"id": id_, "kind": kind, "check": check, "week": week, "profile": profile, "now_hours": now_hours,
              "pre": pre or [], "fake": fake or {}, "expect": expect, "note": note})


# the canonical week, grouped and ordered ---------------------------------------------------------
case("Q01", "order", "order", {"keys": ["CHN:MN-1L", "BHW:MP-1KG", "BHW:GC-200", "GGN:ALL", "KOL:MN-1L", "CHN:MP-1KG"], "built_from": 14},
     note="fourteen deviations become six exceptions: P1s by money, then the P3s")
case("Q02", "group", "group", {"key": "GGN:ALL", "root_cause": "DOCK_CONGESTION", "grouped_keys": ["GGN:LJ-1L", "GGN:MP-1KG", "GGN:GC-200", "GGN:MN-200"], "children_count": 4},
     note="the dock owns every SKU-level OTIF miss at Gurgaon")
case("Q03", "dedupe", "dedupe", {"key": "CHN:MN-1L", "kpi": "OTIF", "root_cause": "TRANSPORT_DELAY", "confidence": 0.95, "kpis_moved": ["DOS", "OTIF", "FILL_RATE@DC", "OTIF@DC"], "children_count": 3},
     note="DOS, OTIF, and the DC's OTIF and fill rate are one exception with the surest KPI as the headline")
case("Q04", "item", "item", {"key": "KOL:MN-1L", "root_cause": "DATA_STALE", "priority": "P3", "impact_inr": 0, "sla_hours": 72, "approver_contains": "Rohan Patil"},
     note="stale data is never urgent and never worth money until the refresh")
case("Q05", "quiet", "agent", {"stop": "submitted", "exceptions": 0, "lines": 0, "breaches": 0, "headline_contains": "Quiet"}, week=10, now_hours=2,
     note="a quiet week is an empty queue and a one-line digest")
# the clock ---------------------------------------------------------------------------------------
case("Q06", "clock", "tick", {"reminded": ["CHN:MN-1L", "BHW:MP-1KG", "BHW:GC-200", "GGN:ALL"], "escalated": []}, now_hours=3,
     note="at 3 h the P1s (SLA 4 h) get their half-SLA nudge; nothing has breached")
case("Q07", "clock", "tick", {"reminded": [], "escalated": ["CHN:MN-1L", "BHW:MP-1KG", "BHW:GC-200", "GGN:ALL"], "approver_added": "Ananya Kulkarni", "not_escalated": ["KOL:MN-1L"]}, now_hours=5,
     note="at 5 h every P1 has passed its SLA and climbs to the VP; the P3 (72 h) does not")
case("Q08", "clock", "tick", {"reminded": [], "escalated": ["CHN:MN-1L", "BHW:MP-1KG", "BHW:GC-200", "GGN:ALL"], "not_reminded": ["KOL:MN-1L"]}, now_hours=30,
     note="at 30 h the P3's reminder (36 h) has not fired yet")
case("Q09", "clock", "tick", {"escalated": ["CHN:MN-1L", "BHW:MP-1KG", "BHW:GC-200", "GGN:ALL"], "approver_added": "Henrik Dahl"}, now_hours=3, profile="brewer",
     note="the brewer's P1 SLA is 2 h: at 3 h the same items are already with the director")
# traps -------------------------------------------------------------------------------------------
case("Q10", "trap", "tick", {"escalated": ["BHW:MP-1KG", "BHW:GC-200", "GGN:ALL"], "not_escalated": ["CHN:MN-1L"], "state": {"CHN:MN-1L": "APPROVED"}}, now_hours=5,
     pre=[{"decide": {"key": "CHN:MN-1L", "approver": "Priya Nambiar", "decision": "APPROVED", "at": 1.0, "note": "transfer from KOL booked"}}],
     note="an item answered before its SLA never escalates")
case("Q11", "trap", "agent", {"stop": "submitted", "escalated_count": 0, "refused_tool": "escalate", "breaches": 0}, now_hours=1, fake={"escalate_early": "CHN:MN-1L"},
     note="the agent asks to escalate at 1 h; the tool refuses; the digest is still written")
case("Q12", "trap", "agent", {"stop": "submitted", "breaches": 4, "p1_count": 4, "p3_count": 1, "escalated_count": 4, "counts_match_queue": True, "money_at_stake_inr": 347373}, now_hours=6,
     note="the digest's numbers must equal the queue's; a brain that skips escalation (skip_escalation=True) fails here")

Path(__file__).resolve().parent.joinpath("queue_cases.json").write_text(json.dumps(C, indent=1))
print(len(C), "cases")
