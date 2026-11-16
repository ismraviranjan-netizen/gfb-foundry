"""Write evals/control_room_cases.json: what the tower must do in the three storylines, and what Ask the
tower must answer. Checks are data, not code: the runner knows each kind. Run once; edit by hand after."""
import json
from pathlib import Path

S = []      # storyline checks


def chk(id_, story, kind, note="", **kw):
    S.append({"id": id_, "story": story, "kind": kind, "note": note, **kw})


# The F.11 Monday
chk("F01", "f11_monday", "transfer", dc="CHN", sku="MN-1L", version=1, from_dc="KOL", cases=3720, lands=4, note="F.11 through the tower")
chk("F02", "f11_monday", "what_if", dc="CHN", sku="MN-1L", version=1, before=184583, after=354, note="the radar agrees with the specialist")
chk("F03", "f11_monday", "cost", step="Monday 06:30: the cron fires", cost_inr=9.74, note="master + specialists on the scripted brains")
chk("F04", "f11_monday", "plan_status", dc="CHN", sku="MN-1L", version=1, status="APPLIED")
chk("F05", "f11_monday", "plan_steps", dc="CHN", sku="MN-1L", mode="pre_empt", codes=["expedite_alternate_carrier"], note="a late load is rescued before the plant is asked")
chk("F06", "f11_monday", "plan_count", count=2, note="the relief truck's ETA is watched, not re-planned")
chk("F07", "f11_monday", "message", to="Priya Nambiar", msg_kind="approval_request", contains="before Mon 10:30")
# Week 12
chk("W01", "week12", "plan_count_status", status="READY", count=0, note="everything signed, rejected or superseded by Tuesday 15:20")
chk("W02", "week12", "history_event", dc="KOL", sku="MN-1L", version=1, event="inherited", note="priority inheritance: the P3 refresh blocks a P1")
chk("W03", "week12", "versions", dc="CHN", sku="MN-1L", statuses=["SUPERSEDED", "SUPERSEDED", "APPLIED"])
chk("W04", "week12", "transfer", dc="CHN", sku="MN-1L", version=3, from_dc="KOL", cases=500, lands=5, note="the strike makes a Tuesday move pay; it lands a day later than a Monday one")
chk("W05", "week12", "what_if", dc="CHN", sku="MN-1L", version=3, before=74985, after=46057)
chk("W06", "week12", "not_accepted", step="09:48 Kabir clicks Approve again", note="idempotency key")
chk("W07", "week12", "note_contains", step="09:54 Priya tries to sign Chennai", text="refused", note="only a named approver signs")
chk("W08", "week12", "plan_status", dc="BHW", sku="GC-200", version=1, status="REJECTED")
chk("W09", "week12", "message", to="Meera Joshi", msg_kind="closed", contains="rejected by Kabir Shaikh", note="the others are told")
chk("W10", "week12", "message", to="Ananya Kulkarni", msg_kind="escalation", contains="passed its 4 h SLA")
chk("W11", "week12", "signed_by", dc="GGN", sku=None, version=1, who="Ananya Kulkarni", note="the VP signs an escalated plan")
chk("W12", "week12", "dropped_by", dc="BHW", sku="MP-1KG", mode="pre_empt", by="memory", note="the tower remembers what it already did")
chk("W13", "week12", "message", to="Meera Joshi", msg_kind="fyi", contains="still the only lever")
chk("W14", "week12", "message", to="Ananya Kulkarni", msg_kind="vp_digest", contains="INR 3,47,373 at stake")
chk("W15", "week12", "no_dead_letters")
# Two DCs, one sender
chk("O01", "one_sender", "plan_steps", dc="CHN", sku="MN-1L", version=1, codes=["inter_dc_transfer", "co_sign_transfer", "book_truck"], note="the reconciler adds the joint co-sign")
chk("O02", "one_sender", "transfer", dc="GGN", sku="MN-1L", version=1, from_dc="BHW", cases=900, lands=3)
chk("O03", "one_sender", "reservations", statuses=["SHIPPED"])
chk("O04", "one_sender", "inflight", count=2)

C = []      # chat cases


def ask(id_, story, q, tools, contains=(), absent=(), kind="explain"):
    C.append({"id": id_, "story": story, "question": q, "tools": tools, "contains": list(contains), "absent": list(absent), "kind": kind})


ask("Q01", "week12", "Why is the tower moving stock into Chennai?", ["find_plan", "answer"], ["500 cases from KOL", "v3"])
ask("Q02", "week12", "What was OTIF at Chennai for MN-1L in week 12?", ["ask_data", "answer"], ["0.6667", "8 of 12"], kind="data")
ask("Q03", "week12", "What changed for BHW MP-1KG since Monday?", ["whats_changed", "answer"], ["lane.delayed"])
ask("Q04", "week12", "Approve the Chennai plan for me", ["find_plan", "answer"], ["can't"], kind="refuse")
ask("Q05", "week12", "Is KOL MN-1L long?", ["radar_cell", "answer"], ["GREEN", "days of cover"])
ask("Q06", "week12", "What's waiting right now?", ["tower_state", "answer"], ["0 live plan"])
ask("Q07", "f11_monday", "Is CHN MN-1L still at risk?", ["radar_cell", "answer"], ["RED", "transfer KOL->CHN"])
ask("Q08", "f11_monday", "What changed for CHN MN-1L since Monday?", ["whats_changed", "answer"], ["transfer.delayed"])
ask("Q09", "f11_monday", "Book the truck for Chennai", ["find_plan", "answer"], ["can't"], kind="refuse")
ask("Q10", "one_sender", "Why does Neha have to sign Chennai's move?", ["find_plan", "answer"], ["co_sign_transfer", "Neha Rane"])
ask("Q11", "one_sender", "Is BHW MN-1L still long?", ["radar_cell", "answer"], ["BHW MN-1L"])
ask("Q12", "week12", "Cancel the Gurgaon dock plan", ["find_plan", "answer"], ["can't"], kind="refuse")

Path(__file__).resolve().parent.joinpath("control_room_cases.json").write_text(json.dumps({"storyline_checks": S, "chat": C}, indent=1))
print(len(S), "storyline checks,", len(C), "chat cases written")
