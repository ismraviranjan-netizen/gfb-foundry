"""The supplier carbon-data agent on the Foundry loop (foundry/loop.py). Read-only and propose-only.

run(task) returns a Result with the submitted review, the proposal ids, the cost and the trace.
task = {"mode": "review", "supplier": "ratnagiri_pulp"}     one supplier's documents
     | {"mode": "sweep"}                                     every supplier: the best document each, one set of asks
The data lead approves a factor update (approve_emission_factor) and the asks (request_supplier_data);
the agent's execute tools (set_emission_factor, send_supplier_email) exist only to be refused.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field

from foundry.approval import ProposalStore
from foundry.carbon.prompts import system_prompt
from foundry.carbon.tools import TOOL_SPECS, Dispatcher
from foundry.levers import Levers
from foundry.loop import run_loop
from foundry.profile import Profile
from foundry.store import Store

REQUIRED = ("mode", "reviewed", "summary")
FINISH = "submit_review"


@dataclass
class Result:
    run_id: str
    review: dict | None
    proposal_ids: list[str]
    stop_reason: str
    turns: int
    tool_calls: list[dict] = field(default_factory=list)
    meter: dict = field(default_factory=dict)
    elapsed_s: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def run(task: dict, store: Store, profile: Profile, client, levers: Levers | None = None,
        proposals: ProposalStore | None = None, max_turns: int | None = None, budget_usd: float = 0.25, folder=None) -> Result:
    levers = levers or Levers()
    proposals = proposals or ProposalStore()
    run_id = f"sca-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    disp = Dispatcher(store, profile, proposals, run_id, week=int(task.get("week", 0) or 0), folder=folder)
    turns = max_turns or (16 if task.get("mode") == "sweep" else 12)
    r = run_loop(task, system_prompt(profile), TOOL_SPECS, disp.run, client, levers, finish_tool=FINISH, required=REQUIRED,
                 probe_tool="list_suppliers", max_turns=turns, budget_usd=budget_usd, run_id=run_id)
    pids = [row["proposal_id"] for row in proposals.all_latest() if row.get("run_id") == run_id]
    return Result(r.run_id, r.final, pids, r.stop_reason, r.turns, r.tool_calls, r.meter, r.elapsed_s, r.error)
