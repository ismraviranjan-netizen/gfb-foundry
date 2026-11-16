"""Write evals/sensing_cases.json: 24 cases the sensing agent must get right. Run once; edit by hand after.

history: "sensing" = generate.sensing_history() (20 weeks, seed 11, trends, a season, ten promos);
or a dict {seed, weeks, trend {"DC:SKU": pct}, season {"DC:SKU": [amp, peak, period]}, promos [[week, dc, sku, name, uplift]],
injections [[kind, week, dc, sku, {params}]]} the runner turns into build(...).
expect: {"decision": "adjust", "sign": "+"|"-", "co_sign": bool} or {"decision": "silent"}.
"""
import json
from pathlib import Path

C = []


def case(id_, kind, history, task, expect, note=""):
    C.append({"id": id_, "kind": kind, "history": history, "task": task, "expect": expect, "note": note})


def T(dc, sku, origin, horizon=2):
    return {"mode": "sense", "dc": dc, "sku": sku, "origin_week": origin, "horizon": horizon}


def H(seed, weeks=20, trend=None, season=None, promos=None, injections=None):
    return {"seed": seed, "weeks": weeks, "trend": trend or {}, "season": season or {}, "promos": promos or [], "injections": injections or []}


# adjust ----------------------------------------------------------------------------------------
case("A01", "adjust", "sensing", T("CHN", "MN-1L", 17), {"decision": "adjust", "sign": "+", "co_sign": True}, "Pongal pack in the calendar for week 18, uplift calibrated on Bhiwandi's summer promo; the trend confirms week 19")
case("A02", "adjust", "sensing", T("BHW", "GC-200", 13), {"decision": "adjust", "sign": "+", "co_sign": True}, "Diwali chakli weeks 14-15: the calendar says +100%, Gurgaon's past chakli promos say it realises about 80%")
case("A03", "adjust", "sensing", T("GGN", "GC-200", 18), {"decision": "adjust", "sign": "+", "co_sign": True}, "year-end chakli at Gurgaon: own history (Holi, monsoon) sizes the uplift")
case("A04", "adjust", "sensing", T("CHN", "MN-1L", 19), {"decision": "adjust", "sign": "+"}, "the 1.5% a week Chennai trend, visible by week 19 without a promo")
case("A05", "adjust", H(12, trend={"BHW:MN-1L": 0.02}), T("BHW", "MN-1L", 18), {"decision": "adjust", "sign": "+"}, "a 2% a week climb at Bhiwandi")
case("A06", "adjust", H(13, trend={"GGN:GC-200": -0.015}), T("GGN", "GC-200", 19), {"decision": "adjust", "sign": "-"}, "chakli at Gurgaon shrinking 1.5% a week")
case("A07", "adjust", H(21, season={"CHN:MN-200": [0.25, 20, 52]}), T("CHN", "MN-200", 16), {"decision": "adjust", "sign": "+"}, "a season rising into its peak")
case("A08", "adjust", H(22, promos=[[6, "KOL", "GC-200", "Spring chakli", 0.5], [10, "KOL", "GC-200", "Puja chakli", 0.5], [17, "KOL", "GC-200", "Winter chakli", 0.5]]), T("KOL", "GC-200", 16), {"decision": "adjust", "sign": "+", "co_sign": True}, "a promo with two of its own past promos to learn from")
case("A09", "adjust", H(23, promos=[[17, "BHW", "LJ-1L", "Lime launch", 0.3]]), T("BHW", "LJ-1L", 16), {"decision": "adjust", "sign": "+", "co_sign": True}, "a promo with no history: the calendar's own number, lower confidence")
case("A10", "adjust", H(14, trend={"KOL:AJ-1L": -0.02}), T("KOL", "AJ-1L", 19), {"decision": "adjust", "sign": "-"}, "apple juice fading at Kolkata")
case("A11", "adjust", H(15, trend={"GGN:MN-200": 0.025}), T("GGN", "MN-200", 17), {"decision": "adjust", "sign": "+"}, "small packs climbing at Gurgaon")
case("A12", "adjust", H(16, trend={"CHN:MN-1L": 0.015}, promos=[[18, "CHN", "MN-1L", "Pongal pack", 0.35]]), T("CHN", "MN-1L", 17), {"decision": "adjust", "sign": "+", "co_sign": True}, "trend and a promo together")
# silent ----------------------------------------------------------------------------------------
case("S13", "silent", "sensing", T("KOL", "MN-1L", 16), {"decision": "silent"}, "the blend won 7 of 8 held-out weeks, but the series sat above the consensus for one week only: silent, and the truth says the consensus was right")
case("S14", "silent", "sensing", T("GGN", "MN-200", 16), {"decision": "silent"}, "the back-test keeps the consensus (w = 1): nothing beats it")
case("S15", "silent", "sensing", T("CHN", "MN-200", 16), {"decision": "silent"}, "a 4.9% nudge is under the 5% bar")
case("S16", "silent", "sensing", T("BHW", "MN-1L", 16), {"decision": "silent"}, "better on average, but only 4 of 8 weeks")
case("S17", "silent", H(101), T("CHN", "MN-1L", 16), {"decision": "silent"}, "pure noise")
case("S18", "silent", H(102), T("BHW", "GC-200", 16), {"decision": "silent"}, "pure noise")
case("S19", "silent", H(24, trend={"CHN:MN-1L": 0.003}), T("CHN", "MN-1L", 19), {"decision": "silent"}, "a trend too small to act on")
case("S20", "silent", H(103), T("GGN", "MN-1L", 16), {"decision": "silent"}, "pure noise")
# traps -----------------------------------------------------------------------------------------
case("T21", "trap", H(104, injections=[["demand_spike", 16, "BHW", "MN-1L", {"pct": 1.0}]]), T("BHW", "MN-1L", 16), {"decision": "silent"}, "a one-week doubling at the origin, not in the calendar: wait a week")
case("T22", "trap", "sensing", T("KOL", "MN-200", 10), {"decision": "silent"}, "ten closed weeks are too few to hold any out")
case("T23", "trap", H(25, promos=[[17, "BHW", "AJ-1L", "Apple week", 0.0]]), T("BHW", "AJ-1L", 16), {"decision": "silent"}, "a promo in the calendar with no number and no history: ask, do not guess")
case("T24", "trap", H(105), T("KOL", "MN-1L", 16), {"decision": "silent"}, "the consensus already beats the smoothing: keep it")

Path(__file__).resolve().parent.joinpath("sensing_cases.json").write_text(json.dumps(C, indent=1))
print(len(C), "cases")
