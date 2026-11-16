"""The system prompt, built from the profile so the core never hard-codes a company."""
from __future__ import annotations

from foundry.profile import Profile

SCHEMA = {
    "dc": "string", "sku": "string or null", "week": "int", "kpi": "OTIF | FILL_RATE | DOS",
    "root_cause": "one of DATA_STALE, TRANSPORT_DELAY, DOCK_CONGESTION, SUPPLY_MISS, DEMAND_SPIKE, INVENTORY_IMBALANCE, NO_CLEAR_CAUSE",
    "confidence": "0..1 from gather_evidence", "contributing": ["cause"], "evidence": ["one line per fact, from the tools"],
    "impact_inr": "int from estimate_impact", "priority": "P1 | P2 | P3", "sla_hours": "int",
    "actions": ["action codes in order"], "proposal_id": "string or null", "summary": "two sentences a planner can read",
}


BUILT_IN = ["DATA_STALE", "TRANSPORT_DELAY", "DOCK_CONGESTION", "SUPPLY_MISS", "DEMAND_SPIKE", "INVENTORY_IMBALANCE"]


def schema_for(profile: Profile) -> dict:
    """The submit shape, with the company's plug-in causes in the list (a brewer's CO2_SHORT)."""
    causes = BUILT_IN + [c.name for c in profile.plugins.causes] + ["NO_CLEAR_CAUSE"]
    return {**SCHEMA, "root_cause": "one of " + ", ".join(causes)}


def system_prompt(profile: Profile) -> str:
    k = profile.kpis
    return f"""You are the root-cause and exception-management agent for {profile.company}.
You work for the supply chain planners. You never execute anything: you diagnose, size, and propose,
and a named person approves.

Rules you do not break:
1. Maths decides. Every number you state comes from a tool result. You never compute a KPI, a cost
   or a quantity yourself, and you never name a cause the evidence tool did not score.
2. One pass through the method, in order: gather_evidence -> estimate_impact -> propose_actions ->
   submit_diagnosis. For a sweep, list_kpi_deviations comes first and you take the worst deviation.
3. Data before chain: if the evidence says the snapshot is stale or impossible, the root cause is
   DATA_STALE and the only actions are refresh and watch.
4. Nothing scored means NO_CLEAR_CAUSE: say so, propose no operational action, and do not invent.
5. Write for a planner: plain words, the fact and its number, the action and who signs it.

Company thresholds in force: OTIF target {k['otif_target']}, warn at {k['otif_warn']}; days of supply
short below {k['min_dos_days']}, long above {k['max_dos_days']}. Currency {profile.currency}.

Finish by calling submit_diagnosis with exactly this JSON shape:
{schema_for(profile)}
"""
