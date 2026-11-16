# gfb-foundry

**The Agent Foundry**: build, prove, ship and re-fit supply chain agents on one method. Four agents
and a control tower, one codebase, one company's numbers you can check against a book you own:
Gokul Foods & Beverages (GFB), Nashik — the six sites, ten SKUs and twelve lanes of
*The Planner's Engine*, Appendix F. No real client, no real data.

| Part | Agent | Module | Evals | What it proposes |
|---|---|---|---|---|
| 1 | Root cause & exception management (two modes) | `foundry/agents/rca_exception/` | 30 | a cause with evidence, the money, a priority with an SLA, a playbook with named approvers |
| 2 | Inventory rebalancing | `foundry/agents/rebalancing/` | 20 | a priced transfer between DCs, or the reason none pays |
| 3 | Demand sensing | `foundry/agents/sensing/` | 24 | a short-horizon forecast adjustment, or silence with the back-test that says why |
| 4 | Exception management as a queue | `foundry/agents/rca_exception/queue*.py` | 12 | the week's queue with SLA clocks, reminders, escalations and a digest |
| 5 | The control tower (master agent) | `foundry/tower/` | 14 | one plan per breach from the specialists' findings, one approver per action |
| 6 | Pipeline additions | `foundry/plugins.py`, `plugin_lib/`, `data/sources.py`, `levers_dynamo.py`, `ops/ledger.py` | — | a new cause, a playbook step or an urgency clock without touching the core; data from S3; the panel in DynamoDB; the cost ledger |
| 7 | Onboarding kit | `kit/`, `profiles/` | — | three days from discovery to a passing gate for a new company |
| 8 | Capstone | `capstone/` | — | a sealed company's data pack (Shivalik Dairy Foods) to onboard in three days |

## The method (Part 0)
Discover → Map the data → Configure the profile → Build → Evaluate → Deploy → Operate → Adapt.
Three documents carry it: the one-page canvas (`kit/canvas.md`), the company profile (`profiles/*.yaml`)
and the data-mapping sheet (`kit/data_mapping.csv`). The core never reads a company name: structure
comes from `foundry/network.py` (sites, lanes, SKUs, demand rows), numbers and names from the profile.

## Layout
```
foundry/canon.py                 the GFB tables; tests assert against Appendix F
foundry/network.py               Network: a company's structure as data (GFB default; networks/*.yaml for others)
foundry/store.py                 the six tables (+ promos, + any plug-in table), in memory or as CSVs
foundry/data/generate.py         weekly history from a Network with injected incidents; canonical_store(); sensing_history()
foundry/data/sources.py          canon: | file | s3://  -> Store
foundry/profile.py  profiles/    every company-specific number and name as data: gfb, brewer, pharma, retailer, bakery
foundry/plugins.py  plugin_lib/  Part 6: a cause, a playbook step, an urgency clock, an override, per profile
foundry/loop.py                  the one agent loop: Converse + tools, levers before every call, budget, cap, six stops
foundry/scripted.py              scripted brains (offline evals and tests)
foundry/levers.py  levers_dynamo.py   kill switch · propose-only · per-tool disable; file or DynamoDB
foundry/approval.py              proposals -> human decision -> apply (stub for the TMS/ERP/planning call)
foundry/bedrock_client.py        Claude on Bedrock (Converse API) + meter
foundry/agents/rca_exception/    kpis (monitor) · diagnosis (tree) · triage · tools · prompts · agent · queue*
foundry/agents/rebalancing/      logic (position, lanes, economics) · tools · prompts · agent · fake
foundry/agents/sensing/          logic (series, ES, promo uplift, back-test, rule) · tools · prompts · agent · fake
foundry/tower/                   routing (who to call, how to merge) · tools · prompts · agent · fake
foundry/cli.py                   every lab's command
evals/                           five labelled sets and their runners (each a CI gate), pass^k
tests/                           the test suite
deploy/                          Lambda handler (one function, six routes) + Terraform (API, DynamoDB x2, S3, alarm)
.github/workflows/               evals-gate on every push; deploy behind a human approval
ops/ledger.py                    cost per run, p95 latency, stop reasons, from runs/runs.jsonl
kit/                             canvas, discovery, mapping, onboarding, known lies, pilot plan/scorecard, business case, demo
capstone/                        the sealed company's data pack (no answers in here)
```

