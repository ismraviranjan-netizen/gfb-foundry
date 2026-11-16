"""Ask the tower, on the Foundry loop.

    from foundry.chat import agent
    r = agent.ask("Why is Chennai waiting?", runtime)          # scripted brain offline
    r.answer["text"], r.answer["cited"], r.meter["cost_inr"]
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from foundry.chat.fake import FakeChatClient
from foundry.chat.prompts import system_prompt
from foundry.chat.tools import TOOL_SPECS, Dispatcher
from foundry.loop import run_loop


@dataclass
class Result:
    run_id: str
    answer: dict | None
    stop_reason: str
    turns: int
    tool_calls: list[dict] = field(default_factory=list)
    meter: dict = field(default_factory=dict)
    sql_meter: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def ask(question: str, runtime, client=None, max_turns: int = 6, budget_usd: float = 0.15, sql_client_factory=None) -> Result:
    disp = Dispatcher(runtime, sql_client_factory)
    r = run_loop({"question": question}, system_prompt(runtime.profile.company), TOOL_SPECS, disp.run, client or FakeChatClient.for_network(runtime.store.net),
                 runtime.levers, finish_tool="answer", required=("text", "cited"), probe_tool="tower_state",
                 max_turns=max_turns, budget_usd=budget_usd, run_prefix="chat")
    ans = r.final
    if ans is not None:
        ans.pop("proposal_id", None)
    return Result(r.run_id, ans, r.stop_reason, r.turns, r.tool_calls, r.meter, disp.sql_meter)
