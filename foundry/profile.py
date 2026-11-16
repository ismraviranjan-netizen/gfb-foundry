"""Load a company profile. The profile holds every company-specific number; the core holds none."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from foundry import plugins as _plugins

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


@dataclass
class Profile:
    company: str
    archetype: str
    currency: str
    kpis: dict[str, Any]
    diagnosis: dict[str, Any]
    triage: dict[str, Any]
    approvers: dict[str, str]
    people: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    plugins: Any = field(default_factory=_plugins.Plugins)   # Part 6: the company's plug-ins, empty by default

    def approver_for(self, action: str, dc: str | None = None) -> str:
        """Name the person who must approve an action: role from the profile, person by DC where given."""
        role = self.approvers.get(action, "vp_supply_chain")
        who = self.people.get(role, role)
        if isinstance(who, dict):
            who = who.get(dc or "", who.get("ANY", role))
        return f"{who} ({role})"

    def sla_hours(self, priority: str) -> int:
        return int(self.triage["classes"][priority]["sla_hours"])

    def section(self, name: str) -> dict[str, Any]:
        """A named section of the profile (rebalance, sensing, queue, ...), empty if absent."""
        return dict(self.raw.get(name) or {})


def load_profile(name: str = "gfb") -> Profile:
    path = Path(name) if str(name).endswith(".yaml") else PROFILES_DIR / f"{name}.yaml"
    raw = yaml.safe_load(path.read_text())
    return Profile(
        company=raw["company"], archetype=raw["archetype"], currency=raw["currency"],
        kpis=raw["kpis"], diagnosis=raw["diagnosis"], triage=raw["triage"],
        approvers=raw["approvers"], people=raw.get("people", {}), raw=raw,
        plugins=_plugins.load(raw.get("plugins"), raw.get("plugin_tables")),
    )
