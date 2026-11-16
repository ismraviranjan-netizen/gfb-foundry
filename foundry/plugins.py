"""Plug-in points: where the core grows for a company whose difference is not a number.

A profile can name plug-in modules:

    plugins:
      - foundry.plugin_lib.pharma_qa_release
      - foundry.plugin_lib.retail_delivery_wave

Each module has a `register(plugins: Plugins)` function that adds any of three things:
  * a cause: a gatherer the diagnosis tree runs beside the six built-in ones, with its playbook
  * a playbook step: an action inserted first or last for a cause (QA release before a transfer)
  * an urgency clock: hours to impact computed differently (hours to the next store delivery wave)

The registry lives on the Profile instance, so two companies in one process never share a plug-in,
and the defaults (no plug-ins) leave the core's behaviour exactly as the tests pin it.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class CausePlugin:
    name: str                       # e.g. "QA_HOLD"
    gatherer: Callable               # (store, profile, week, dc, sku) -> Evidence
    playbook: list[str]              # action codes, in order
    action_text: dict[str, str]      # code -> text for any new codes
    dc_level: bool = False           # run for DC-level questions too (sku is None)


@dataclass
class StepPlugin:
    code: str                        # e.g. "qa_release"
    text: str
    position: str                    # "first" | "last"
    for_causes: list[str] | None     # None = every cause
    when_action: str | None = None   # only when the playbook contains this action (e.g. inter_dc_transfer)


@dataclass
class LaneStepPlugin:
    code: str                        # e.g. "raise_ead"
    text: str
    applies: Callable                # (net, profile, from_dc, to_dc) -> str | None: why this lane needs the step, or None


@dataclass
class Plugins:
    causes: list[CausePlugin] = field(default_factory=list)
    steps: list[StepPlugin] = field(default_factory=list)
    urgency_clock: Callable | None = None       # (store, profile, week, dc, sku, default_hours) -> hours
    overrides: list[Callable] = field(default_factory=list)   # (evidence: list, kpi) -> list; a new cause that explains an old one
    tables: dict[str, list[dict]] = field(default_factory=dict)   # extra tables a plug-in reads (qa_holds, returns, ...)
    names: list[str] = field(default_factory=list)
    routes: dict[str, list[str]] = field(default_factory=dict)    # The Control Room: which specialists a plug-in cause earns
    lane_steps_: list[LaneStepPlugin] = field(default_factory=list)  # paperwork a particular lane needs (an excise document, a customs entry)

    # --- what a plug-in module calls ---------------------------------------------------------------
    def add_cause(self, name: str, gatherer: Callable, playbook: list[str], action_text: dict[str, str] | None = None,
                  dc_level: bool = False) -> None:
        self.causes.append(CausePlugin(name, gatherer, list(playbook), dict(action_text or {}), dc_level))

    def add_step(self, code: str, text: str, position: str = "first", for_causes: list[str] | None = None,
                 when_action: str | None = None) -> None:
        self.steps.append(StepPlugin(code, text, position, for_causes, when_action))

    def add_route(self, cause: str, specialists: list[str]) -> None:
        """The tower's routing for a plug-in cause (a CO2 shortage at the brewery earns the rebalancing
        specialist, as a short plant run does). Without a route the tower asks no specialist."""
        self.routes[cause] = list(specialists)

    def add_lane_step(self, code: str, text: str, applies: Callable) -> None:
        """A step the rebalancing specialist adds to a transfer when the lane needs it: applies(net, profile,
        from_dc, to_dc) returns the reason ('NL tax warehouse -> BE tax warehouse, duty suspended') or None.
        The step sits after the co-sign and before the truck: decide, co-sign, paperwork, truck."""
        self.lane_steps_.append(LaneStepPlugin(code, text, applies))

    def lane_steps(self, net, profile, from_dc: str, to_dc: str) -> list[dict]:
        out = []
        for ls in self.lane_steps_:
            why = ls.applies(net, profile, from_dc, to_dc)
            if why:
                out.append({"code": ls.code, "text": f"{ls.text} ({why})", "why": why})
        return out

    def set_urgency_clock(self, fn: Callable) -> None:
        self.urgency_clock = fn

    def add_override(self, fn: Callable) -> None:
        """fn(evidence, kpi) -> evidence. Runs after every gatherer and before the ranking, so a
        plug-in cause can demote a built-in cause it explains (a rejected cold-chain load looks
        like a plant that shipped short until the cold-chain table says otherwise)."""
        self.overrides.append(fn)

    def apply_overrides(self, evidence: list, kpi: str) -> list:
        for fn in self.overrides:
            evidence = fn(evidence, kpi)
        return evidence

    # --- what the core calls --------------------------------------------------------------------------
    def action_text(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for c in self.causes:
            out.update(c.action_text)
        for s in self.steps:
            out[s.code] = s.text
        for ls in self.lane_steps_:
            out.setdefault(ls.code, ls.text)
        return out

    def playbook_for(self, cause: str) -> list[str] | None:
        for c in self.causes:
            if c.name == cause:
                return list(c.playbook)
        return None

    def apply_steps(self, cause: str, codes: list[str]) -> list[str]:
        out = list(codes)
        for s in self.steps:
            if s.for_causes and cause not in s.for_causes:
                continue
            if s.when_action and s.when_action not in out:
                continue
            if s.code in out:
                continue
            if s.position == "first":
                out.insert(0, s.code)
            else:
                out.append(s.code)
        return out

    def hours_to_impact(self, store, profile, week: int, dc: str, sku: str | None, default_hours: float) -> float:
        if self.urgency_clock is None:
            return default_hours
        return float(self.urgency_clock(store, profile, week, dc, sku, default_hours))


def load(names: list[str] | None, tables: dict[str, list[dict]] | None = None) -> Plugins:
    """Import each named module and let it register. Unknown modules fail loudly: a profile that
    names a plug-in expects it."""
    p = Plugins(tables=dict(tables or {}))
    for name in names or []:
        mod = importlib.import_module(name)
        mod.register(p)
        p.names.append(name)
    return p
