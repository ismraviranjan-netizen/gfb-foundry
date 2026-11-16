"""Run the queue eval set. Offline by default (FakeQueueClient); --live uses Bedrock for the digest cases.

Deterministic checks (order, grouping, dedupe, items, the clock) run the queue code directly;
agent checks run the digest agent and compare its numbers with the queue's. Exit 1 below the
threshold, which is what makes it a CI gate.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from foundry.data.generate import canonical_store                    # noqa: E402
from foundry.profile import load_profile                              # noqa: E402
from foundry.levers import Levers                                     # noqa: E402
from foundry.agents.rca_exception import queue as Q                   # noqa: E402
from foundry.agents.rca_exception import queue_agent                  # noqa: E402
from foundry.agents.rca_exception.queue_fake import FakeQueueClient   # noqa: E402

CASES = Path(__file__).resolve().parent / "queue_cases.json"


def _by_key(q: Q.Queue) -> dict[str, Q.ExceptionItem]:
    return {it.key: it for it in q.items}


def _check_tick(c: dict, q: Q.Queue, profile) -> tuple[bool, dict]:
    keys = _by_key(q)
    for step in c["pre"]:
        if "decide" in step:
            d = step["decide"]
            Q.decide(q, keys[d["key"]].id, d["approver"], d["decision"], d.get("at", 0.0), d.get("note", ""))
    r = Q.tick(q, profile, float(c["now_hours"]))
    id_to_key = {it.id: it.key for it in q.items}
    got = {"reminded": [id_to_key[i] for i in r["reminded"]], "escalated": [id_to_key[i] for i in r["escalated"]],
           "approvers": {it.key: it.approvers for it in q.items}, "state": {it.key: it.state for it in q.items}}
    exp = c["expect"]
    ok = True
    if "reminded" in exp:
        ok &= sorted(got["reminded"]) == sorted(exp["reminded"])
    if "escalated" in exp:
        ok &= sorted(got["escalated"]) == sorted(exp["escalated"])
    for k in exp.get("not_escalated", []):
        ok &= not keys[k].escalated
    for k in exp.get("not_reminded", []):
        ok &= keys[k].reminded_at is None
    if "approver_added" in exp:
        ok &= all(any(exp["approver_added"] in a for a in keys[k].approvers) for k in exp.get("escalated", []))
    for k, st in exp.get("state", {}).items():
        ok &= keys[k].state == st
    return bool(ok), got


def _check_structure(c: dict, q: Q.Queue) -> tuple[bool, dict]:
    exp = c["expect"]
    keys = _by_key(q)
    if c["check"] == "order":
        got = {"keys": [it.key for it in q.items], "built_from": q.built_from}
        return got["keys"] == exp["keys"] and got["built_from"] == exp["built_from"], got
    it = keys.get(exp["key"])
    if it is None:
        return False, {"missing": exp["key"], "keys": list(keys)}
    got = {"key": it.key, "kpi": it.kpi, "root_cause": it.root_cause, "confidence": it.confidence, "priority": it.priority,
           "impact_inr": it.impact_inr, "sla_hours": it.sla_hours, "kpis_moved": it.kpis_moved, "children_count": len(it.children),
           "grouped_keys": [g["key"] for g in it.grouped], "approvers": it.approvers}
    ok = True
    for f in ("kpi", "root_cause", "confidence", "priority", "impact_inr", "sla_hours", "kpis_moved", "children_count", "grouped_keys"):
        if f in exp:
            ok &= got[f] == exp[f]
    if "approver_contains" in exp:
        ok &= any(exp["approver_contains"] in a for a in it.approvers)
    return bool(ok), got


def _check_agent(c: dict, store, profile, client_factory, tmp: Path) -> tuple[bool, dict]:
    exp = c["expect"]
    fake = dict(c.get("fake") or {})
    q = Q.build_queue(store, profile, int(c["week"]))
    keys = _by_key(q)
    if fake.get("escalate_early") in keys:
        fake["escalate_early"] = keys[fake["escalate_early"]].id
    levers = Levers(tmp / "panel.json", tmp / "audit.jsonl")
    client = client_factory(**fake) if fake else client_factory()
    res = queue_agent.run({"mode": "digest", "week": int(c["week"]), "now_hours": float(c["now_hours"] or 0)}, store, profile, client, levers,
                          queue=q, persist=False)
    d = res.digest or {}
    truth = Q.digest(q, float(c["now_hours"] or 0))
    got = {"stop": res.stop_reason, "turns": res.turns, "breaches": d.get("breaches"), "p1_count": d.get("p1_count"), "p3_count": d.get("p3_count"),
           "lines": len(d.get("lines", [])), "headline": d.get("headline"), "money_at_stake_inr": d.get("money_at_stake_inr"),
           "escalated_count": len(res.escalated), "refused": [t["tool"] for t in res.tool_calls if "error" in (t.get("keys") or [])],
           "queue_breaches": truth["breach_count"], "queue_open_p1": truth["open_by_priority"]["P1"], "cost_usd": res.meter.get("cost_usd")}
    ok = res.stop_reason == exp.get("stop", "submitted")
    for f in ("breaches", "p1_count", "p3_count", "lines", "money_at_stake_inr", "escalated_count"):
        if f in exp:
            ok &= got[f] == exp[f]
    if "exceptions" in exp:
        ok &= len(q.items) == exp["exceptions"]
    if "headline_contains" in exp:
        ok &= exp["headline_contains"] in (d.get("headline") or "")
    if exp.get("counts_match_queue"):
        ok &= d.get("breaches") == truth["breach_count"] and d.get("p1_count") == truth["open_by_priority"]["P1"] \
            and d.get("money_at_stake_inr") == truth["money_at_stake_inr"]
    if "refused_tool" in exp:
        ok &= exp["refused_tool"] in got["refused"]
    return bool(ok), got


def run_case(c: dict, client_factory, tmp: Path) -> dict:
    profile = load_profile(c.get("profile", "gfb"))
    store = canonical_store()
    t0 = time.time()
    if c["check"] == "tick":
        q = Q.build_queue(store, profile, int(c["week"]))
        ok, got = _check_tick(c, q, profile)
    elif c["check"] == "agent":
        ok, got = _check_agent(c, store, profile, client_factory, tmp)
    else:
        q = Q.build_queue(store, profile, int(c["week"]))
        ok, got = _check_structure(c, q)
    return {"id": c["id"], "kind": c["kind"], "check": c["check"], "pass": bool(ok), "expected": c["expect"], "got": got,
            "cost_usd": got.get("cost_usd") if isinstance(got, dict) else None, "elapsed_s": round(time.time() - t0, 2), "note": c.get("note", "")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use Claude on Bedrock for the digest cases")
    ap.add_argument("--profile", default=None, help="override the profile for every case")
    ap.add_argument("--threshold", type=float, default=0.90)
    ap.add_argument("--only", default=None, help="comma-separated case ids")
    ap.add_argument("--out", default="evals/queue_results.json")
    args = ap.parse_args(argv)
    if args.live:
        from foundry.bedrock_client import BedrockClient
        client_factory = lambda **kw: BedrockClient()      # noqa: E731
    else:
        client_factory = FakeQueueClient
    cases = json.loads(CASES.read_text())
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    if args.profile:
        for c in cases:
            c["profile"] = args.profile
    tmp = Path(tempfile.mkdtemp(prefix="foundry-queue-evals-"))
    rows = [run_case(c, client_factory, tmp) for c in cases]
    passed = sum(r["pass"] for r in rows)
    rate = passed / len(rows) if rows else 0.0
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], [0, 0])
        by_kind[r["kind"]][1] += 1
        by_kind[r["kind"]][0] += int(r["pass"])
    total_cost = sum((r["cost_usd"] or 0) for r in rows)
    summary = {"ran_at": time.strftime("%Y-%m-%d %H:%M:%S"), "live": args.live, "cases": len(rows), "passed": passed,
               "pass_rate": round(rate, 3), "by_kind": {k: f"{v[0]}/{v[1]}" for k, v in by_kind.items()},
               "total_cost_usd": round(total_cost, 4), "threshold": args.threshold, "gate": "PASS" if rate >= args.threshold else "FAIL"}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(f"{'id':5} {'kind':7} {'check':6} {'pass':5} note")
    for r in rows:
        print(f"{r['id']:5} {r['kind']:7} {r['check']:6} {'ok' if r['pass'] else 'FAIL':5} {r['note']}")
    print(json.dumps(summary, indent=1))
    return 0 if rate >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