## Run it
```
pip install pyyaml pytest                              # boto3 only for --live and the deploy
make test                                              # the whole suite
make evals                                             # all five eval sets, each gated at 90%
python3 -m foundry.cli monitor --week 12
python3 -m foundry.cli diagnose --dc CHN --sku MN-1L --week 12            # Part 1 (scripted brain)
python3 -m foundry.cli rebalance --sku MN-1L --week 1 --position           # Part 2 (F.11: 3,720 cases KOL->CHN)
python3 -m foundry.cli sense --dc CHN --sku MN-1L --origin-week 16         # Part 3 (on the sensing history)
python3 -m foundry.cli queue --week 12 --now-hours 5 --tick                # Part 4
python3 -m foundry.cli plan --dc CHN --sku MN-1L --week 12                 # Part 5 (the tower)
python3 -m foundry.cli plan --dc CHN --sku MN-1L --week 1 --position       # the Sarah flow with a transfer that pays
python3 -m foundry.cli --profile brewer plan --dc CHN --sku MN-1L --week 1 --position   # same code, another company
python3 -m foundry.cli proposals · approve PRP-... --by "Priya Nambiar" · apply PRP-... · levers kill|reset
python3 -m foundry.cli ledger
```
Add `--live` to any agent command for Claude on Bedrock (`aws login --region ap-south-1`;
model `global.anthropic.claude-sonnet-4-6` unless `FOUNDRY_MODEL` says otherwise). `--data s3://bucket/prefix`
or a folder of CSVs (+ `network.yaml`) runs the same agents on another company's tables.

## The Control Room (the three agents as one tower)

```bash
python3 -m foundry.cli radar --week 12                 # the 14-day risk radar: RED / AMBER / GREEN / GREY, LONG senders
python3 -m foundry.cli story week12 --messages         # Monday 06:30 to Tuesday 15:20: refresh, refusals, escalation, a strike
python3 -m foundry.cli story f11_monday                # F.11 through the tower; the transfer's truck breaks down on Wednesday
python3 -m foundry.cli story one_sender                # two DCs, one sender: the reconciler adds the joint co-sign
python3 -m foundry.cli ask "Why is the tower moving stock into Chennai?" --story week12
python3 -m foundry.console.server --storyline blank    # the console on http://localhost:8080 (press Run Monday)
python3 -m foundry.console.snapshot                    # out/console_demo.html: the replay, no server needed
python3 evals/run_control_room_evals.py                # 26 storyline checks + 12 chat cases
```

`foundry/risk` projects every DC x SKU fourteen days ahead from one shared arrival schedule (the "one clock" the
rebalancing agent now prices on). `foundry/orchestrator` is the event bus and the runtime: watcher, radar,
orchestrator, listener, reconciler, notifier, coworker, approvals and SLA, with idempotency keys and a journal.
`foundry/notify` writes one message per person per batch; `foundry/chat` answers questions read-only and hands
data questions to `foundry/sqlx`; `foundry/console` is the API and the one-file page. On AWS (`deploy/tower.tf`):
a second Lambda with reserved concurrency 1 (state in S3), the Monday cron `cron(0 1 ? * MON *)`, a custom
event bus `gfb-tower` for carrier and data feeds, an SNS topic for approval emails.

## Deploy
`make layer && cd deploy && terraform init && terraform apply -var fake=1` for the scripted smoke test,
then `-var fake=0`. Routes: `POST /diagnose /sweep /rebalance /sense /queue /plan`, `GET /health`.
`.github/workflows/deploy.yml` runs every eval set and then applies only after a reviewer approves the
`production` environment.

## Laws (the same five as the control tower)
Maths decides, the model explains and chooses · every agent has a budget, an exit and a human ·
tools are doors; the agent gets only its own keys · no eval, no deploy · watch cost, latency, correctness.
