"""The one agent loop every Foundry agent runs on: Converse with tools, levers before every call,
a meter after every call, a budget, a turn cap, and six named ways to stop.

An agent module supplies four things: a system prompt, its tool specs, a dispatcher (name, input
-> output) and the name of its finish tool. Everything else -- the kill switch, propose-only,
per-tool disable, the budget, the trace, the cost in rupees -- is here and is tested once.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from dataclasses import asdict, dataclass, field
from typing import Callable

from foundry.bedrock_client import Meter
from foundry.levers import Levers

STOP_REASONS = ("submitted", "killed", "budget", "model_error", "no_tool_call", "max_turns")


@dataclass
class LoopResult:
    run_id: str
    final: dict | None             # what the finish tool received, or None
    proposal_id: str | None
    stop_reason: str
    turns: int
    tool_calls: list[dict] = field(default_factory=list)
    meter: dict = field(default_factory=dict)
    elapsed_s: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def tool_result_block(tool_use_id: str, content: dict) -> dict:
    return {"toolResult": {"toolUseId": tool_use_id, "content": [{"json": content}]}}


def run_loop(task: dict, system: str, tool_specs: list[dict], dispatch: Callable[[str, dict], dict], client,
             levers: Levers | None = None, *, finish_tool: str, required: tuple[str, ...] = (),
             probe_tool: str | None = None, max_turns: int = 8, budget_usd: float = 0.25,
             run_prefix: str = "run", run_id: str | None = None) -> LoopResult:
    t0 = time.time()
    run_id = run_id or f"{run_prefix}-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    levers = levers or Levers()
    probe = probe_tool or next((t["toolSpec"]["name"] for t in tool_specs), finish_tool)
    meter = Meter()
    messages = [{"role": "user", "content": [{"text": "TASK " + json.dumps(task)}]}]
    trace: list[dict] = []
    final = None
    proposal_id = None
    stop = "max_turns"

    for turn in range(1, max_turns + 1):
        if levers.decide(probe).reason.startswith("kill switch"):      # read fresh from disk at the top of every turn
            stop = "killed"
            break
        if meter.cost_usd() > budget_usd:
            stop = "budget"
            break
        try:
            resp = client.converse(system, messages, tool_specs)
        except Exception as e:  # the model or the network failed: stop cleanly, keep the trace
            res = LoopResult(run_id, None, proposal_id, "model_error", turn - 1, trace, meter.as_dict(),
                             round(time.time() - t0, 2), str(e))
            log_run(res, task)
            return res
        meter.add(resp.get("usage", {}))
        msg = resp["output"]["message"]
        messages.append(msg)
        uses = [c["toolUse"] for c in msg["content"] if "toolUse" in c]
        if not uses:
            stop = "no_tool_call"          # prose is not a decision; the planner needs the structure
            break
        results = []
        done = False
        for u in uses:
            name, inp = u["name"], u["input"]
            d = levers.decide(name)        # and again before every tool call
            if not d.allowed:
                trace.append({"turn": turn, "tool": name, "allowed": False, "reason": d.reason})
                if d.reason.startswith("kill switch"):
                    done, stop = True, "killed"
                    break
                results.append(tool_result_block(u["toolUseId"], {"error": d.reason}))
                continue
            if name == finish_tool:
                missing = [k for k in required if k not in inp]
                if missing:
                    trace.append({"turn": turn, "tool": name, "allowed": True, "rejected": f"missing fields {missing}"})
                    results.append(tool_result_block(u["toolUseId"], {"error": f"missing fields {missing}"}))
                    continue
                final = dict(inp)
                final.setdefault("proposal_id", proposal_id)
                trace.append({"turn": turn, "tool": name, "allowed": True, "input": {k: inp[k] for k in list(inp)[:6]}})
                done, stop = True, "submitted"
                break
            try:
                out = dispatch(name, inp)
            except Exception as e:     # a bad input must not crash the loop or the tower above it (The Agent Desk, 8 Nov)
                out = {"error": f"tool {name} failed: {type(e).__name__}: {e}"}
            trace.append({"turn": turn, "tool": name, "allowed": True, "input": inp, "keys": sorted(out.keys())[:8]})
            if isinstance(out, dict) and out.get("proposal_id"):
                proposal_id = out["proposal_id"]
            results.append(tool_result_block(u["toolUseId"], out))
        if done:
            break
        messages.append({"role": "user", "content": results})

    res = LoopResult(run_id, final, proposal_id, stop, len(trace), trace, meter.as_dict(), round(time.time() - t0, 2))
    log_run(res, task)
    return res


def log_run(res: LoopResult, task: dict | None = None) -> None:
    """One line per run in runs/runs.jsonl (FOUNDRY_RUNS): what the cost ledger reads."""
    path = Path(os.environ.get("FOUNDRY_RUNS", "runs/runs.jsonl"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps({"ts": time.time(), "run_id": res.run_id, "agent": res.run_id.split("-")[0],
                                "stop_reason": res.stop_reason, "turns": res.turns, "meter": res.meter,
                                "elapsed_s": res.elapsed_s, "task": task or {}}) + "\n")
    except Exception:
        pass
