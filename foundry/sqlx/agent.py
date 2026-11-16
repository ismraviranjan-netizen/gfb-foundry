"""Talk to Data: the question-to-SQL agent on the Foundry loop.

    from foundry.sqlx import agent
    from foundry.sqlx.fake import FakeSqlClient
    r = agent.run("What was OTIF at Chennai for MN-1L in week 12?", store, profile, FakeSqlClient())
    r.answer["text"], r.data["rows"], r.meter["cost_inr"]

Result fields: run_id, answer (what the `answer` tool received, or None), stop_reason, turns,
tool_calls, meter, elapsed_s, error, data (the last result set the agent actually read: sql, columns,
rows, truncated, metric), executed (every statement attempted, with ok/error).
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field

from foundry.levers import Levers
from foundry.loop import run_loop
from foundry.sqlx.prompts import system_prompt
from foundry.sqlx.tools import TOOL_SPECS, Dispatcher

REQUIRED = ("text", "needs_clarification")
FINISH = "answer"


@dataclass
class Result:
    run_id: str
    answer: dict | None
    stop_reason: str
    turns: int
    tool_calls: list[dict] = field(default_factory=list)
    meter: dict = field(default_factory=dict)
    elapsed_s: float = 0.0
    error: str | None = None
    data: dict | None = None
    executed: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def run(question: str, store, profile, client, levers: Levers | None = None, max_turns: int = 8,
        budget_usd: float = 0.25, conn: sqlite3.Connection | None = None, lexical: str = "tokens") -> Result:
    """Answer one question from the tables. Pass `conn` to reuse a warehouse across questions;
    lexical="strict" puts the original word-list guard back in front of run_sql (see guard.py)."""
    levers = levers or Levers()
    disp = Dispatcher(store=store, profile=profile, conn=conn, lexical=lexical)
    run_id = f"sql-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    r = run_loop({"question": question}, system_prompt(profile, disp.schema()), TOOL_SPECS, disp.run, client, levers,
                 finish_tool=FINISH, required=REQUIRED, probe_tool="list_metrics", max_turns=max_turns,
                 budget_usd=budget_usd, run_id=run_id)
    ans = r.final
    if ans is not None:
        ans.pop("proposal_id", None)          # the loop adds it for proposing agents; this one never proposes
    return Result(r.run_id, ans, r.stop_reason, r.turns, r.tool_calls, r.meter, r.elapsed_s, r.error,
                  disp.last, list(disp.executed))
