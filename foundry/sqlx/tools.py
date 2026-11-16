"""The question-to-SQL agent's tools (Converse toolSpec format) and the dispatcher that runs them.

Six doors, all read-only. The model may look at the schema, read the metric catalogue, ask for a
named metric (the preferred door: one definition, one number), or write one SELECT. It finishes by
calling `answer`. There is no tool that writes; `write_sql` is registered as an execute tool only so
the levers have something to refuse if a model ever names it.

The dispatcher keeps what actually ran (`executed`) and the last result set (`last`), so an eval can
judge the answer by the rows the agent read, not by the words it wrote.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from foundry.levers import register_tools
from foundry.sqlx import guard, semantic, warehouse
from foundry.sqlx.warehouse import SqlRefused, build_warehouse, schema_card

ROWS_TO_MODEL = 50          # the model sees at most this many rows; the dispatcher keeps up to 200

# The fourth guard. PRAGMA query_only is a connection setting, and the same connection can switch it
# off with PRAGMA query_only = OFF; only the word list stops a model sending that. An authorizer is set
# by the host program and no SQL can remove it: SQLite asks it about every action while compiling a
# statement, and it allows reads and nothing else. It is the SQLite cousin of a read-only database role.
_READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
_SAFE_PRAGMAS = {"table_info", "table_xinfo"}


def read_only_authorizer(action, arg1, arg2, db_name, source):
    if action in _READ_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in _SAFE_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def lock_down(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Install the read-only authorizer on a warehouse connection (idempotent)."""
    conn.set_authorizer(read_only_authorizer)
    return conn

_FILTERS = {"week": {"type": ["integer", "null"]}, "dc": {"type": ["string", "null"]}, "sku": {"type": ["string", "null"]},
            "weeks": {"type": ["array", "null"], "items": {"type": "integer"}, "description": "[first, last] inclusive"},
            "late_days": {"type": ["number", "null"], "description": "late_trucks only: a truck is late past this many days (default 1.0)"}}

TOOL_SPECS = [
    {"toolSpec": {"name": "list_tables", "description": "Every table in the warehouse with its columns and row count.",
                  "inputSchema": {"json": {"type": "object", "properties": {}}}}},
    {"toolSpec": {"name": "describe_table", "description": "One table: its columns with types, its row count and three sample rows.",
                  "inputSchema": {"json": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}},
    {"toolSpec": {"name": "list_metrics", "description": "The semantic layer: every named metric with its definition in words, its grain and its unit. Prefer these to free SQL.",
                  "inputSchema": {"json": {"type": "object", "properties": {}}}}},
    {"toolSpec": {"name": "metric", "description": "Compute one named metric with optional filters (week or weeks, dc, sku). Returns the SQL that ran and the rows.",
                  "inputSchema": {"json": {"type": "object", "properties": {"name": {"type": "string"}, **_FILTERS}, "required": ["name"]}}}},
    {"toolSpec": {"name": "run_sql", "description": "Run ONE read-only SELECT (or WITH ... SELECT) on the warehouse. Writes, PRAGMA, ATTACH and multiple statements are refused. Returns at most 50 rows and says if more exist.",
                  "inputSchema": {"json": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}}}},
    {"toolSpec": {"name": "answer", "description": "Finish: the answer for the planner, with the SQL or metric it rests on. Ask one clarifying question instead if the question is ambiguous.",
                  "inputSchema": {"json": {"type": "object", "properties": {
                      "text": {"type": "string", "description": "two or three plain sentences with the number(s) from the tools"},
                      "sql": {"type": ["string", "null"]}, "metric": {"type": ["string", "null"]},
                      "rows_used": {"type": ["integer", "null"]},
                      "needs_clarification": {"type": "boolean"}, "clarifying_question": {"type": ["string", "null"]},
                      "refused": {"type": "boolean", "description": "true when the request was to change data or run something other than a read"},
                      "out_of_scope": {"type": "boolean", "description": "true when the tables cannot answer the question"}},
                      "required": ["text", "needs_clarification"]}}}},
]
TOOL_NAMES = [t["toolSpec"]["name"] for t in TOOL_SPECS]
register_tools(read=set(TOOL_NAMES), execute={"write_sql"})


