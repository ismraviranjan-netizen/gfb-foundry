"""Scripted brains: fake Converse clients that call an agent's tools in the method's order and
submit exactly what the tools said. Tests and offline evals run on them; they are deliberately not
clever, so that when the maths is wrong the eval is wrong, which is the point of evals.

Every fake reads the task from the first user message ("TASK {json}") and the tool results so far
from the conversation, and answers with one tool call per turn.
"""
from __future__ import annotations

import json


class ScriptedClient:
    model = "fake"

    def __init__(self, fail_at_turn: int | None = None):
        self.fail_at_turn = fail_at_turn
        self.turn = 0

    # --- what a subclass implements ---------------------------------------------------------------
    def next_call(self, task: dict, results: list[dict], seen: list[str]) -> tuple[str, dict]:
        """Return (tool name, input) for this turn. results = [{"name", "content"}] so far."""
        raise NotImplementedError

    # --- the Converse shape ---------------------------------------------------------------------
    def converse(self, system: str, messages: list[dict], tools: list[dict], max_tokens: int = 1500) -> dict:
        self.turn += 1
        if self.fail_at_turn and self.turn == self.fail_at_turn:
            raise RuntimeError("simulated model outage")
        task = find_task(messages)
        results = tool_results(messages)
        seen = [r["name"] for r in results]
        name, inp = self.next_call(task, results, seen)
        usage = {"inputTokens": 900 + 400 * len(results), "outputTokens": 160}
        return {"output": {"message": {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": f"tu-{self.turn}", "name": name, "input": inp}}]}},
            "stopReason": "tool_use", "usage": usage}


class ProseClient(ScriptedClient):
    """A model that answers in prose instead of calling a tool: the loop must stop with no_tool_call."""

    def converse(self, system, messages, tools, max_tokens=1500):
        self.turn += 1
        return {"output": {"message": {"role": "assistant", "content": [{"text": "It was probably the trucks."}]}},
                "stopReason": "end_turn", "usage": {"inputTokens": 900, "outputTokens": 40}}


def result_of(results: list[dict], name: str, default=None):
    return next((r["content"] for r in results if r["name"] == name), default)


def find_task(messages: list[dict]) -> dict:
    for m in messages:
        if m["role"] == "user":
            for c in m["content"]:
                if "text" in c and c["text"].startswith("TASK "):
                    return json.loads(c["text"][5:])
    return {}


def tool_results(messages: list[dict]) -> list[dict]:
    out = []
    names = {}
    for m in messages:
        for c in m["content"]:
            if "toolUse" in c:
                names[c["toolUse"]["toolUseId"]] = c["toolUse"]["name"]
            if "toolResult" in c:
                tr = c["toolResult"]
                body = tr["content"][0].get("json") or json.loads(tr["content"][0].get("text", "{}"))
                out.append({"name": names.get(tr["toolUseId"], "?"), "content": body})
    return out
