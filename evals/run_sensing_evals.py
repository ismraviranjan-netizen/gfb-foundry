"""Run the sensing eval set. Offline by default (FakeSensingClient); --live uses Bedrock.

Pass = decision matches; where the case pins them, the sign of the largest adjustment and the
co-sign flag match too. Exits non-zero below the threshold: a CI gate.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from foundry.data.generate import Injection, Promo, Signals, build, sensing_history   # noqa: E402
from foundry.profile import load_profile                                             # noqa: E402
from foundry.levers import Levers                                                    # noqa: E402
from foundry.approval import ProposalStore                                           # noqa: E402
from foundry.agents.sensing import agent                                             # noqa: E402
from foundry.agents.sensing.fake import FakeSensingClient                            # noqa: E402

CASES = Path(__file__).resolve().parent / "sensing_cases.json"


def history_store(spec):
    if spec == "sensing":
        return sensing_history()
    trend = {tuple(k.split(":")): float(v) for k, v in spec.get("trend", {}).items()}
    season = {tuple(k.split(":")): tuple(v) for k, v in spec.get("season", {}).items()}
    promos = [Promo(int(w), dc, sku, name, float(u)) for w, dc, sku, name, u in spec.get("promos", [])]
    inj = [Injection(kind, int(w), dc, sku, dict(params)) for kind, w, dc, sku, params in spec.get("injections", [])]
    return build(inj, seed=int(spec["seed"]), weeks=int(spec.get("weeks", 20)), signals=Signals(trend=trend, season=season), promos=promos)


def run_case(c: dict, profile, client_factory, tmp: Path) -> dict:
    store = history_store(c["history"])
    levers = Levers(tmp / "panel.json", tmp / "audit.jsonl")
    props = ProposalStore(tmp / f"proposals-{c['id']}.jsonl")
    t0 = time.time()
    res = agent.run(dict(c["task"]), store, profile, client_factory(), levers, props)
    s = res.sensing or {}
    exp = c["expect"]
    adj = s.get("adjustment_pct") or []
    biggest = max(adj, key=abs) if adj else 0.0
    ok = res.stop_reason == "submitted" and s.get("decision") == exp["decision"]
    if ok and exp["decision"] == "adjust":
        ok = res.proposal_id is not None
        if ok and "sign" in exp:
            ok = (biggest > 0) == (exp["sign"] == "+")
        if ok and "co_sign" in exp:
            row = props.get(res.proposal_id) or {}
            has = any(a["code"] == "co_sign_forecast" for a in row.get("actions", []))
            ok = has == bool(exp["co_sign"])
    if ok and exp["decision"] == "silent":
        ok = res.proposal_id is None
    return {"id": c["id"], "kind": c["kind"], "pass": bool(ok), "expected": exp,
            "got": {"decision": s.get("decision"), "adjustment_pct": adj, "confidence": s.get("confidence"), "proposal_id": res.proposal_id,
                    "reason": (s.get("reasons") or [""])[0]},
            "stop_reason": res.stop_reason, "turns": res.turns, "cost_usd": res.meter.get("cost_usd"), "elapsed_s": round(time.time() - t0, 2),
            "note": c.get("note", "")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use Claude on Bedrock instead of the scripted fake")
    ap.add_argument("--profile", default="gfb")
    ap.add_argument("--threshold", type=float, default=0.90)
    ap.add_argument("--only", default=None, help="comma-separated case ids")
    ap.add_argument("--out", default="evals/sensing_results.json")
    ap.add_argument("--chase-spikes", action="store_true", help="the fake that proposes whatever it sees (to show the traps work)")
    args = ap.parse_args(argv)
    profile = load_profile(args.profile)
    if args.live:
        from foundry.bedrock_client import BedrockClient
        client_factory = lambda: BedrockClient()      # noqa: E731
    else:
        client_factory = lambda: FakeSensingClient(chase_spikes=args.chase_spikes)   # noqa: E731
    cases = json.loads(CASES.read_text())
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    tmp = Path(tempfile.mkdtemp(prefix="foundry-sensing-evals-"))
    rows = [run_case(c, profile, client_factory, tmp) for c in cases]
    passed = sum(r["pass"] for r in rows)
    rate = passed / len(rows) if rows else 0.0
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], [0, 0])
        by_kind[r["kind"]][1] += 1
        by_kind[r["kind"]][0] += int(r["pass"])
    summary = {"agent": "sensing", "profile": args.profile, "live": args.live, "cases": len(rows), "passed": passed, "pass_rate": round(rate, 3),
               "by_kind": {k: f"{v[0]}/{v[1]}" for k, v in by_kind.items()}, "threshold": args.threshold,
               "gate": "PASS" if rate >= args.threshold and by_kind.get("trap", [0, 0])[0] == by_kind.get("trap", [0, 0])[1] else "FAIL",
               "cost_usd": round(sum(r["cost_usd"] or 0 for r in rows), 4)}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    for r in rows:
        print(f"{'PASS' if r['pass'] else 'FAIL'} {r['id']:4} {r['kind']:7} expected {r['expected']} got {r['got']['decision']} {r['got']['adjustment_pct']} | {r['note'][:70]}")
    print(json.dumps(summary, indent=1))
    return 0 if summary["gate"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
