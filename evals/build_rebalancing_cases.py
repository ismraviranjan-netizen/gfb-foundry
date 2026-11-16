"""Write evals/rebalancing_cases.json: 20 cases the rebalancing agent must get right.

A case starts from the opening stock position (Store.from_position(), F.5 for GFB, week 1) and
overrides rows of the inventory table ("position"), or from the canonical incident store
("store": "canonical", week 12). Run once; edit the JSON by hand after.
"""
import json
from pathlib import Path

C = []


def case(id_, kind, task, expect, position=None, store="position", note=""):
    C.append({"id": id_, "kind": kind, "store": store, "position": position or [], "task": task, "expect": expect, "note": note})


def pos(dc, sku, on_hand, open_receipt=0, age=2.0):
    return {"dc": dc, "sku": sku, "on_hand": on_hand, "open_receipt": open_receipt, "snapshot_age_h": age}


def T(sku, week=1, to_dc=None, mode="transfer"):
    t = {"mode": mode, "week": week}
    if mode == "transfer":
        t["sku"] = sku
        if to_dc:
            t["to_dc"] = to_dc
    return t


MOVE = lambda f, t, cases, co=False: {"transfer": True, "from_dc": f, "to_dc": t, "cases": cases, "pays": True, "co_sign": co}   # noqa: E731
NONE = lambda alt=None: {"transfer": False, "alternative": alt}                                                                   # noqa: E731

# clear moves -----------------------------------------------------------------------------------
case("C01", "clear", T("MN-1L"), MOVE("KOL", "CHN", 3720), note="F.11: Chennai short, Kolkata long; the canon answer")
case("C02", "clear", T("MN-1L"), MOVE("BHW", "CHN", 3720), [pos("BHW", "MN-1L", 17000), pos("KOL", "MN-1L", 3000)], note="Bhiwandi long instead: the 3-day lane")
case("C03", "clear", T("MN-1L"), MOVE("BHW", "GGN", 900), [pos("GGN", "MN-1L", 0), pos("BHW", "MN-1L", 20000), pos("CHN", "MN-1L", 4000)], note="Gurgaon dry; Bhiwandi is the only lane into GGN; the plant lands on day 5, so only days 4-5 can be saved: a 900-case move, not 6,200 (changed 8 Nov when the transfer-timing flaw was fixed)")
case("C04", "clear", T("GC-200"), MOVE("BHW", "CHN", 2340), [pos("CHN", "GC-200", 400), pos("BHW", "GC-200", 9000)], note="chakli: a two-week lead into Chennai makes the move pay")
case("C05", "clear", T("MN-1L"), MOVE("BHW", "CHN", 3720), [pos("BHW", "MN-1L", 17000)], note="two long DCs: the cheapest lane that lands in time wins")
case("C06", "clear", T("MN-1L"), MOVE("KOL", "CHN", 5200), [pos("CHN", "MN-1L", 0)], note="Chennai dry: no lane is in time, the fastest that lands before the receipt is taken")
case("C07", "clear", T("MN-1L"), MOVE("KOL", "CHN", 3720), [pos("BHW", "MN-1L", 17000, age=30)], note="the cheaper sender is stale; the fresh one is taken")
case("C08", "clear", T("GC-200"), MOVE("BHW", "GGN", 500), [pos("GGN", "GC-200", 0), pos("BHW", "GC-200", 12000), pos("CHN", "GC-200", 4000)], note="chakli into Gurgaon: sized to the two days the 3-day lane can save (was 3,266 before the timing fix)")
case("C09", "clear", T("MN-1L"), MOVE("KOL", "CHN", 3200), [pos("CHN", "MN-1L", 2000)], note="Chennai at 5.4 days: a smaller top-up")
case("C10", "clear", T("MN-1L"), MOVE("BHW", "GGN", 900), [pos("GGN", "MN-1L", 1000), pos("BHW", "MN-1L", 20000), pos("CHN", "MN-1L", 4000)], note="topping up to 14 days would not pay once the timing is right; the gap-sized move does (was 5,200)")
# no move ---------------------------------------------------------------------------------------
case("N11", "no_move", T("MN-1L"), NONE("expedite_plant_run"), [pos("KOL", "MN-1L", 3000)], note="nobody is long: ask the plant")
case("C21", "clear", T("MN-1L"), MOVE("KOL", "CHN", 3200), [pos("CHN", "MN-1L", 0, open_receipt=2000)], note="was N12 until 8 Nov: the slipped truck lands in two days, but its 2,000 cases run out on day 7 and the plant's truck lands on day 12; priced on the shared projection the move saves INR 1,06,286 for INR 70,400 of lane (the one-receipt rule said wait)")
case("N12", "no_move", T("MN-1L"), NONE("expedite_alternate_carrier"), [pos("CHN", "MN-1L", 0, open_receipt=5000)], note="the slipped truck lands in two days and its 5,000 cases cover Chennai until the plant's truck: wait")
case("N13", "no_move", T("TG-250"), NONE(), [pos("CHN", "TG-250", 130), pos("KOL", "TG-250", 600)], note="a C item: the move would be 150 cases, under the 200 minimum")
case("N14", "no_move", T("LJ-1L"), NONE(), [pos("CHN", "LJ-1L", 100), pos("KOL", "LJ-1L", 3000)], note="one-week lead, low contribution: the move does not pay")
case("N15", "no_move", T("MN-1L"), NONE("expedite_plant_run"), [pos("KOL", "MN-1L", 500), pos("BHW", "MN-1L", 17000), pos("CHN", "MN-1L", 4000)], note="Kolkata short but no inter-DC lane runs into Kolkata")
case("N16", "no_move", T(None, mode="sweep"), NONE(), [pos("CHN", "MN-1L", 4000)], note="a sweep with nobody short")
# traps -----------------------------------------------------------------------------------------
case("T17", "trap", T("MN-1L", week=12), NONE("refresh_snapshot"), store="canonical", note="week 12: Kolkata's snapshot is 36 h old; the only long DC is not to be believed")
case("T18", "trap", T("MN-1L"), NONE("expedite_plant_run"), [pos("CHN", "MN-1L", 1000), pos("GGN", "MN-1L", 1000), pos("KOL", "MN-1L", 3000)], note="two DCs short, nobody long")
case("T19", "trap", T("MN-1L"), MOVE("KOL", "CHN", 2605, co=True), [pos("KOL", "MN-1L", 4000), pos("CHN", "MN-1L", 0)], note="the move takes Kolkata below its long line down to its ROP: Arjun co-signs")
case("T20", "trap", T("MN-1L"), NONE("refresh_snapshot"), [pos("CHN", "MN-1L", 1480, age=30)], note="the receiver's own snapshot is stale: refresh before pulling stock towards it")

Path(__file__).resolve().parent.joinpath("rebalancing_cases.json").write_text(json.dumps(C, indent=1))
print(len(C), "cases written")
