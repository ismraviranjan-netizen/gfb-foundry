"""The console's API: one dispatch function, the same locally (server.py) and on Lambda (deploy/handler.py).

    GET  /api/health                       company, clock
    GET  /api/state                        everything the page draws (plans, radar now and at 06:30, queue, outbox, journal)
    GET  /api/plan/<id>                    one plan, its proposal audit trail and the chain of events that caused it
    GET  /api/cell/<week>/<dc>/<sku>       the day-by-day projection behind a radar cell
    GET  /api/outbox?to=<name>             messages, optionally for one person
    GET  /api/carbon/<id> | ?week=<n>      the carbon column for one plan or a week (The Green Ledger; read-only)
    POST /api/monday      {week}
    POST /api/decide      {plan_id, approver, decision, note?, step?}
    POST /api/refresh     {week, dc, sku, by?}
    POST /api/eta         {week, dc, sku, eta_days, carrier?, truck_id?}
    POST /api/lane        {week, lane_from, lane_to, delay_days, reason, applies_to?}
    POST /api/transfer    {transfer_id, lands_day, reason?}
    POST /api/tick        {at}
    POST /api/ask         {question}
Every POST takes an optional "at" (hours since Monday 06:30) to move the clock first. Writes need the
header x-tower-key when FOUNDRY_CONSOLE_KEY is set (a laptop demo you expose); on AWS the gateway's key
authorizer checks every write before this code runs. Reads are open: the data is made up.
"""
from __future__ import annotations

import os
from typing import Callable

from foundry.notify.coworker import clock_label

WRITE_ROUTES = ("monday", "decide", "refresh", "eta", "lane", "transfer", "tick", "ask")


class ConsoleAPI:
    def __init__(self, runtime, chat_client_factory: Callable | None = None):
        self.rt = runtime
        self.chat_client_factory = chat_client_factory

    def handle(self, method: str, path: str, query: dict | None = None, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
        query, body, headers = query or {}, body or {}, {k.lower(): v for k, v in (headers or {}).items()}
        parts = [p for p in path.split("?")[0].split("/") if p]
        if not parts or parts[0] != "api":
            return 404, {"error": f"no route {path}"}
        route = parts[1] if len(parts) > 1 else ""
        try:
            if method == "GET":
                return self._get(route, parts[2:], query)
            if method == "POST":
                key = os.environ.get("FOUNDRY_CONSOLE_KEY")
                if key and headers.get("x-tower-key") != key:
                    return 401, {"error": "writes need the x-tower-key header"}
                return self._post(route, body)
            return 405, {"error": f"method {method} not allowed"}
        except (KeyError, ValueError, PermissionError) as e:
            return 400, {"error": f"{type(e).__name__}: {e}"}

    def _get(self, route: str, rest: list[str], query: dict) -> tuple[int, dict]:
        rt = self.rt
        if route == "health":
            return 200, {"ok": True, "company": rt.profile.company, "clock": clock_label(rt.now), "plans": len(rt.plans)}
        if route == "state":
            return 200, rt.state(journal_tail=int(query.get("journal", 400)))
        if route == "plan" and rest:
            rec = rt.plans.get(rest[0])
            if not rec:
                return 404, {"error": f"no plan {rest[0]}"}
            audit = [r for r in rt.proposals._all() if r["proposal_id"] in {rec["plan_id"], rec["proposal_id"]}]
            return 200, {"plan": rt.plan_view(rec), "audit": audit, "trace": rt.bus.trace(rec["trigger"])}
        if route == "cell" and len(rest) == 3:
            from foundry.risk import projection as risk
            p = risk.project(rt.store, rt.profile, int(rest[0]), rest[1], rest[2])
            return 200, p.as_dict(with_days=True)
        if route == "outbox":
            return 200, {"messages": rt.outbox.messages(query.get("to"))}
        if route == "carbon":           # The Green Ledger: kg CO2e per plan step, shown next to the money, never applied
            from foundry.carbon.column import api_view
            return api_view(rt, rest, query)
        return 404, {"error": f"no route GET /api/{route}"}

    def _post(self, route: str, b: dict) -> tuple[int, dict]:
        rt = self.rt
        at = float(b["at"]) if b.get("at") not in (None, "") else None
        if route == "monday":
            return 200, rt.monday(int(b["week"]), at=at)
        if route == "decide":
            return 200, rt.decide(b["plan_id"], b["approver"], b["decision"], b.get("note", ""), b.get("step"), at=at)
        if route == "refresh":
            return 200, rt.refresh(int(b["week"]), b["dc"], b["sku"], b.get("by", ""), at=at)
        if route == "eta":
            return 200, rt.eta(int(b["week"]), b["dc"], b["sku"], int(b["eta_days"]), b.get("carrier", ""), b.get("truck_id", ""), at=at)
        if route == "lane":
            return 200, rt.lane_delay(int(b["week"]), b["lane_from"], b["lane_to"], int(b["delay_days"]), b["reason"], b.get("carrier", ""),
                                      b.get("applies_to", "all"), at=at)
        if route == "transfer":
            return 200, rt.transfer_delay(b["transfer_id"], int(b["lands_day"]), b.get("reason", ""), at=at)
        if route == "tick":
            return 200, rt.tick(float(b["at"]))
        if route == "ask":
            from foundry.chat import agent as chat
            client = self.chat_client_factory() if self.chat_client_factory else None
            r = chat.ask(str(b["question"])[:500], rt, client)
            return 200, {"answer": r.answer, "stop_reason": r.stop_reason, "tools": [t.get("tool") for t in r.tool_calls], "meter": r.meter}
        return 404, {"error": f"no route POST /api/{route}"}
