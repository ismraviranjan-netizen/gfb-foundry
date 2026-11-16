"""The semantic layer: every metric the tower talks about, defined once, in words and in SQL.

A question about OTIF must give the same number on the console, in the monitor, in the chat and in a
planner's spreadsheet. That only happens if 'OTIF' is defined in one place. These definitions match
foundry/agents/rca_exception/kpis.py exactly; tests/test_sqlx.py holds them to it.
"""
from __future__ import annotations

import re

from foundry.sqlx.warehouse import run_sql

METRICS: dict[str, dict] = {
    "otif": {
        "words": "orders delivered both on time and in full, divided by all orders",
        "grain": "week x dc (x sku)", "unit": "share 0-1",
        "sql": "SELECT week, dc{sku_col}, COUNT(*) AS orders, SUM(CASE WHEN on_time = 1 AND in_full = 1 THEN 1 ELSE 0 END) AS otif_orders, "
               "ROUND(1.0 * SUM(CASE WHEN on_time = 1 AND in_full = 1 THEN 1 ELSE 0 END) / COUNT(*), 4) AS otif "
               "FROM orders WHERE 1 = 1{filters} GROUP BY week, dc{sku_col} ORDER BY week, dc{sku_col}",
    },
    "fill_rate": {
        "words": "cases shipped divided by cases requested",
        "grain": "week x dc (x sku)", "unit": "share 0-1",
        "sql": "SELECT week, dc{sku_col}, SUM(requested) AS requested, SUM(shipped) AS shipped, ROUND(1.0 * SUM(shipped) / SUM(requested), 4) AS fill_rate "
               "FROM orders WHERE 1 = 1{filters} GROUP BY week, dc{sku_col} ORDER BY week, dc{sku_col}",
    },
    "days_of_supply": {
        "words": "cases on hand divided by average daily demand (weekly mean / 7)",
        "grain": "week x dc x sku", "unit": "days",
        "sql": "SELECT i.week, i.dc, i.sku, i.on_hand, i.snapshot_age_h, ROUND(i.on_hand / d.daily_demand, 1) AS days_of_supply "
               "FROM inventory i JOIN dim_dc_sku d ON d.dc = i.dc AND d.sku = i.sku WHERE 1 = 1{filters_i} ORDER BY i.week, i.dc, i.sku",
    },
    "late_trucks": {
        "words": "inbound trucks that arrived more than {late_days} day(s) after plan, and the cases they carried",
        "grain": "week x destination dc (x sku)", "unit": "trucks, cases",
        "sql": "SELECT week, lane_to AS dc{sku_col}, COUNT(*) AS trucks, SUM(CASE WHEN late_days > {late_days} THEN 1 ELSE 0 END) AS late_trucks, "
               "SUM(CASE WHEN late_days > {late_days} THEN cases ELSE 0 END) AS late_cases "
               "FROM shipments WHERE 1 = 1{filters_ship} GROUP BY week, lane_to{sku_col} ORDER BY week, lane_to{sku_col}",
    },
    "dock_dwell": {
        "words": "average hours an inbound truck waited at the DC's dock",
        "grain": "week x dc", "unit": "hours",
        "sql": "SELECT week, dc, trucks_served, avg_dwell_h FROM dock WHERE 1 = 1{filters_dock} ORDER BY week, dc",
    },
    "forecast_error": {
        "words": "absolute percentage error of the consensus forecast against sell-out (MAPE when averaged)",
        "grain": "week x dc x sku", "unit": "share 0-1",
        "sql": "SELECT week, dc, sku, forecast, actual, ROUND(1.0 * ABS(actual - forecast) / actual, 4) AS ape, ROUND(1.0 * (actual - forecast) / actual, 4) AS signed_error "
               "FROM demand WHERE actual > 0{filters} ORDER BY week, dc, sku",
    },
    "receipts_fill": {
        "words": "cases received from the plant divided by cases the plant was planned to ship",
        "grain": "week x dc x sku", "unit": "share 0-1",
        "sql": "SELECT week, dc, sku, planned, received, ROUND(1.0 * received / planned, 4) AS receipts_fill "
               "FROM receipts WHERE planned > 0{filters} ORDER BY week, dc, sku",
    },
    "cases_short": {
        "words": "cases requested but not shipped",
        "grain": "week x dc (x sku)", "unit": "cases",
        "sql": "SELECT week, dc{sku_col}, SUM(requested - shipped) AS cases_short FROM orders WHERE 1 = 1{filters} GROUP BY week, dc{sku_col} ORDER BY week, dc{sku_col}",
    },
}

_CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,15}$")


def _safe_code(v: str | None, what: str) -> str | None:
    if v is None:
        return None
    v = str(v).upper().strip()
    if not _CODE.match(v):
        raise ValueError(f"{what} must be a site or SKU code like CHN or MN-1L, got {v!r}")
    return v


def metric_sql(name: str, week: int | None = None, dc: str | None = None, sku: str | None = None,
               weeks: tuple[int, int] | None = None, late_days: float = 1.0) -> str:
    """The SQL for one metric with its filters written in, so the chat can show exactly what ran."""
    if name not in METRICS:
        raise KeyError(f"unknown metric {name!r}; known: {', '.join(METRICS)}")
    dc, sku = _safe_code(dc, "dc"), _safe_code(sku, "sku")
    f, fi, fs, fd = [], [], [], []
    if week is not None:
        w = int(week)
        f.append(f"week = {w}"); fi.append(f"i.week = {w}"); fs.append(f"week = {w}"); fd.append(f"week = {w}")
    if weeks is not None:
        a, b = int(weeks[0]), int(weeks[1])
        f.append(f"week BETWEEN {a} AND {b}"); fi.append(f"i.week BETWEEN {a} AND {b}"); fs.append(f"week BETWEEN {a} AND {b}"); fd.append(f"week BETWEEN {a} AND {b}")
    if dc:
        f.append(f"dc = '{dc}'"); fi.append(f"i.dc = '{dc}'"); fs.append(f"lane_to = '{dc}'"); fd.append(f"dc = '{dc}'")
    if sku:
        f.append(f"sku = '{sku}'"); fi.append(f"i.sku = '{sku}'"); fs.append(f"sku = '{sku}'")
    j = lambda xs: "".join(" AND " + x for x in xs)  # noqa: E731
    return METRICS[name]["sql"].format(sku_col=", sku" if sku else "", filters=j(f), filters_i=j(fi), filters_ship=j(fs),
                                       filters_dock=j(fd), late_days=late_days)


def metric(conn, name: str, **filters) -> dict:
    sql = metric_sql(name, **filters)
    res = run_sql(conn, sql)
    m = METRICS[name]
    return {"metric": name, "words": m["words"].format(late_days=filters.get("late_days", 1.0)), "grain": m["grain"],
            "unit": m["unit"], **res}


def catalogue() -> list[dict]:
    return [{"metric": k, "words": v["words"].format(late_days=1.0), "grain": v["grain"], "unit": v["unit"]} for k, v in METRICS.items()]


# --- appended metrics (Talk to Data, 8 Nov 2026) -----------------------------------------------------------------
# New definitions go below this line and the ones above never change: a changed definition changes every number
# already quoted on the console, in the chat and in last month's deck. Each appended metric has a test that holds it
# to an independent computation (tests/test_sqlx_guard.py) before any agent may call it.
METRICS["forecast_wmape"] = {
    "words": "sum of absolute forecast errors divided by the sum of sell-out over the weeks in scope (weighted MAPE); "
             "bias is the signed version, positive when sell-out beat the forecast",
    "grain": "dc (x sku) over the weeks filtered", "unit": "share 0-1",
    "sql": "SELECT dc{sku_col}, MIN(week) AS first_week, MAX(week) AS last_week, SUM(actual) AS actual, "
           "SUM(ABS(actual - forecast)) AS abs_error, "
           "ROUND(1.0 * SUM(ABS(actual - forecast)) / NULLIF(SUM(actual), 0), 4) AS wmape, "
           "ROUND(1.0 * SUM(actual - forecast) / NULLIF(SUM(actual), 0), 4) AS bias "
           "FROM demand WHERE 1 = 1{filters} GROUP BY dc{sku_col} ORDER BY dc{sku_col}",
}
