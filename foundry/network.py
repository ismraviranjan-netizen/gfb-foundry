"""A company's physical network as data: sites, lanes, SKUs, demand rows, the opening position.

The core never reads a company name. Every agent asks the Store's Network for a weekly mean, a lane
or a contribution, and the Network answers from its tables. GFB (The Planner's Engine, Appendix F)
is the default Network, built from foundry/canon.py; any other company is a YAML file with the
same shape (see networks/*.yaml), which is how the capstone company and the archetype demos run on
exactly the same code.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from foundry import canon

Z = canon.Z


@dataclass
class Network:
    company: str
    code: str
    currency: str
    sites: dict[str, dict]                              # code -> {name, kind, serves}
    lanes: dict[tuple[str, str], tuple[int, float]]     # (from, to) -> (transit days, cost per case)
    parts: dict[str, tuple]                             # code -> (name, class, made_at, cost, units, annual value in crore)
    demand: dict[tuple[str, str], tuple[float, float, int]]  # (sku, dc) -> (weekly mean, weekly sd, lead weeks); the rest derived
    position: dict[tuple[str, str], tuple[float, float]]     # (sku, dc) -> (on hand, open receipt) at the opening week
    truck_cases: int = 1100
    holding_rate: float = 0.20
    contribution_override: dict[str, float] = field(default_factory=dict)   # sku -> contribution per case
    contribution_share: float = 0.129                                       # of case cost, for the other SKUs
    dc_share: dict[str, float] = field(default_factory=dict)                # share of each SKU's demand per DC
    dc_cv: dict[str, float] = field(default_factory=dict)                   # weekly sd / mean per DC
    value_unit: float = 1e7                                                 # crore -> currency units
    carriers: list[str] = field(default_factory=list)                       # hauliers the generator draws from (empty = GFB's three)
    orders_per_week: dict[str, int] = field(default_factory=dict)           # customer orders per SKU class per DC per week (empty = 12/8/5)
    sku_dc_share: dict[str, dict[str, float]] = field(default_factory=dict) # a SKU sold only in some markets: its own split (absent DC = not stocked)
    truck_space: dict[str, float] = field(default_factory=dict)             # truck slots one case of a SKU takes (a 50 l keg ~ 6.7 can cases); absent = 1

    # --- structure -----------------------------------------------------------------------------
    @property
    def dcs(self) -> list[str]:
        return [c for c, s in self.sites.items() if s["kind"] == "dc"]

    @property
    def plants(self) -> list[str]:
        return [c for c, s in self.sites.items() if s["kind"] == "plant"]

    def lane(self, src: str, dst: str) -> tuple[int, float] | None:
        return self.lanes.get((src, dst))

    def space(self, sku: str) -> float:
        """How many standard truck slots one case of this SKU takes. A lane's cost per case is for the
        standard case; a keg pays its own share of the truck (GFB: every SKU is 1)."""
        return float(self.truck_space.get(sku, 1.0))

    def dc_lanes_into(self, dst: str) -> list[tuple[str, int, float]]:
        """Inter-DC lanes that end at dst: (from, days, cost per case), cheapest first."""
        out = [(a, d, c) for (a, b), (d, c) in self.lanes.items() if b == dst and a in self.dcs]
        return sorted(out, key=lambda x: (x[2], x[1]))

    # --- demand ----------------------------------------------------------------------------------
    def annual_cases(self, sku: str) -> float:
        name, cls, made, cost, units, value_cr = self.parts[sku]
        return value_cr * self.value_unit / cost

    def share(self, sku: str, dc: str) -> float:
        """The DC's share of a SKU's demand: the SKU's own split if it has one (a pack labelled for one
        market), else the company's split."""
        if sku in self.sku_dc_share:
            return float(self.sku_dc_share[sku].get(dc, 0.0))
        return float(self.dc_share[dc])

    def stocked(self, sku: str, dc: str) -> bool:
        """Does this DC carry this SKU at all? (GFB: every DC carries every SKU.)"""
        return (sku, dc) in self.demand or (sku, dc) in self.position or self.share(sku, dc) > 0

    def weekly_mean(self, sku: str, dc: str) -> float:
        row = self.demand.get((sku, dc))
        if row:
            return float(row[0])
        return round(self.annual_cases(sku) / 52.0 * self.share(sku, dc), 0)

    def weekly_sigma(self, sku: str, dc: str) -> float:
        row = self.demand.get((sku, dc))
        if row:
            return float(row[1])
        return round(self.weekly_mean(sku, dc) * self.dc_cv[dc], 0)

    def daily_demand(self, sku: str, dc: str) -> float:
        return self.weekly_mean(sku, dc) / 7.0

    def lead_weeks(self, sku: str, dc: str) -> int:
        row = self.demand.get((sku, dc))
        if row:
            return int(row[2])
        plant = self.parts[sku][2]
        transit, _ = self.lanes[(plant, dc)]
        return max(1, math.ceil((transit + 2) / 7.0))

    def lead_days(self, sku: str, dc: str) -> int:
        return self.lead_weeks(sku, dc) * 7

    # --- money -----------------------------------------------------------------------------------
    def cost_per_case(self, sku: str) -> float:
        return float(self.parts[sku][3])

    def contribution_per_case(self, sku: str) -> float:
        if sku in self.contribution_override:
            return float(self.contribution_override[sku])
        return round(self.parts[sku][3] * self.contribution_share, 1)

    def holding_cost_per_case_day(self, sku: str) -> float:
        return self.cost_per_case(sku) * self.holding_rate / 365.0

    def sku_class(self, sku: str) -> str:
        return self.parts[sku][1]

    # --- stock arithmetic (the three formulas the whole book leans on) ---------------------------
    def days_of_supply(self, sku: str, dc: str, on_hand: float) -> float:
        d = self.daily_demand(sku, dc)
        return on_hand / d if d else float("inf")

    def safety_stock(self, sku: str, dc: str, csl: float = 0.95) -> float:
        return Z[csl] * self.weekly_sigma(sku, dc) * math.sqrt(self.lead_weeks(sku, dc))

    def reorder_point(self, sku: str, dc: str, csl: float = 0.95) -> float:
        return self.weekly_mean(sku, dc) * self.lead_weeks(sku, dc) + self.safety_stock(sku, dc, csl)

    def opening_on_hand(self, sku: str, dc: str, weeks_of_cover: float = 1.6) -> float:
        pos = self.position.get((sku, dc))
        if pos:
            return float(pos[0])
        return weeks_of_cover * self.weekly_mean(sku, dc)

    def opening_open_receipt(self, sku: str, dc: str) -> float:
        pos = self.position.get((sku, dc))
        return float(pos[1]) if pos else 0.0

    # --- loading ---------------------------------------------------------------------------------
    @classmethod
    def gfb(cls) -> "Network":
        """The default: GFB from the canon, numbers identical to Appendix F."""
        demand = {("MN-1L", dc): (r["mu"], r["sigma"], r["L"]) for dc, r in canon.MN1L.items()}
        position = {("MN-1L", dc): (r["on_hand"], r["open_receipt"]) for dc, r in canon.MN1L.items()}
        return cls(
            company=canon.COMPANY, code="GFB", currency="INR",
            sites=dict(canon.SITES), lanes=dict(canon.LANES), parts=dict(canon.PARTS),
            demand=demand, position=position, truck_cases=canon.TRUCK_CASES, holding_rate=canon.HOLDING_RATE,
            contribution_override={"MN-1L": float(canon.CONTRIBUTION_MN1L)}, contribution_share=canon.CONTRIBUTION_SHARE,
            dc_share=dict(canon.DC_SHARE), dc_cv=dict(canon.DC_CV),
        )

    def to_dict(self) -> dict:
        """The YAML shape (networks/gfb.yaml is this, dumped): the template for any other company."""
        return {
            "company": self.company, "code": self.code, "currency": self.currency, "truck_cases": self.truck_cases,
            "holding_rate": self.holding_rate, "contribution_share": self.contribution_share, "value_unit": self.value_unit,
            "sites": [{"code": c, **s} for c, s in self.sites.items()],
            "lanes": [{"from": a, "to": b, "days": d, "cost_per_case": c} for (a, b), (d, c) in self.lanes.items()],
            "parts": [{"code": k, "name": v[0], "class": v[1], "made_at": v[2], "cost": v[3], "units": v[4], "annual_value": v[5]} for k, v in self.parts.items()],
            "demand": [{"sku": s, "dc": d, "mean": m, "sd": sd, "lead_weeks": L} for (s, d), (m, sd, L) in self.demand.items()],
            "position": [{"sku": s, "dc": d, "on_hand": oh, "open_receipt": orc} for (s, d), (oh, orc) in self.position.items()],
            "dc_share": dict(self.dc_share), "dc_cv": dict(self.dc_cv), "contribution_override": dict(self.contribution_override),
            **({"carriers": list(self.carriers)} if self.carriers else {}),
            **({"orders_per_week": dict(self.orders_per_week)} if self.orders_per_week else {}),
            **({"sku_dc_share": {k: dict(v) for k, v in self.sku_dc_share.items()}} if self.sku_dc_share else {}),
            **({"truck_space": dict(self.truck_space)} if self.truck_space else {}),
        }

    def to_yaml(self, path: str | Path) -> Path:
        import yaml
        Path(path).write_text(yaml.safe_dump(self.to_dict(), sort_keys=False))
        return Path(path)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Network":
        """Any other company. See networks/sunrise.yaml for the shape."""
        import yaml
        doc = yaml.safe_load(Path(path).read_text())
        lanes = {(l["from"], l["to"]): (int(l["days"]), float(l["cost_per_case"])) for l in doc["lanes"]}
        parts = {p["code"]: (p["name"], p["class"], p["made_at"], float(p["cost"]), int(p.get("units", 1)), float(p["annual_value"]))
                 for p in doc["parts"]}
        demand = {(d["sku"], d["dc"]): (float(d["mean"]), float(d["sd"]), int(d["lead_weeks"])) for d in doc.get("demand", [])}
        position = {(p["sku"], p["dc"]): (float(p["on_hand"]), float(p.get("open_receipt", 0))) for p in doc.get("position", [])}
        sites = {s["code"]: {"name": s["name"], "kind": s["kind"], "serves": s.get("serves", "")} for s in doc["sites"]}
        dcs = [c for c, s in sites.items() if s["kind"] == "dc"]
        share = doc.get("dc_share") or {dc: 1.0 / len(dcs) for dc in dcs}
        cv = doc.get("dc_cv") or {dc: 0.15 for dc in dcs}
        return cls(
            company=doc["company"], code=doc["code"], currency=doc.get("currency", "INR"),
            sites=sites, lanes=lanes, parts=parts, demand=demand, position=position,
            truck_cases=int(doc.get("truck_cases", 1100)), holding_rate=float(doc.get("holding_rate", 0.2)),
            contribution_override={k: float(v) for k, v in (doc.get("contribution_override") or {}).items()},
            contribution_share=float(doc.get("contribution_share", 0.129)),
            dc_share={k: float(v) for k, v in share.items()}, dc_cv={k: float(v) for k, v in cv.items()},
            value_unit=float(doc.get("value_unit", 1e7)),
            carriers=list(doc.get("carriers") or []),
            orders_per_week={k: int(v) for k, v in (doc.get("orders_per_week") or {}).items()},
            sku_dc_share={k: {d: float(s) for d, s in v.items()} for k, v in (doc.get("sku_dc_share") or {}).items()},
            truck_space={k: float(v) for k, v in (doc.get("truck_space") or {}).items()},
        )


GFB = Network.gfb()
