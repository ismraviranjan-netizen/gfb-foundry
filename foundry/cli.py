"""foundry: the command line the labs use.

  python3 -m foundry.cli generate [folder]                     write the incident weeks as CSVs
  python3 -m foundry.cli monitor --week 12                     the deviations the monitor raises
  python3 -m foundry.cli diagnose --dc CHN --sku MN-1L --week 12 [--kpi OTIF] [--live]
  python3 -m foundry.cli sweep --week 12 [--live]
  python3 -m foundry.cli rebalance --sku MN-1L --week 1 --position [--to-dc CHN] [--live]     Part 2 (F.11 on the opening position)
  python3 -m foundry.cli rebalance --sku MN-1L --week 12 [--refreshed] [--live]               Part 2 (the incident week)
  python3 -m foundry.cli rebalance --sweep --week 12
  python3 -m foundry.cli sense --dc CHN --sku MN-1L --origin-week 16 [--horizon 2] [--live]   Part 3 (on the sensing history)
  python3 -m foundry.cli sense --sweep --origin-week 16
  python3 -m foundry.cli queue --week 12 [--now-hours 5] [--tick] [--digest] [--live]        Part 4
  python3 -m foundry.cli plan --dc CHN --sku MN-1L --week 12 [--kpi OTIF] [--position] [--refreshed] [--live]   Part 5 (the tower)
  python3 -m foundry.cli plan --sweep --week 12
  python3 -m foundry.cli refresh --dc KOL --sku MN-1L --week 12   what Rohan's refresh does to the snapshot
  python3 -m foundry.cli proposals                             what is waiting for a human
  python3 -m foundry.cli approve PRP-... --by "Priya Nambiar" [--reject]
  python3 -m foundry.cli apply PRP-...
  python3 -m foundry.cli levers [status|kill|reset|propose-only on|off|disable TOOL|enable TOOL]
  python3 -m foundry.cli ledger                                cost per run, p95 latency, stop reasons

Global options: --profile gfb|brewer|pharma|retailer|bakery, --data <folder|s3://...|canon:|canon:sensing>, --seed 7
"""
from __future__ import annotations

import argparse
import json
import sys

from foundry.data.generate import canonical_store, refresh_snapshot, sensing_history
from foundry.data.sources import load_store
from foundry.store import Store
from foundry.profile import load_profile
from foundry.levers import Levers
from foundry.approval import ProposalStore
from foundry.scripted import ScriptedClient


def _store(args, default: str = "canon:") -> Store:
    uri = getattr(args, "data", None) or default
    if getattr(args, "position", False):
        from pathlib import Path
        from foundry.network import Network
        y = Path(uri.replace("file://", "")) / "network.yaml" if not uri.startswith(("canon:", "s3://")) else None
        return Store.from_position(net=Network.from_yaml(y) if y and y.exists() else None)
    if uri == "canon:":
        return canonical_store(seed=args.seed, refreshed=getattr(args, "refreshed", False))
    if uri == "canon:sensing":
        return sensing_history()
    return load_store(uri)


def _client(args, fake_cls):
    if getattr(args, "live", False):
        from foundry.bedrock_client import BedrockClient
        return BedrockClient()
    return fake_cls()


