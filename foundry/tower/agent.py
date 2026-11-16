"""The tower on the Foundry loop. task = {"mode": "breach", "dc", "sku", "week", "kpi"} | {"mode": "sweep", "week"}."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field

from foundry.approval import ProposalStore
from foundry.levers import Levers
from foundry.loop import run_loop
from foundry.profile import Profile
from foundry.scripted import ScriptedClient
from foundry.store import Store
from foundry.tower.prompts import system_prompt
from foundry.tower.tools import TOOL_SPECS, Dispatcher

REQUIRED = ("dc", "week", "kpi", "root_cause", "priority", "specialists_called", "plan", "approvals", "total_impact_inr", "summary")
FINISH = "submit_plan"


@dataclass
class Result:
    run_id: str
    plan: dict | None
    proposal_id: str | None
    stop_reason: str
    turns: int
    tool_calls: list[dict] = field(default_factory=list)
    meter: dict = field(default_factory=dict)            # the master's own calls
    specialists: dict = field(default_factory=dict)      # each specialist's meter
    total_meter: dict = field(default_factory=dict)      # master + specialists
    elapsed_s: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def default_specialists(master_client):
    """Offline, each specialist gets its own scripted brain; live, they share the one Bedrock client."""
    if isinstance(master_client, ScriptedClient):
        from foundry.bedrock_client import FakeBedrockClient
        from foundry.agents.rebalancing.fake import FakeRebalancingClient
        from foundry.agents.sensing.fake import FakeSensingClient
        makers = {"rca": FakeBedrockClient, "rebalancing": FakeRebalancingClient, "sensing": FakeSensingClient}
        return lambda name: makers[name]()
    return lambda name: master_client


def run(task: dict, store: Store, profile: Profile, client, levers: Levers | None = None, proposals: ProposalStore | None = None,
        max_turns: int = 10, budget_usd: float = 0.60, specialist_client=None) -> Result:
    levers = levers or Levers()
    proposals = proposals or ProposalStore()
    run_id = f"tower-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    disp = Dispatcher(store, profile, proposals, levers, run_id, specialist_client or default_specialists(client), budget_usd=budget_usd / 2)
    disp.disabled = [n for n in ("rebalancing", "sensing") if not levers.decide(f"call_{n}").allowed]
    r = run_loop(task, system_prompt(profile), TOOL_SPECS, disp.run, client, levers, finish_tool=FINISH, required=REQUIRED,
                 probe_tool="call_rca", max_turns=max_turns, budget_usd=budget_usd, run_id=run_id)
    total = disp.total_meter()
    for k in ("calls", "input_tokens", "output_tokens", "cost_usd", "cost_inr"):
        total[k] = round(total.get(k, 0) + r.meter.get(k, 0), 4) if isinstance(total.get(k, 0), float) or isinstance(r.meter.get(k, 0), float) else total.get(k, 0) + r.meter.get(k, 0)
    return Result(r.run_id, r.final, r.proposal_id, r.stop_reason, r.turns, r.tool_calls, r.meter, disp.meters, total, r.elapsed_s, r.error)
