"""The levers panel in DynamoDB (Part 6): the same three levers, read before every tool call, but
the panel is one item in a table instead of a file on one machine, so the operator's phone and
every Lambda see the same switch within a second.

Item: {"panel_id": "gfb", "kill_switch": false, "propose_only": true, "disabled_tools": []}
Fail closed: a missing item, a throttled read or any error reads as kill_switch=true.
"""
from __future__ import annotations

import os

from foundry.levers import DEFAULT_PANEL, Levers


_SEEN: dict[str, dict] = {}     # the panel each container last read, to notice a flip made outside the code


def _audit(kind: str, **fields) -> None:
    """One JSON line to stdout: on Lambda that is CloudWatch Logs, which outlives the container."""
    import json
    import time
    print(json.dumps({"audit": kind, "ts": time.time(), **fields}, default=str))


class DynamoLevers(Levers):
    def __init__(self, table: str | None = None, panel_id: str | None = None, audit_path=None, client=None):
        super().__init__(panel_path="/dev/null", audit_path=audit_path)
        self.table = table or os.environ.get("FOUNDRY_PANEL_TABLE", "gfb-foundry-panel")
        self.panel_id = panel_id or os.environ.get("FOUNDRY_PANEL_ID", "gfb")
        self._client = client

    def _ddb(self):
        if self._client is None:
            import boto3  # lazy
            self._client = boto3.client("dynamodb")
        return self._client

    def read_panel(self) -> dict:
        try:
            item = self._ddb().get_item(TableName=self.table, Key={"panel_id": {"S": self.panel_id}}, ConsistentRead=True).get("Item")
            if not item:
                return {"kill_switch": True, "propose_only": True, "disabled_tools": [], "_error": "panel item missing"}
            panel = {**DEFAULT_PANEL,
                     "kill_switch": item.get("kill_switch", {}).get("BOOL", False),
                     "propose_only": item.get("propose_only", {}).get("BOOL", True),
                     "disabled_tools": [v["S"] for v in item.get("disabled_tools", {}).get("L", [])]}
            last = _SEEN.get(self.panel_id)
            if last is not None and last != panel:      # flipped from the AWS console, the CLI or another Lambda
                _audit("panel.changed", panel_id=self.panel_id, before=last, after=panel)
            _SEEN[self.panel_id] = panel
            return panel
        except Exception as e:  # fail closed
            return {"kill_switch": True, "propose_only": True, "disabled_tools": [], "_error": str(e)}

    def set(self, by: str = "code", **changes) -> dict:
        before = self.read_panel()
        panel = {**before, **changes}
        panel.pop("_error", None)
        before.pop("_error", None)
        self._ddb().put_item(TableName=self.table, Item={
            "panel_id": {"S": self.panel_id},
            "kill_switch": {"BOOL": bool(panel["kill_switch"])},
            "propose_only": {"BOOL": bool(panel["propose_only"])},
            "disabled_tools": {"L": [{"S": t} for t in panel.get("disabled_tools", [])]},
        })
        _audit("panel.set", panel_id=self.panel_id, by=by, before=before, after=panel)
        _SEEN[self.panel_id] = {**DEFAULT_PANEL, "kill_switch": bool(panel["kill_switch"]), "propose_only": bool(panel["propose_only"]),
                                "disabled_tools": list(panel.get("disabled_tools", []))}
        return panel
