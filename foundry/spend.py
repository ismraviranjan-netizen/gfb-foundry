"""The daily spend ledger: a hard cap on what the model may cost in one day, kept outside the model.

The per-run budget (FOUNDRY_BUDGET_USD) stops one runaway loop. This stops a day of them: a public URL
called in a loop, a cron that fires too often, a test left running. Before a call that may reach Bedrock
the handler asks `allowed()`; after it, `add()` books what the run's meter says it cost.

On AWS the ledger is one item per UTC day in the panel table ("spend#2026-11-08"), updated with an atomic
ADD so two Lambdas never lose each other's cost. Locally it is a JSON file. Both fail closed: if the
ledger cannot be read, `allowed()` says no.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


class Spend:
    """File-backed ledger (laptop, tests)."""

    def __init__(self, cap_usd: float | None = None, path: str | Path | None = None):
        self.cap = float(cap_usd if cap_usd is not None else os.environ.get("FOUNDRY_DAILY_USD", "2.00"))
        self.path = Path(path or os.environ.get("FOUNDRY_SPEND", "runs/spend.json"))

    def spent(self, day: str | None = None) -> float:
        try:
            return float(json.loads(self.path.read_text()).get(day or today(), 0.0))
        except FileNotFoundError:
            return 0.0

    def add(self, usd: float, day: str | None = None) -> float:
        day = day or today()
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            data = {}
        data[day] = round(float(data.get(day, 0.0)) + float(usd or 0.0), 6)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=1))
        return data[day]

    def allowed(self) -> tuple[bool, str]:
        try:
            s = self.spent()
        except Exception as e:                       # fail closed
            return False, f"spend ledger unreadable ({e})"
        if s >= self.cap:
            return False, f"daily model budget spent: USD {s:.2f} of USD {self.cap:.2f} (resets 00:00 UTC)"
        return True, f"USD {s:.2f} of USD {self.cap:.2f} spent today"


class DynamoSpend(Spend):
    """The same ledger as one item per day in the panel table (atomic ADD)."""

    def __init__(self, cap_usd: float | None = None, table: str | None = None, client=None):
        super().__init__(cap_usd, path="/dev/null")
        self.table = table or os.environ.get("FOUNDRY_PANEL_TABLE", "gfb-foundry-panel")
        self._client = client

    def _ddb(self):
        if self._client is None:
            import boto3  # lazy
            self._client = boto3.client("dynamodb")
        return self._client

    def spent(self, day: str | None = None) -> float:
        item = self._ddb().get_item(TableName=self.table, Key={"panel_id": {"S": f"spend#{day or today()}"}},
                                    ConsistentRead=True).get("Item") or {}
        return float(item.get("usd", {}).get("N", "0"))

    def add(self, usd: float, day: str | None = None) -> float:
        out = self._ddb().update_item(TableName=self.table, Key={"panel_id": {"S": f"spend#{day or today()}"}},
                                      UpdateExpression="ADD usd :u", ExpressionAttributeValues={":u": {"N": f"{float(usd or 0.0):.6f}"}},
                                      ReturnValues="UPDATED_NEW")
        return float(out.get("Attributes", {}).get("usd", {}).get("N", "0"))


def ledger() -> Spend:
    return DynamoSpend() if os.environ.get("FOUNDRY_PANEL_TABLE") else Spend()
