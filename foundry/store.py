"""The tables the agents read, kept in memory as lists of dicts (small enough for a demo, shaped
like the CSVs an ERP export would give you). The data-mapping sheet in the kit maps a real
company's fields onto exactly these columns. A Store also carries the company's Network (sites,
lanes, SKUs, demand rows), so an agent never imports a company by name.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from foundry.network import GFB, Network

TABLES = ("demand", "inventory", "orders", "shipments", "dock", "receipts", "promos")


@dataclass
class Store:
    demand: list[dict] = field(default_factory=list)      # week, dc, sku, forecast, actual
    inventory: list[dict] = field(default_factory=list)   # week, dc, sku, on_hand, open_receipt, snapshot_age_h
    orders: list[dict] = field(default_factory=list)      # week, dc, sku, order_id, requested, shipped, on_time, in_full
    shipments: list[dict] = field(default_factory=list)   # week, lane_from, lane_to, sku, truck_id, carrier, cases, late_days
    dock: list[dict] = field(default_factory=list)        # week, dc, trucks_served, avg_dwell_h
    receipts: list[dict] = field(default_factory=list)    # week, dc, sku, planned, received
    promos: list[dict] = field(default_factory=list)      # week, dc, sku, name, uplift_pct (the promo calendar)
    weeks: int = 12
    net: Network = field(default_factory=lambda: GFB)
    extra: dict[str, list[dict]] = field(default_factory=dict)   # company-specific tables a plug-in reads (qa_holds, returns, ...)
    dispatch_day: int = 0     # days since the snapshot when a decision is taken: the tower's clock, not the snapshot's (The Control Room)

    def rows(self, table: str, **where) -> list[dict]:
        out = []
        for r in (self.extra.get(table, []) if table not in TABLES else getattr(self, table)):
            if all(r.get(k) == v for k, v in where.items()):
                out.append(r)
        return out

    def to_csv(self, folder: str | Path) -> None:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        for t, rows in list(zip(TABLES, (getattr(self, t) for t in TABLES))) + list(self.extra.items()):
            if not rows:
                continue
            with open(folder / f"{t}.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)

    @classmethod
    def from_csv(cls, folder: str | Path, net: Network | None = None) -> "Store":
        folder = Path(folder)
        s = cls(net=net or GFB)
        for p in sorted(folder.glob("*.csv")):
            with open(p, newline="") as f:
                rows = [_typed(r) for r in csv.DictReader(f)]
            if p.stem in TABLES:
                setattr(s, p.stem, rows)
            else:
                s.extra[p.stem] = rows
        s.weeks = max((r["week"] for r in s.demand), default=12)
        return s

    @classmethod
    def from_position(cls, net: Network | None = None, week: int = 1, snapshot_age_h: float = 2.0) -> "Store":
        """A one-week store holding only the opening stock position (F.5 for GFB): what the
        rebalancing agent reads when the question is "where does the stock sit right now"."""
        net = net or GFB
        s = cls(weeks=week, net=net)
        for sku in net.parts:
            for dc in net.dcs:
                if not net.stocked(sku, dc):
                    continue
                s.inventory.append({"week": week, "dc": dc, "sku": sku, "on_hand": round(net.opening_on_hand(sku, dc)),
                                    "open_receipt": round(net.opening_open_receipt(sku, dc)), "snapshot_age_h": snapshot_age_h})
        return s


def _typed(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if v in ("True", "False"):
            out[k] = v == "True"
            continue
        try:
            out[k] = int(v)
            continue
        except ValueError:
            pass
        try:
            out[k] = float(v)
            continue
        except ValueError:
            pass
        out[k] = v
    return out
