"""The Control Room's evals: storyline checks (the tower's behaviour, 100% required) and Ask the tower
(>= 90%, and every refusal must hold). Offline on the scripted brains; --out writes the results JSON.

    python evals/run_control_room_evals.py [--out evals/control_room_results.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from foundry.chat import agent as chat                       # noqa: E402
from foundry.orchestrator.scenarios import STORYLINES        # noqa: E402


def _plan(rt, c):
    rows = [p for p in rt.plans.values() if p["dc"] == c["dc"] and p["sku"] == c.get("sku")
            and ("version" not in c or p["version"] == c["version"]) and ("mode" not in c or p["mode"] == c["mode"])]
    return rows[0] if rows else None


def check(rt, log, c) -> tuple[bool, str]:
    k = c["kind"]
    step = next((s for s in log if s["label"] == c.get("step")), None)
    if k == "transfer":
        p = _plan(rt, c)
        t = next((s for s in (p or {}).get("steps", []) if s["code"] == "inter_dc_transfer"), None)
        got = (t["from_dc"], t["cases"], t.get("lands_end_of_day")) if t else None
        return got == (c["from_dc"], c["cases"], c["lands"]), str(got)
    if k == "what_if":
        w = (_plan(rt, c) or {}).get("what_if")
        got = (w["before"]["money_at_risk"], w["after"]["money_at_risk"]) if w else None
        return got == (c["before"], c["after"]), str(got)
    if k == "cost":
        return step is not None and step["cost_inr"] == c["cost_inr"], str(step and step["cost_inr"])
    if k == "plan_status":
        p = _plan(rt, c)
        return bool(p) and p["status"] == c["status"], str(p and p["status"])
    if k == "plan_steps":
        p = _plan(rt, c)
        got = [s["code"] for s in p["steps"]] if p else None
        return got == c["codes"], str(got)
    if k == "plan_count":
        return len(rt.plans) == c["count"], str(len(rt.plans))
    if k == "plan_count_status":
        n = sum(1 for p in rt.plans.values() if p["status"] == c["status"])
        return n == c["count"], str(n)
    if k == "message":
        hit = [m for m in rt.outbox.items if m["to"] == c["to"] and m["kind"] == c["msg_kind"] and c["contains"] in (m["subject"] + m["body"])]
        return bool(hit), f"{len(hit)} matching"
    if k == "history_event":
        p = _plan(rt, c)
        return bool(p) and any(h["event"] == c["event"] for h in p["history"]), str(p and [h["event"] for h in p["history"]])
    if k == "versions":
        rows = [p["status"] for p in rt.plans.values() if p["dc"] == c["dc"] and p["sku"] == c["sku"]]
        return rows == c["statuses"], str(rows)
    if k == "not_accepted":
        return step is not None and step["accepted"] is False, str(step and step["accepted"])
    if k == "note_contains":
        notes = " ".join(n for e in (step or {}).get("events", []) for n in (e.get("notes") or []))
        return c["text"] in notes, notes[:120]
    if k == "signed_by":
        p = _plan(rt, c)
        return bool(p) and any(s["decided_by"] == c["who"] for s in p["steps"]), str(p and [s["decided_by"] for s in p["steps"]])
    if k == "dropped_by":
        p = _plan(rt, c)
        return bool(p) and any(d["by"] == c["by"] for d in p["dropped"]), str(p and [d["by"] for d in p["dropped"]])
    if k == "no_dead_letters":
        return not rt.bus.dead, str(len(rt.bus.dead))
    if k == "reservations":
        got = sorted({r["status"] for r in rt.store.extra.get("reservations", [])})
        return got == c["statuses"], str(got)
    if k == "inflight":
        n = len(rt.store.extra.get("transfers_inflight", []))
        return n == c["count"], str(n)
    return False, f"unknown check kind {k}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--threshold", type=float, default=0.9, help="the chat set's pass rate; storyline checks always need 100%%")
    a = ap.parse_args(argv)
    cases = json.loads(Path(__file__).with_name("control_room_cases.json").read_text())
    tmp = Path(tempfile.mkdtemp(prefix="cr-evals-"))
    runs = {name: fn(tmp / name) for name, (_, fn) in STORYLINES.items()}
    results = []
    for c in cases["storyline_checks"]:
        rt, log = runs[c["story"]]
        ok, got = check(rt, log, c)
        results.append({"id": c["id"], "set": "storyline", "kind": c["kind"], "pass": ok, "got": got, "note": c.get("note", "")})
    cost = 0.0
    for c in cases["chat"]:
        rt, _ = runs[c["story"]]
        r = chat.ask(c["question"], rt)
        text = (r.answer or {}).get("text", "")
        tools = [t.get("tool") for t in r.tool_calls]
        ok = r.stop_reason == "submitted" and tools == c["tools"] and all(s in text for s in c["contains"]) and not any(s in text for s in c["absent"])
        cost += r.meter.get("cost_usd", 0)
        results.append({"id": c["id"], "set": "chat", "kind": c["kind"], "pass": ok, "got": text[:160], "tools": tools})
    st = [r for r in results if r["set"] == "storyline"]
    ch = [r for r in results if r["set"] == "chat"]
    refusals_hold = all(r["pass"] for r in ch if r["kind"] == "refuse")
    summary = {"ran_at": time.strftime("%Y-%m-%d %H:%M:%S"), "storyline": f"{sum(r['pass'] for r in st)}/{len(st)}",
               "chat": f"{sum(r['pass'] for r in ch)}/{len(ch)}", "passed": sum(r["pass"] for r in results), "cases": len(results),
               "refusals_hold": refusals_hold, "chat_cost_usd": round(cost, 4),
               "gate": "PASS" if all(r["pass"] for r in st) and sum(r["pass"] for r in ch) >= a.threshold * len(ch) and refusals_hold else "FAIL"}
    for r in results:
        if not r["pass"]:
            print(f"FAIL {r['id']} {r['kind']}: got {r['got']}")
    print(json.dumps(summary, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps({"summary": summary, "results": results}, indent=1))
    return 0 if summary["gate"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
