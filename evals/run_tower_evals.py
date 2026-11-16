"""Run the tower's end-to-end scenarios. Offline by default (every brain scripted); --live uses Bedrock
for the master and the specialists alike.

Pass = root cause, the specialists called, the plan's step codes (in order, where pinned), the dropped
steps, named approvers present, and the stop reason where the case pins one (the traps). Exit 1 below
the threshold or on any trap failing.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from foundry.store import Store                               # noqa: E402
from foundry.data.generate import canonical_store             # noqa: E402
from foundry.profile import load_profile                      # noqa: E402
from foundry.levers import Levers                             # noqa: E402
from foundry.approval import ProposalStore                    # noqa: E402
from foundry.tower import agent                               # noqa: E402
from foundry.tower.fake import FakeTowerClient                # noqa: E402

CASES = Path(__file__).resolve().parent / "tower_cases.json"


def build_store(kind: str) -> Store:
    if kind == "canonical":
        return canonical_store()
    if kind == "canonical_refreshed":
        return canonical_store(refreshed=True)
    return Store.from_position()


def judge(res, exp: dict) -> tuple[bool, str]:
    if "stop_reason" in exp:
        return (res.stop_reason == exp["stop_reason"], f"stop {res.stop_reason}")
    if res.stop_reason != "submitted" or not res.plan:
        return (False, f"stop {res.stop_reason}")
    p = res.plan
    if p.get("root_cause") != exp.get("root_cause"):
        return (False, f"cause {p.get('root_cause')}")
    if "dc" in exp and p.get("dc") != exp["dc"]:
        return (False, f"dc {p.get('dc')}")
    if "sku" in exp and p.get("sku") != exp["sku"]:
        return (False, f"sku {p.get('sku')}")
    if sorted(p.get("specialists_called", [])) != sorted(exp.get("specialists", [])):
        return (False, f"specialists {p.get('specialists_called')}")
    codes = [s["code"] for s in p.get("plan", [])]
    if "steps" in exp and codes != exp["steps"]:
        return (False, f"steps {codes}")
    dropped = [d["code"] for d in p.get("dropped", [])]
    if "dropped" in exp and sorted(dropped) != sorted(exp["dropped"]):
        return (False, f"dropped {dropped}")
    approvers = {a.get("approver") for a in p.get("approvals", [])}
    for who in exp.get("approvers_include", []):
        if who not in approvers:
            return (False, f"approver {who} missing from {sorted(approvers)}")
    if "transfer_cases" in exp:
        t = next((s for s in p["plan"] if s["code"] == "inter_dc_transfer"), {})
        if int(t.get("cases") or 0) != exp["transfer_cases"]:
            return (False, f"cases {t.get('cases')}")
    if "summary_contains" in exp and exp["summary_contains"] not in p.get("summary", ""):
        return (False, f"summary {p.get('summary')!r}")
    if "note_contains" in exp:
        notes = " ".join(s.get("note", "") for s in p["plan"])
        if exp["note_contains"] not in notes:
            return (False, f"note {notes!r}")
    return (True, "ok")


def run_case(c: dict, client_factory, tmp: Path, live: bool) -> dict:
    store = build_store(c["store"])
    profile = load_profile(c.get("profile", "gfb"))
    levers = Levers(tmp / f"panel-{c['id']}.json", tmp / "audit.jsonl")
    if c.get("levers"):
        levers.set(**c["levers"])
    props = ProposalStore(tmp / f"proposals-{c['id']}.jsonl")
    t0 = time.time()
    kwargs = {"budget_usd": c["budget_usd"]} if c.get("budget_usd") else {}
    res = agent.run(dict(c["task"]), store, profile, client_factory(), levers, props, **kwargs)
    ok, why = judge(res, c["expect"])
    p = res.plan or {}
    got = {"root_cause": p.get("root_cause"), "specialists": p.get("specialists_called"), "steps": [s["code"] for s in p.get("plan", [])],
           "dropped": [d["code"] for d in p.get("dropped", [])], "approvers": sorted({a.get("approver") for a in p.get("approvals", [])}), "stop": res.stop_reason}
    return {"id": c["id"], "kind": c["kind"], "pass": ok, "why": why, "expected": c["expect"], "got": got, "turns": res.turns,
            "cost_usd": res.total_meter.get("cost_usd"), "elapsed_s": round(time.time() - t0, 2), "note": c.get("note", "")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.90)
    ap.add_argument("--only", default=None)
    ap.add_argument("--out", default="evals/tower_results.json")
    ap.add_argument("--skip-specialists", action="store_true", help="the master that ignores the route (to watch the scenarios fail)")
    args = ap.parse_args(argv)
    if args.live:
        from foundry.bedrock_client import BedrockClient
        client_factory = lambda: BedrockClient()      # noqa: E731
    else:
        client_factory = lambda: FakeTowerClient(skip_specialists=args.skip_specialists)   # noqa: E731
    cases = json.loads(CASES.read_text())
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    tmp = Path(tempfile.mkdtemp(prefix="foundry-tower-evals-"))
    rows = [run_case(c, client_factory, tmp, args.live) for c in cases]
    passed = sum(r["pass"] for r in rows)
    rate = passed / len(rows) if rows else 0.0
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], [0, 0])
        by_kind[r["kind"]][1] += 1
        by_kind[r["kind"]][0] += int(r["pass"])
    traps_ok = all(r["pass"] for r in rows if r["kind"] == "trap")
    gate = rate >= args.threshold and traps_ok
    total_cost = sum(r["cost_usd"] or 0 for r in rows)
    summary = {"ran_at": time.strftime("%Y-%m-%d %H:%M:%S"), "live": args.live, "agent": "tower", "cases": len(rows), "passed": passed,
               "pass_rate": round(rate, 3), "by_kind": {k: f"{v[0]}/{v[1]}" for k, v in by_kind.items()}, "total_cost_usd": round(total_cost, 4),
               "threshold": args.threshold, "traps_all_pass": traps_ok, "gate": "PASS" if gate else "FAIL"}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(f"{'id':5} {'kind':11} {'pass':5} {'cause':20} {'specialists':26} {'steps'}")
    for r in rows:
        g = r["got"]
        print(f"{r['id']:5} {r['kind']:11} {'ok' if r['pass'] else 'FAIL':5} {str(g['root_cause']):20} {str(g['specialists']):26} {g['steps']}  {'' if r['pass'] else r['why']}")
    print(json.dumps(summary, indent=1))
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
