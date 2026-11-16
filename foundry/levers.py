"""The containment levers, read fresh before every tool call (from The Lever Room, cut to the bone).

panel.json:  {"kill_switch": false, "propose_only": true, "disabled_tools": []}
kill_switch   stop the loop now, whatever it was doing
propose_only  the agent may write proposals, never execute; the only write tool is propose_actions
disabled      a tool the operator has switched off for everyone

A missing or unreadable panel fails closed: everything is denied.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PANEL = {"kill_switch": False, "propose_only": True, "disabled_tools": []}
READ_TOOLS = {"list_kpi_deviations", "gather_evidence", "estimate_impact", "submit_diagnosis"}
WRITE_TOOLS = {"propose_actions"}
EXECUTE_TOOLS = {"execute_action", "create_sto", "write_forecast", "book_truck_now"}   # exist so the lever has something to refuse


def register_tools(read: set[str] | None = None, write: set[str] | None = None, execute: set[str] | None = None) -> None:
    """Every agent module registers its tool names at import time: read tools are always allowed,
    write tools are the only ones that leave a mark (a proposal), execute tools are refused in
    propose-only mode. An unregistered tool is refused, whoever asks."""
    READ_TOOLS.update(read or set())
    WRITE_TOOLS.update(write or set())
    EXECUTE_TOOLS.update(execute or set())


@dataclass
class Decision:
    allowed: bool
    reason: str


class Levers:
    def __init__(self, panel_path: str | Path | None = None, audit_path: str | Path | None = None):
        self.panel_path = Path(panel_path or os.environ.get("FOUNDRY_PANEL", "panel.json"))
        self.audit_path = Path(audit_path or os.environ.get("FOUNDRY_AUDIT", "runs/audit.jsonl"))

    def read_panel(self) -> dict:
        try:
            data = json.loads(self.panel_path.read_text())
            if not isinstance(data, dict):
                raise ValueError("panel is not an object")
            return {**DEFAULT_PANEL, **data}
        except FileNotFoundError:
            if os.environ.get("FOUNDRY_PANEL_DEFAULT", "1") == "1":
                return dict(DEFAULT_PANEL)        # no panel yet: safe defaults (propose-only)
            return {"kill_switch": True, "propose_only": True, "disabled_tools": [], "_error": "panel missing"}
        except Exception as e:                      # fail closed on a corrupt panel
            return {"kill_switch": True, "propose_only": True, "disabled_tools": [], "_error": str(e)}

    def decide(self, tool: str) -> Decision:
        panel = self.read_panel()
        if panel.get("kill_switch"):
            return self._log(tool, Decision(False, "kill switch is on" + (f" ({panel['_error']})" if panel.get("_error") else "")))
        if tool in panel.get("disabled_tools", []):
            return self._log(tool, Decision(False, f"{tool} is disabled on the panel"))
        if tool in EXECUTE_TOOLS and panel.get("propose_only", True):
            return self._log(tool, Decision(False, "propose-only mode: execution needs a human approval, not the agent"))
        if tool not in READ_TOOLS | WRITE_TOOLS | EXECUTE_TOOLS:
            return self._log(tool, Decision(False, f"unknown tool {tool}"))
        return self._log(tool, Decision(True, "allowed"))

    def set(self, **changes) -> dict:
        panel = {**self.read_panel(), **changes}
        panel.pop("_error", None)
        tmp = self.panel_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(panel, indent=2))
        os.replace(tmp, self.panel_path)        # atomic: a half-written panel is never read
        return panel

    def _log(self, tool: str, d: Decision) -> Decision:
        line = json.dumps({"ts": time.time(), "tool": tool, "allowed": d.allowed, "reason": d.reason})
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_path, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass
        if os.environ.get("FOUNDRY_AUDIT_STDOUT") == "1":     # on Lambda /tmp dies with the container; the log stays
            print(json.dumps({"audit": "lever.decision", **json.loads(line)}))
        return d
