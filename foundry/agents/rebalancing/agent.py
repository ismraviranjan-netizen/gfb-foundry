"""The inventory rebalancing agent on the Foundry loop (foundry/loop.py).

run(task) returns a Result with the submitted plan, the proposal id, the cost and the trace.
task = {"mode": "transfer", "sku": "MN-1L", "week": 1, "to_dc": "CHN" (optional)}
     | {"mode": "sweep", "week": 12}   (every A/B SKU; the worst short DC with a fresh long partner)
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field

from foundry.approval import ProposalStore
from foundry.levers import Levers
from foundry.loop import run_loop
from foundry.profile import Profile
from foundry.store import Store
from foundry.agents.rebalancing.prompts import system_prompt
from foundry.agents.rebalancing.tools import TOOL_SPECS, Dispatcher

REQUIRED = ("to_dc", "sku", "week", "cases", "pays", "summary")
FINISH = "submit_plan"


@dataclass
class Result:
    run_id: str
    plan: dict | None
    proposal_id: str | None
    stop_reason: str
    turns: int
    tool_calls: list[dict] = field(default_factory=list)
    meter: dict = field(default_factory=dict)
    elapsed_s: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def run(task: dict, store: Store, profile: Profile, client, levers: Levers | None = None,
        proposals: ProposalStore | None = None, max_turns: int = 8, budget_usd: float = 0.25) -> Result:
    levers = levers or Levers()
    proposals = proposals or ProposalStore()
    run_id = f"rbl-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    disp = Dispatcher(store, profile, proposals, run_id)
    r = run_loop(task, system_prompt(profile), TOOL_SPECS, disp.run, client, levers, finish_tool=FINISH,
                 required=REQUIRED, probe_tool="stock_position", max_turns=max_turns, budget_usd=budget_usd,
                 run_id=run_id)
    return Result(r.run_id, r.final, r.proposal_id, r.stop_reason, r.turns, r.tool_calls, r.meter, r.elapsed_s, r.error)