def _print(res, key: str) -> int:
    out = {"run_id": res.run_id, "stop": res.stop_reason, "turns": res.turns, "meter": res.meter, key: getattr(res, key), "error": res.error}
    if hasattr(res, "total_meter"):
        out["specialists"] = res.specialists
        out["total_meter"] = res.total_meter
    print(json.dumps(out, indent=1, default=str))
    return 0 if res.stop_reason == "submitted" else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="foundry")
    ap.add_argument("--profile", default="gfb")
    ap.add_argument("--data", default=None, help="folder of CSVs, s3://bucket/prefix, canon: (default) or canon:sensing")
    ap.add_argument("--seed", type=int, default=7)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate"); g.add_argument("folder", nargs="?", default="data/out"); g.add_argument("--sensing", action="store_true")
    m = sub.add_parser("monitor"); m.add_argument("--week", type=int, default=12)
    d = sub.add_parser("diagnose"); d.add_argument("--dc", required=True); d.add_argument("--sku", default=None); d.add_argument("--week", type=int, default=12); d.add_argument("--kpi", default="OTIF"); d.add_argument("--live", action="store_true")
    s = sub.add_parser("sweep"); s.add_argument("--week", type=int, default=12); s.add_argument("--live", action="store_true")
    rb = sub.add_parser("rebalance"); rb.add_argument("--sku", default="MN-1L"); rb.add_argument("--week", type=int, default=12); rb.add_argument("--to-dc", default=None)
    rb.add_argument("--position", action="store_true", help="the opening position (F.5), week 1"); rb.add_argument("--refreshed", action="store_true"); rb.add_argument("--sweep", action="store_true"); rb.add_argument("--live", action="store_true")
    se = sub.add_parser("sense"); se.add_argument("--dc", default="CHN"); se.add_argument("--sku", default="MN-1L"); se.add_argument("--origin-week", type=int, default=16); se.add_argument("--horizon", type=int, default=2)
    se.add_argument("--sweep", action="store_true"); se.add_argument("--live", action="store_true")
    q = sub.add_parser("queue"); q.add_argument("--week", type=int, default=12); q.add_argument("--now-hours", type=float, default=0.0); q.add_argument("--tick", action="store_true"); q.add_argument("--digest", action="store_true"); q.add_argument("--live", action="store_true")
    pl = sub.add_parser("plan"); pl.add_argument("--dc", default="CHN"); pl.add_argument("--sku", default="MN-1L"); pl.add_argument("--week", type=int, default=12); pl.add_argument("--kpi", default="OTIF")
    pl.add_argument("--position", action="store_true"); pl.add_argument("--refreshed", action="store_true"); pl.add_argument("--sweep", action="store_true"); pl.add_argument("--live", action="store_true")
    rf = sub.add_parser("refresh"); rf.add_argument("--dc", required=True); rf.add_argument("--sku", required=True); rf.add_argument("--week", type=int, default=12)
    sub.add_parser("proposals")
    a = sub.add_parser("approve"); a.add_argument("proposal_id"); a.add_argument("--by", required=True); a.add_argument("--reject", action="store_true"); a.add_argument("--note", default="")
    ap2 = sub.add_parser("apply"); ap2.add_argument("proposal_id")
    lv = sub.add_parser("levers"); lv.add_argument("action", nargs="*", default=["status"])
    sub.add_parser("ledger")
    # The Control Room
    rd = sub.add_parser("radar"); rd.add_argument("--week", type=int, default=12); rd.add_argument("--position", action="store_true", help="the opening position (week 1, F.5)"); rd.add_argument("--refreshed", action="store_true")
    st = sub.add_parser("story"); st.add_argument("name", choices=["f11_monday", "week12", "one_sender"]); st.add_argument("--folder", default="runs/story"); st.add_argument("--messages", action="store_true")
    ak = sub.add_parser("ask"); ak.add_argument("question"); ak.add_argument("--story", default="week12", choices=["f11_monday", "week12", "one_sender"])
    args = ap.parse_args(argv)

    profile = load_profile(args.profile)
    if args.cmd == "generate":
        st = sensing_history() if args.sensing else _store(args)
        st.to_csv(args.folder); print("written", args.folder); return 0
    if args.cmd == "monitor":
        from foundry.agents.rca_exception import kpis
        st = _store(args)
        for dv in kpis.deviations(st, profile, args.week):
            print(f"{dv.key:28} value {dv.value:<8} baseline {dv.baseline_mean:<8} z {dv.z:<7} {dv.reason}")
        return 0
    if args.cmd in ("diagnose", "sweep"):
        from foundry.agents.rca_exception import agent
        from foundry.bedrock_client import FakeBedrockClient
        st = _store(args)
        task = {"mode": "sweep", "week": args.week} if args.cmd == "sweep" else {"mode": "diagnose", "dc": args.dc, "sku": args.sku, "week": args.week, "kpi": args.kpi}
        return _print(agent.run(task, st, profile, _client(args, FakeBedrockClient), Levers(), ProposalStore()), "diagnosis")
    if args.cmd == "rebalance":
        from foundry.agents.rebalancing import agent
        from foundry.agents.rebalancing.fake import FakeRebalancingClient
        st = _store(args)
        week = 1 if args.position else args.week
        task = {"mode": "sweep", "week": week} if args.sweep else {"mode": "transfer", "sku": args.sku, "week": week, "to_dc": args.to_dc}
        return _print(agent.run(task, st, profile, _client(args, FakeRebalancingClient), Levers(), ProposalStore()), "plan")
    if args.cmd == "sense":
        from foundry.agents.sensing import agent
        from foundry.agents.sensing.fake import FakeSensingClient
        st = _store(args, default="canon:sensing")
        task = {"mode": "sweep", "origin_week": args.origin_week, "horizon": args.horizon} if args.sweep else \
               {"mode": "sense", "dc": args.dc, "sku": args.sku, "origin_week": args.origin_week, "horizon": args.horizon}
        return _print(agent.run(task, st, profile, _client(args, FakeSensingClient), Levers(), ProposalStore()), "sensing")
    if args.cmd == "queue":
        from foundry.agents.rca_exception import queue as Q
        st = _store(args)
        qu = Q.load_or_build(st, profile, args.week, proposals=ProposalStore())
        if args.tick:
            print(json.dumps(Q.tick(qu, profile, args.now_hours), indent=1, default=str))
            Q.to_json(qu, Q.queue_path(args.week))
        if args.digest:
            from foundry.agents.rca_exception import queue_agent
            from foundry.agents.rca_exception.queue_fake import FakeQueueClient
            res = queue_agent.run({"mode": "digest", "week": args.week, "now_hours": args.now_hours}, st, profile, _client(args, FakeQueueClient), Levers(), queue=qu)
            return _print(res, "digest")
        for row in qu.table(args.now_hours):
            print(json.dumps(row, default=str))
        print(json.dumps(Q.digest(qu, args.now_hours), indent=1, default=str))
        return 0
    if args.cmd == "plan":
        from foundry.tower import agent
        from foundry.tower.fake import FakeTowerClient
        st = _store(args)
        week = 1 if args.position else args.week
        task = {"mode": "sweep", "week": week} if args.sweep else {"mode": "breach", "dc": args.dc, "sku": args.sku, "week": week, "kpi": args.kpi}
        return _print(agent.run(task, st, profile, _client(args, FakeTowerClient), Levers(), ProposalStore()), "plan")
    if args.cmd == "refresh":
        st = _store(args)
        print(json.dumps(refresh_snapshot(st, args.week, args.dc, args.sku), indent=1)); return 0
    if args.cmd == "ledger":
        from ops.ledger import summarise
        print(json.dumps(summarise(), indent=1)); return 0
    ps = ProposalStore()
    if args.cmd == "proposals":
        for p in ps.pending():
            print(p["proposal_id"], p.get("kind", "rca"), p["dc"], p["sku"], p["root_cause"], "->", [(a["code"], a.get("approver")) for a in p["actions"]])
        return 0
    if args.cmd == "approve":
        row = ps.decide(args.proposal_id, args.by, "REJECTED" if args.reject else "APPROVED", args.note); print(row["status"]); return 0
    if args.cmd == "apply":
        row = ps.apply(args.proposal_id); print(json.dumps(row["sent"], indent=1)); return 0
    if args.cmd == "radar":
        from foundry.risk import projection as risk
        from foundry.store import Store as _S
        st_ = _S.from_position() if args.position else _store(args)
        wk = 1 if args.position else args.week
        r = risk.radar(st_, profile, wk)
        print(f"week {wk}: {r['counts']}  money at risk (RED) INR {r['money_at_risk']:,}  pre-empt {[(x['dc'], x['sku']) for x in r['pre_empt']]}")
        for row in r["rows"]:
            if row["risk"] != "GREEN" or row["long"]:
                print(f"  {row['risk']:<5} {row['dc']} {row['sku']:<7} dos {row['dos0']:>5}  {'LONG ' if row['long'] else ''}{row['reason']}")
        return 0
    if args.cmd == "story":
        import shutil as _sh
        from foundry.orchestrator.scenarios import STORYLINES
        _sh.rmtree(args.folder, ignore_errors=True)
        title, fn = STORYLINES[args.name]
        rt, log = fn(args.folder)
        print(f"# {title}")
        for s in log:
            print(f"\n[{s['clock']}] {s['label']}" + (f"  ({s['said']})" if s["said"] else "") + ("" if s["accepted"] else "  -> duplicate key, ignored"))
            for e in s["events"]:
                for n in e.get("notes") or []:
                    print(f"   {e['id']} {e['type']:<18} {n}")
            if args.messages:
                for m in s["messages"]:
                    print(f"   MSG to {m['to']}: {m['subject']}")
        print(f"\nplans: {rt.state()['counts']}  tower runs {len(rt.runs)}  model cost INR {rt.cost_inr()}")
        return 0
    if args.cmd == "ask":
        import tempfile as _tf
        from foundry.chat import agent as chat
        from foundry.orchestrator.scenarios import STORYLINES
        rt, _ = STORYLINES[args.story][1](_tf.mkdtemp(prefix="ask-"))
        r = chat.ask(args.question, rt)
        print((r.answer or {}).get("text", r.stop_reason))
        print(f"  tools: {' -> '.join(c.get('tool') for c in r.tool_calls)}  cited: {(r.answer or {}).get('cited')}  INR {r.meter.get('cost_inr')}")
        return 0
    if args.cmd == "levers":
        lv = Levers(); act = args.action
        if act[0] == "status":
            print(json.dumps(lv.read_panel(), indent=1))
        elif act[0] == "kill":
            print(lv.set(kill_switch=True))
        elif act[0] == "reset":
            print(lv.set(kill_switch=False, propose_only=True, disabled_tools=[]))
        elif act[0] == "propose-only":
            print(lv.set(propose_only=(act[1] == "on")))
        elif act[0] in ("disable", "enable"):
            panel = lv.read_panel(); dt = set(panel["disabled_tools"]); (dt.add if act[0] == "disable" else dt.discard)(act[1]); print(lv.set(disabled_tools=sorted(dt)))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
