"""The queue mode on the Foundry loop: the model reads the week's queue and writes the digest.

run(task) with task = {"mode": "digest", "week": 12, "now_hours": 6} returns a Result whose
`digest` is what write_digest received, plus the ids the agent escalated on the way.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from foundry.levers import Levers
from foundry.loop import run_loop
from foundry.profile import Profile
from foundry.store import Store
from foundry.agents.rca_exception.queue_prompts import system_prompt
from foundry.agents.rca_exception.queue_tools import TOOL_SPECS, Dispatcher

REQUIRED = ("week", "now_hours", "headline", "p1_count", "p2_count", "p3_count", "breaches", "money_at_stake_inr", "lines")
FINISH = "write_digest"


@dataclass
class Result:
    run_id: str
    digest: dict | None
    stop_reason: str
    turns: int
    tool_calls: list[dict] = field(default_factory=list)
    meter: dict = field(default_factory=dict)
    elapsed_s: float = 0.0
    error: str | None = None
    escalated: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def run(task: dict, store: Store, profile: Profile, client, levers: Levers | None = None, proposals=None,
        max_turns: int = 12, budget_usd: float = 0.25, folder: str | Path | None = None, queue=None,
        persist: bool = True) -> Result:
    levers = levers or Levers()
    run_id = f"queue-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    disp = Dispatcher(store, profile, now_hours=float(task.get("now_hours", 0.0)), folder=folder, proposals=proposals,
                      queue=queue, persist=persist)
    r = run_loop(task, system_prompt(profile), TOOL_SPECS, disp.run, client, levers, finish_tool=FINISH,
                 required=REQUIRED, probe_tool="list_queue", max_turns=max_turns, budget_usd=budget_usd, run_id=run_id)
    return Result(r.run_id, r.final, r.stop_reason, r.turns, r.tool_calls, r.meter, r.elapsed_s, r.error, list(disp.escalations))
