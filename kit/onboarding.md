# Onboarding a company in three days (Part 7)

The method's steps 1–3 and 5–7, run against a real company's data, in the order that keeps the
third day from becoming the eleventh week. Each half-day ends with a file in the repo.

| Half-day | With whom | You produce | Done when |
|---|---|---|---|
| D1 am | planning lead (60 min), then alone | `kit/canvas.md` filled for the first agent; `kit/discovery.md` answered | 12 boxes scored 18+ by you; every threshold question has a number |
| D1 pm | data lead | `kit/data_mapping.csv` with source system, field, unit, refresh and the known lie for every row | no empty cell in the three question columns; the stale-snapshot column has an answer |
| D2 am | alone | `profiles/<company>.yaml` from the discovery answers; `networks/<company>.yaml` from the site list and lane sheet | `python3 -m foundry.cli --profile <company> --data <folder> monitor --week <last>` runs and the list is readable |
| D2 pm | data lead on call | the five CSVs exported into the Foundry's columns (plus any table a plug-in needs) | the monitor's deviations match the planner's own list for the last closed week, give or take two |
| D3 am | alone | the diagnosis on the three exceptions the planner named in discovery; a plug-in if a cause is missing (Part 6) | three primaries the planner agrees with, or a written reason why not |
| D3 pm | planning lead, data lead | `evals/<company>_cases.json` (8 clear, 4 ambiguous, 3 traps from their own weeks); `kit/pilot_plan.md`; `kit/business_case.md`; the seven-minute demo on their numbers | the gate passes; the pilot has a DC, a start date, an approver and a pass mark |

## The rules that keep three days three days
1. The company's data goes into the five tables, never the agents into the company's schema.
2. A threshold the client cannot name on day one is a line in the profile with a comment, not a meeting.
3. A cause the tree does not have is a plug-in on day three, not a change to the tree.
4. Nothing live before the gate: the pilot runs behind `propose_only: true` whatever the client says.
5. Every number in the business case comes from `estimate_impact` on their own weeks.