class Dispatcher:
    """Builds the warehouse once (or takes a connection) and runs the tools against it.

    lexical="tokens" (the default since 8 Nov 2026) puts guard.check in front of run_sql: it reads code,
    not the words inside quotes, and runs the statement it approved without rewriting it.
    lexical="strict" keeps warehouse.check_sql, the original word list, for side-by-side comparison."""

    def __init__(self, store=None, profile=None, conn: sqlite3.Connection | None = None, lexical: str = "tokens"):
        if conn is None and store is None:
            raise ValueError("give the dispatcher a store or a warehouse connection")
        if lexical not in ("tokens", "strict"):
            raise ValueError("lexical must be 'tokens' or 'strict'")
        self.conn = lock_down(conn or build_warehouse(store))
        self.profile = profile
        self.lexical = lexical
        self.executed: list[dict] = []     # every statement attempted: sql, ok, error, rows
        self.last: dict | None = None      # the last successful result set (columns, rows, sql, metric)

    # --- the tools ----------------------------------------------------------------------------------
    def run(self, name: str, inp: dict[str, Any]) -> dict:
        try:
            if name == "list_tables":
                return {"tables": self._tables()}
            if name == "describe_table":
                return self._describe(str(inp.get("name", "")))
            if name == "list_metrics":
                return {"metrics": semantic.catalogue(),
                        "note": "ask for a metric by name with week or weeks [first, last], dc and sku filters"}
            if name == "metric":
                return self._metric(inp)
            if name == "run_sql":
                return self._sql(str(inp.get("sql", "")))
            if name == "answer":
                return {"accepted": True}
            return {"error": f"unknown tool {name}"}
        except (KeyError, ValueError) as e:
            return {"error": str(e)}

    # --- helpers --------------------------------------------------------------------------------------
    def _tables(self) -> list[dict]:
        out = []
        for (t,) in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
            cols = [r[1] for r in self.conn.execute(f'PRAGMA table_info("{t}")')]
            n = self.conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            out.append({"table": t, "columns": cols, "rows": n})
        return out

    def _describe(self, name: str) -> dict:
        known = {t["table"] for t in self._tables()}
        if name not in known:
            return {"error": f"no table {name!r}; tables: {', '.join(sorted(known))}"}
        cols = [{"name": r[1], "type": r[2]} for r in self.conn.execute(f'PRAGMA table_info("{name}")')]
        n = self.conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        sample = [list(r) for r in self.conn.execute(f'SELECT * FROM "{name}" LIMIT 3')]
        return {"table": name, "columns": cols, "rows": n, "sample": sample}

    def _metric(self, inp: dict) -> dict:
        name = str(inp.get("name", "")).strip().lower()
        filters = {}
        for k in ("week", "dc", "sku"):
            if inp.get(k) not in (None, "", "ALL", "all"):
                filters[k] = inp[k]
        if inp.get("weeks"):
            w = list(inp["weeks"])
            if len(w) != 2:
                return {"error": "weeks must be [first, last]"}
            filters["weeks"] = (int(w[0]), int(w[1]))
        if inp.get("late_days") is not None:
            filters["late_days"] = float(inp["late_days"])
        res = semantic.metric(self.conn, name, **filters)
        self._record(res["sql"], True, None, res, metric=name)
        return self._to_model(res)

    def _sql(self, sql: str) -> dict:
        try:
            res = guard.run_read(self.conn, sql) if self.lexical == "tokens" else warehouse.run_sql(self.conn, sql)
        except SqlRefused as e:
            self._record(sql, False, f"refused: {e}", None)
            return {"error": f"refused: {e}", "sql": sql}
        except sqlite3.Error as e:
            self._record(sql, False, f"sql error: {e}", None)
            return {"error": f"sql error: {e}", "sql": sql, "hint": "check table and column names with list_tables or describe_table"}
        self._record(res["sql"], True, None, res)
        return self._to_model(res)

    def _record(self, sql: str, ok: bool, error: str | None, res: dict | None, metric: str | None = None) -> None:
        self.executed.append({"sql": sql, "ok": ok, "error": error, "rows": (res or {}).get("row_count"), "metric": metric})
        if ok and res is not None:
            self.last = {"sql": res["sql"], "columns": res["columns"], "rows": res["rows"], "truncated": res["truncated"], "metric": metric}

    @staticmethod
    def _to_model(res: dict) -> dict:
        out = dict(res)
        rows = res.get("rows", [])
        out["rows"] = rows[:ROWS_TO_MODEL]
        out["truncated"] = bool(res.get("truncated")) or len(rows) > ROWS_TO_MODEL
        out["rows_total_kept"] = len(rows)
        return out

    def schema(self) -> str:
        return schema_card(self.conn)
