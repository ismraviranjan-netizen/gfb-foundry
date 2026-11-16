# Pilot scorecard (Part 7): four weeks, one DC, the agent proposes, a person approves

| Measure | How it is computed | Pass mark | Week 1 | Week 2 | Week 3 | Week 4 |
|---|---|---|---|---|---|---|
| Root-cause accuracy | agreed primaries / exceptions reviewed on Friday with the planner | ≥ 90% | | | | |
| Trap discipline | quiet weeks and stale snapshots that ended in no action / all such cases | 100% | | | | |
| Time to decision on P1 | approval timestamp − queue timestamp, median | < SLA | | | | |
| Proposals accepted | approved / proposed | ≥ 60% (and rising) | | | | |
| Cost per run | from `ops/ledger.py` | within the business case | | | | |
| p95 latency | from the ledger | < 60 s | | | | |
| Actions executed without a name | from the proposal store | 0 | | | | |
| Drill | a kill-switch drill with a log, once in the pilot | done | | | | |

Sign-off: the planning lead and the data lead initial the sheet on the fourth Friday; the business
case is re-cut from the sheet's numbers before anything is scaled to a second DC.
