"""The Store as a SQL warehouse: SQLite in memory, built from the tables the agents already read.

Facts:  demand · inventory · orders · shipments · dock · receipts · promos · any plug-in table
Dims:   dim_site · dim_sku · dim_lane · dim_dc_sku (the F.5-style row per DC and SKU)

The connection is put in query_only mode after it is built, so a write is refused by SQLite itself,
not only by the word list in run_sql. That is the structural guard; the word list is the second one.
"""
from __future__ import annotations

import re
import sqlite3
import time

from foundry.store import TABLES

MAX_ROWS = 200
FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum|trigger|reindex|analyze)\b", re.I)


class SqlRefused(Exception):
    """A statement the warehouse will not run: not a single read-only SELECT or WITH."""


def _sql_type(v) -> str:
    if isinstance(v, bool) or isinstance(v, int):
        return "INTEGER"
    if isinstance(v, float):
        return "REAL"
    return "TEXT"


def _load(conn: sqlite3.Connection, name: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    types = {}
    for c in cols:
        v = next((r.get(c) for r in rows if r.get(c) is not None), "")
        types[c] = _sql_type(v)
    conn.execute(f'CREATE TABLE "{name}" (' + ", ".join(f'"{c}" {types[c]}' for c in cols) + ")")
    conn.executemany(f'INSERT INTO "{name}" VALUES (' + ",".join("?" for _ in cols) + ")",
                     [[(int(r.get(c)) if isinstance(r.get(c), bool) else r.get(c)) for c in cols] for r in rows])


def build_warehouse(store) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    for t in TABLES:
        _load(conn, t, getattr(store, t))
    for name, rows in getattr(store, "extra", {}).items():
        _load(conn, name, rows)
    net = store.net
    _load(conn, "dim_site", [{"code": c, "name": s["name"], "kind": s["kind"], "serves": s.get("serves", "")} for c, s in net.sites.items()])
    _load(conn, "dim_sku", [{"sku": k, "name": v[0], "class": v[1], "made_at": v[2], "cost_per_case": float(v[3]), "units_per_case": int(v[4]),
                             "annual_value_cr": float(v[5]), "contribution_per_case": float(net.contribution_per_case(k))} for k, v in net.parts.items()])
    _load(conn, "dim_lane", [{"lane_from": a, "lane_to": b, "days": int(d), "cost_per_case": float(c), "inter_dc": int(a in net.dcs and b in net.dcs)}
                             for (a, b), (d, c) in net.lanes.items()])
    _load(conn, "dim_dc_sku", [{"dc": dc, "sku": sku, "weekly_mean": float(net.weekly_mean(sku, dc)), "weekly_sd": float(net.weekly_sigma(sku, dc)),
                                "daily_demand": round(net.daily_demand(sku, dc), 2), "lead_weeks": int(net.lead_weeks(sku, dc)),
                                "safety_stock_95": round(net.safety_stock(sku, dc)), "reorder_point_95": round(net.reorder_point(sku, dc))}
                               for sku in net.parts for dc in net.dcs])
    conn.commit()
    conn.execute("PRAGMA query_only = ON")        # from here on, SQLite itself refuses writes
    return conn


def check_sql(sql: str) -> str:
    s = re.sub(r"--[^\n]*", " ", sql)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S).strip().rstrip(";").strip()
    if ";" in s:
        raise SqlRefused("one statement only")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise SqlRefused("read-only: a query must start with SELECT or WITH")
    m = FORBIDDEN.search(s)
    if m:
        raise SqlRefused(f"read-only: '{m.group(1)}' is not allowed")
    return s


def run_sql(conn: sqlite3.Connection, sql: str, max_rows: int = MAX_ROWS, max_steps: int = 5_000_000) -> dict:
    """Run one read-only query. Returns the columns, up to max_rows rows, and whether it was cut.
    A runaway query is stopped by a step budget (SQLite's progress handler), not by a timer."""
    clean = check_sql(sql)
    steps = {"n": 0}

    def _guard():
        steps["n"] += 1
        return 1 if steps["n"] * 1000 > max_steps else 0

    conn.set_progress_handler(_guard, 1000)
    t0 = time.time()
    try:
        cur = conn.execute(clean)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(max_rows + 1)
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            raise SqlRefused("query stopped: it ran past the step budget") from e
        raise
    finally:
        conn.set_progress_handler(None, 0)
    truncated = len(rows) > max_rows
    rows = [list(r) for r in rows[:max_rows]]
    return {"sql": clean, "columns": cols, "rows": rows, "row_count": len(rows), "truncated": truncated,
            "elapsed_ms": round((time.time() - t0) * 1000, 1)}


def schema_card(conn: sqlite3.Connection) -> str:
    """One line per table: its columns. What a question-to-SQL prompt needs, and nothing else."""
    out = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
        out.append(f"{name}({', '.join(cols)})")
    return "\n".join(out)
