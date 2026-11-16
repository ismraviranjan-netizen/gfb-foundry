"""Claude on Amazon Bedrock through the Converse API, plus a scripted fake for tests and evals.

Real client: boto3 bedrock-runtime, model from FOUNDRY_MODEL (default the Sonnet 4.6 global
profile that works from Mumbai), region from AWS_REGION (default ap-south-1).
Meter: tokens and cost at the Sonnet 4.6 list price, in USD and rupees.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from foundry.scripted import ScriptedClient, result_of

DEFAULT_MODEL = os.environ.get("FOUNDRY_MODEL", "global.anthropic.claude-sonnet-4-6")
PRICES_USD_PER_M = {  # input, output, cache read -- Sonnet 4.6 list price on Bedrock, Nov 2026
    "sonnet-4-6": (3.00, 15.00, 0.30),
}
INR_PER_USD = float(os.environ.get("FOUNDRY_INR_PER_USD", "95"))


@dataclass
class Meter:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0

    def add(self, usage: dict) -> None:
        self.calls += 1
        self.input_tokens += int(usage.get("inputTokens", 0))
        self.output_tokens += int(usage.get("outputTokens", 0))
        self.cache_read_tokens += int(usage.get("cacheReadInputTokens", 0))

    def cost_usd(self, model: str = DEFAULT_MODEL) -> float:
        pin, pout, pcache = PRICES_USD_PER_M["sonnet-4-6"]
        return (self.input_tokens * pin + self.output_tokens * pout + self.cache_read_tokens * pcache) / 1e6

    def cost_inr(self, model: str = DEFAULT_MODEL) -> float:
        return self.cost_usd(model) * INR_PER_USD

    def as_dict(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens, "cost_usd": round(self.cost_usd(), 5), "cost_inr": round(self.cost_inr(), 2)}


class BedrockClient:
    """The real thing. Import boto3 lazily so tests never need it."""

    def __init__(self, model: str = DEFAULT_MODEL, region: str | None = None):
        import boto3  # noqa: WPS433 (lazy on purpose)
        self.model = model
        self.rt = boto3.client("bedrock-runtime", region_name=region or os.environ.get("AWS_REGION", "ap-south-1"))

    def converse(self, system: str, messages: list[dict], tools: list[dict], max_tokens: int = 1500) -> dict:
        return self.rt.converse(
            modelId=self.model,
            system=[{"text": system}],
            messages=messages,
            toolConfig={"tools": tools},
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
        )


class FakeBedrockClient(ScriptedClient):
    """The RCA agent's scripted brain: list (sweep only) -> gather -> impact -> propose -> submit.
    ignore_evidence=True names DEMAND_SPIKE whatever the evidence says: the model that stopped
    reading, which the eval traps exist to catch."""

    def __init__(self, fail_at_turn: int | None = None, ignore_evidence: bool = False):
        super().__init__(fail_at_turn)
        self.ignore_evidence = ignore_evidence

    def next_call(self, task: dict, results: list[dict], seen: list[str]) -> tuple[str, dict]:
        if task.get("mode") == "sweep" and "list_kpi_deviations" not in seen:
            return "list_kpi_deviations", {"week": task["week"]}
        if task.get("mode") == "sweep" and "gather_evidence" not in seen:
            devs = results[-1]["content"].get("deviations", [])
            if not devs:
                return "submit_diagnosis", {"dc": "ALL", "sku": None, "week": task["week"], "kpi": "OTIF",
                                            "root_cause": "NO_CLEAR_CAUSE", "confidence": 1.0, "contributing": [],
                                            "evidence": ["no KPI moved this week"], "priority": "P3", "actions": [], "summary": "Quiet week."}
            top = devs[0]
            task.update({"dc": top["dc"], "sku": top["sku"], "kpi": top["kpi"]})
            return "gather_evidence", {"dc": top["dc"], "sku": top["sku"], "week": task["week"], "kpi": top["kpi"]}
        if "gather_evidence" not in seen:
            return "gather_evidence", {"dc": task["dc"], "sku": task.get("sku"), "week": task["week"], "kpi": task.get("kpi", "OTIF")}
        diag = result_of(results, "gather_evidence")
        if "estimate_impact" not in seen:
            return "estimate_impact", {"dc": diag["dc"], "sku": diag["sku"], "week": diag["week"]}
        tri = result_of(results, "estimate_impact")
        cause = "DEMAND_SPIKE" if self.ignore_evidence else diag["primary"]
        if "propose_actions" not in seen and tri["actions"]:
            return "propose_actions", {"dc": diag["dc"], "sku": diag["sku"], "week": diag["week"], "root_cause": cause,
                                       "actions": [a["code"] for a in tri["actions"]],
                                       "rationale": "; ".join(e["summary"] for e in diag["evidence"] if e["score"] >= 0.4) or "no strong evidence"}
        prop = result_of(results, "propose_actions", {}) or {}
        return "submit_diagnosis", {
            "dc": diag["dc"], "sku": diag["sku"], "week": diag["week"], "kpi": diag["kpi"],
            "root_cause": cause, "confidence": diag["confidence"], "contributing": diag["contributing"],
            "evidence": [e["summary"] for e in diag["evidence"] if e["score"] >= 0.4][:4],
            "impact_inr": tri["impact_inr"], "priority": tri["priority"], "sla_hours": tri["sla_hours"],
            "actions": [a["code"] for a in tri["actions"]], "proposal_id": prop.get("proposal_id"),
            "summary": f"{diag['dc']} {diag['sku'] or ''} {diag['kpi']}: {cause} ({tri['priority']}).",
        }
