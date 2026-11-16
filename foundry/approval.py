"""Proposals, approvals and the audit trail. The agent writes a proposal; a named person approves;
only an approved proposal can be applied, and applying it here means calling the stub that stands
in for the TMS or ERP API. Nothing in the agent loop can reach apply().
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


class ProposalStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("FOUNDRY_PROPOSALS", "runs/proposals.jsonl"))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def _append(self, row: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(row) + "\n")

    def propose(self, dc: str, sku: str | None, week: int, root_cause: str, actions: list[dict], rationale: str, run_id: str,
                kind: str = "rca", payload: dict | None = None, agent: str = "rca_exception") -> dict:
        """Every agent proposes through here. actions = [{"code", "approver", ...}]; payload carries the
        agent-specific body (a transfer, a forecast adjustment, a tower plan)."""
        row = {
            "proposal_id": f"PRP-{week:02d}-{dc}-{uuid.uuid4().hex[:6]}", "status": "PENDING_HUMAN",
            "kind": kind, "agent": agent, "dc": dc, "sku": sku, "week": week, "root_cause": root_cause, "actions": actions,
            "payload": payload or {}, "rationale": rationale, "run_id": run_id, "created_ts": time.time(), "decisions": [],
        }
        self._append(row)
        return row

    def approvers(self, proposal_id: str) -> list[str]:
        row = self.get(proposal_id)
        return sorted({a.get("approver", "") for a in (row or {}).get("actions", []) if a.get("approver")})

    def get(self, proposal_id: str) -> dict | None:
        rows = [r for r in self._all() if r["proposal_id"] == proposal_id]
        return rows[-1] if rows else None

    def decide(self, proposal_id: str, approver: str, decision: str, note: str = "", enforce: bool = True) -> dict:
        """A named person decides a pending proposal. enforce=False is for the tower runtime, which has
        already checked every step's signature (an escalated VP, a plan that carries specialists' proposals)."""
        row = self.get(proposal_id)
        if not row:
            raise KeyError(proposal_id)
        if decision not in ("APPROVED", "REJECTED"):
            raise ValueError("decision must be APPROVED or REJECTED")
        if row["status"] != "PENDING_HUMAN":     # found by The Agent Desk (8 Nov): a second decide re-opened an APPLIED proposal
            raise ValueError(f"{proposal_id} is {row['status']}: only a pending proposal can be decided")
        names = {a.get("approver", "").split(" (")[0] for a in row.get("actions", []) if a.get("approver")}
        if enforce and names and approver.split(" (")[0] not in names:
            raise PermissionError(f"{approver} is not an approver of {proposal_id}: {', '.join(sorted(names))}")
        row = {**row, "status": decision, "decisions": row["decisions"] + [{"approver": approver, "decision": decision, "note": note, "ts": time.time()}]}
        self._append(row)
        return row

    def supersede(self, proposal_id: str, by: str, note: str = "") -> dict:
        """The tower re-planned: the old proposal stops waiting for anyone. Not a human decision."""
        row = self.get(proposal_id)
        if not row:
            raise KeyError(proposal_id)
        if row["status"] != "PENDING_HUMAN":
            return row
        row = {**row, "status": "SUPERSEDED", "superseded_by": by, "decisions": row["decisions"] + [{"approver": "tower", "decision": "SUPERSEDED", "note": note, "ts": time.time()}]}
        self._append(row)
        return row

    def apply(self, proposal_id: str, only: set[str] | None = None) -> dict:
        """Write back to the operational system. Here: a stub that records what would have been sent.
        `only`: the step codes still in the signed plan; an action the plan dropped (already done this week,
        owned by another plan) is not sent."""
        row = self.get(proposal_id)
        if not row or row["status"] != "APPROVED":
            raise PermissionError("only an APPROVED proposal can be applied")
        sent = [{"system": _system_for(a["code"]), "payload": a} for a in row["actions"] if only is None or a["code"] in only]
        row = {**row, "status": "APPLIED", "applied_ts": time.time(), "sent": sent}
        self._append(row)
        return row

    def all_latest(self) -> list[dict]:
        latest = {}
        for r in self._all():
            latest[r["proposal_id"]] = r
        return list(latest.values())

    def pending(self) -> list[dict]:
        latest = {}
        for r in self._all():
            latest[r["proposal_id"]] = r
        return [r for r in latest.values() if r["status"] == "PENDING_HUMAN"]


TMS_ACTIONS = ("expedite_alternate_carrier", "inform_customers", "book_truck", "inter_dc_transfer")
PLANNING_ACTIONS = ("adjust_forecast", "promo_cap", "allocate_fair_share")


EXTRA_SYSTEMS: dict[str, str] = {}      # a plug-in's own codes -> the system that receives them (raise_ead -> EMCS)


def register_system(code: str, system: str) -> None:
    EXTRA_SYSTEMS[code] = system


def _system_for(code: str) -> str:
    if code in EXTRA_SYSTEMS:
        return EXTRA_SYSTEMS[code]
    if code in TMS_ACTIONS:
        return "TMS"
    if code in PLANNING_ACTIONS:
        return "PLANNING"
    return "ERP"
