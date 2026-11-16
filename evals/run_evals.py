"""Run the eval set. Offline by default (FakeBedrockClient); --live uses Bedrock.

Pass = root cause matches (and priority, where the case pins one; and the contributing cause, where
pinned). The run exits non-zero below the threshold, which is what makes it a CI gate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from foundry.data.generate import Injection, build          # noqa: E402
from foundry.profile import load_profile                     # noqa: E402
from foundry.levers import Levers                            # noqa: E402
from foundry.approval import ProposalStore                   # noqa: E402
from foundry.bedrock_client import FakeBedrockClient         # noqa: E402
from foundry.agents.rca_exception import agent               # noqa: E402

CASES = Path(__file__).resolve().parent / "cases.json"


def run_case(c: dict, profile, client_factory, tmp: Path) -> dict:
    inj = [Injection(i["kind"], i["week"], i["dc"], i.get("sku"), i.get("params", {})) for i in c["injections"]]
    store = build(inj, seed=7)
    levers = Levers(tmp / "panel.json", tmp / "audit.jsonl")
    props = ProposalStore(tmp / f"proposals-{c['id']}.jsonl")
    t0 = time.time()
    res = agent.run(dict(c["task"]), store, profile, client_factory(), levers, props)
    d = res.diagnosis or {}
    exp = c["expect"]
    ok = res.stop_reason == "submitted" and d.get("root_cause") == exp["root_cause"]
    if ok and "priority" in exp:
        ok = d.get("priority") == exp["priority"]
    if ok and "contributing" in exp:
        ok = exp["contributing"] in (d.get("contributing") or [])
    return {"id": c["id"], "kind": c["kind"], "pass": bool(ok), "expected": exp, "got": {"root_cause": d.get("root_cause"), "priority": d.get("priority"), "contributing": d.get("contributing"), "confidence": d.get("confidence")},
            "stop_reason": res.stop_reason, "turns": res.turns, "cost_usd": res.meter.get("cost_usd"), "elapsed_s": round(time.time() - t0, 2), "note": c.get("note", "")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use Claude on Bedrock instead of the scripted fake")
    ap.add_argument("--profile", default="gfb")
    ap.add_argument("--threshold", type=float, default=0.90)
    ap.add_argument("--only", default=None, help="comma-separated case ids")
    ap.add_argument("--out", default="evals/results.json")
    args = ap.parse_args(argv)
    profile = load_profile(args.profile)
    if args.live:
        from foundry.bedrock_client import BedrockClient
        client_factory = lambda: BedrockClient()      # noqa: E731
    else:
        client_factory = FakeBedrockClient
    cases = json.loads(CASES.read_text())
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    tmp = Path(tempfile.mkdtemp(prefix="foundry-evals-"))
    rows = [run_case(c, profile, client_factory, tmp) for c in cases]
    passed = sum(r["pass"] for r in rows)
    rate = passed / len(rows) if rows else 0.0
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], [0, 0])
        by_kind[r["kind"]][1] += 1
        by_kind[r["kind"]][0] += int(r["pass"])
    total_cost = sum(r["cost_usd"] or 0 for r in rows)
    summary = {"ran_at": time.strftime("%Y-%m-%d %H:%M:%S"), "live": args.live, "profile": args.profile, "cases": len(rows), "passed": passed,
               "pass_rate": round(rate, 3), "by_kind": {k: f"{v[0]}/{v[1]}" for k, v in by_kind.items()}, "total_cost_usd": round(total_cost, 4),
               "mean_cost_usd": round(total_cost / len(rows), 5) if rows else 0, "threshold": args.threshold, "gate": "PASS" if rate >= args.threshold else "FAIL"}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(f"{'id':5} {'kind':9} {'pass':5} {'expected':22} {'got':22} {'prio':4} {'conf':5} {'turns':5}")
    for r in rows:
        print(f"{r['id']:5} {r['kind']:9} {'ok' if r['pass'] else 'FAIL':5} {r['expected']['root_cause']:22} {str(r['got']['root_cause']):22} {str(r['got']['priority']):4} {str(r['got']['confidence']):5} {r['turns']:5}")
    print(json.dumps(summary, indent=1))
    return 0 if rate >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
