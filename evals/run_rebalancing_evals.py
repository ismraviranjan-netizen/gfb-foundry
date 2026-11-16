"""Run the rebalancing eval set. Offline by default (FakeRebalancingClient); --live uses Bedrock.

Pass = the agent moves when the case says move (same sender, same receiver, cases within 1%, pays,
co-sign flag as pinned) and proposes nothing when the case says no move (with the alternative, where
pinned). The gate is the pass rate at the threshold AND every trap passing: a trap failing means the
agent moved stock on a bad number. The run exits non-zero below the gate, which makes it a CI gate.
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
from foundry.agents.rebalancing import agent                  # noqa: E402
from foundry.agents.rebalancing.fake import FakeRebalancingClient   # noqa: E402

CASES = Path(__file__).resolve().parent / "rebalancing_cases.json"


def build_store(c: dict) -> Store:
    if c.get("store") == "canonical":
        return canonical_store()
    s = Store.from_position()
    for o in c.get("position", []):
        for r in s.inventory:
            if r["dc"] == o["dc"] and r["sku"] == o["sku"]:
                r["on_hand"] = o["on_hand"]
                r["open_receipt"] = o.get("open_receipt", 0)
                r["snapshot_age_h"] = o.get("snapshot_age_h", 2.0)
    return s


def judge(plan: dict | None, exp: dict, stop: str) -> bool:
    if stop != "submitted" or plan is None:
        return False
    moved = bool(plan.get("from_dc")) and int(plan.get("cases") or 0) > 0
    if not exp["transfer"]:
        if moved:
            return False
        if exp.get("alternative") and plan.get("alternative") != exp["alternative"]:
            return False
        return True
    if not moved:
        return False
    if plan.get("from_dc") != exp["from_dc"] or plan.get("to_dc") != exp["to_dc"]:
        return False
    if abs(int(plan["cases"]) - exp["cases"]) > max(1, 0.01 * exp["cases"]):
        return False
    if bool(plan.get("pays")) != exp["pays"]:
        return False
    if "co_sign" in exp and bool(plan.get("co_sign_needed")) != exp["co_sign"]:
        return False
    return True


def run_case(c: dict, profile, client_factory, tmp: Path) -> dict:
    store = build_store(c)
    levers = Levers(tmp / "panel.json", tmp / "audit.jsonl")
    props = ProposalStore(tmp / f"proposals-{c['id']}.jsonl")
    t0 = time.time()
    res = agent.run(dict(c["task"]), store, profile, client_factory(), levers, props)
    p = res.plan or {}
    ok = judge(res.plan, c["expect"], res.stop_reason)
    got = {"from_dc": p.get("from_dc"), "to_dc": p.get("to_dc"), "cases": p.get("cases"), "pays": p.get("pays"),
           "co_sign": p.get("co_sign_needed"), "alternative": p.get("alternative"), "margin_saved": p.get("margin_saved_inr"), "cost": p.get("cost_inr")}
    return {"id": c["id"], "kind": c["kind"], "pass": bool(ok), "expected": c["expect"], "got": got, "stop_reason": res.stop_reason,
            "turns": res.turns, "cost_usd": res.meter.get("cost_usd"), "elapsed_s": round(time.time() - t0, 2), "note": c.get("note", "")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use Claude on Bedrock instead of the scripted fake")
    ap.add_argument("--profile", default="gfb")
    ap.add_argument("--threshold", type=float, default=0.90)
    ap.add_argument("--only", default=None, help="comma-separated case ids")
    ap.add_argument("--out", default="evals/rebalancing_results.json")
    ap.add_argument("--ignore-freshness", action="store_true", help="the fake that moves stock from a stale DC (to watch the traps fail)")
    args = ap.parse_args(argv)
    profile = load_profile(args.profile)
    if args.live:
        from foundry.bedrock_client import BedrockClient
        client_factory = lambda: BedrockClient()      # noqa: E731
    else:
        client_factory = lambda: FakeRebalancingClient(ignore_freshness=args.ignore_freshness)   # noqa: E731
    cases = json.loads(CASES.read_text())
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    tmp = Path(tempfile.mkdtemp(prefix="foundry-rbl-evals-"))
    rows = [run_case(c, profile, client_factory, tmp) for c in cases]
    passed = sum(r["pass"] for r in rows)
    rate = passed / len(rows) if rows else 0.0
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], [0, 0])
        by_kind[r["kind"]][1] += 1
        by_kind[r["kind"]][0] += int(r["pass"])
    total_cost = sum(r["cost_usd"] or 0 for r in rows)
    traps_ok = all(r["pass"] for r in rows if r["kind"] == "trap")     # a trap failing means the agent moved stock on a bad number
    gate = rate >= args.threshold and traps_ok
    summary = {"ran_at": time.strftime("%Y-%m-%d %H:%M:%S"), "live": args.live, "profile": args.profile, "agent": "rebalancing",
               "cases": len(rows), "passed": passed, "pass_rate": round(rate, 3), "by_kind": {k: f"{v[0]}/{v[1]}" for k, v in by_kind.items()},
               "total_cost_usd": round(total_cost, 4), "mean_cost_usd": round(total_cost / len(rows), 5) if rows else 0,
               "threshold": args.threshold, "traps_all_pass": traps_ok, "gate": "PASS" if gate else "FAIL"}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(f"{'id':5} {'kind':8} {'pass':5} {'expected':28} {'got':28} {'turns':5}")
    for r in rows:
        e, g = r["expected"], r["got"]
        es = f"{e['from_dc']}->{e['to_dc']} {e['cases']}" if e["transfer"] else f"no move ({e.get('alternative') or '-'})"
        gs = f"{g['from_dc']}->{g['to_dc']} {g['cases']}" if g["from_dc"] else f"no move ({g.get('alternative') or '-'})"
        print(f"{r['id']:5} {r['kind']:8} {'ok' if r['pass'] else 'FAIL':5} {es:28} {gs:28} {r['turns']:5}")
    print(json.dumps(summary, indent=1))
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
