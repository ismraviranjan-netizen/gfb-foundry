"""Data quality as SQL: the known lies of the mapping sheet, written as queries that return the rows
that fail. A check that returns no rows passes. Run them before any agent reads the tables, and put
the failing count on the console beside the KPIs.

Each check has a tolerance where the data has honest noise (a truck split into cases rounds by a
case or two), because a check that cries wolf every week is switched off by week three, the same
lesson as the monitor's chronic rule.
"""
from __future__ import annotations

from foundry.sqlx.warehouse import run_sql

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _checks(max_age_h: float) -> list[dict]:
    return [
        {"check": "stale_snapshot", "severity": "high",
         "words": f"stock snapshots older than {max_age_h:g} h (the number may be yesterday's or worse)",
         "sql": f"SELECT week, dc, sku, on_hand, snapshot_age_h FROM inventory WHERE snapshot_age_h > {max_age_h} ORDER BY week, dc, sku"},
        {"check": "impossible_drop", "severity": "high",
         "words": "on hand fell more than 80% in a week while receipts covered at least 80% of what shipped (stock cannot vanish)",
         # orders are summed once in a CTE and joined; a correlated subquery per receipt row ran past the
         # warehouse's step budget (3,840 orders scanned 480 times), which is the guard doing its job
         "sql": "WITH inv AS (SELECT week, dc, sku, on_hand, LAG(on_hand) OVER (PARTITION BY dc, sku ORDER BY week) AS prev FROM inventory), "
                "shipped AS (SELECT week, dc, sku, SUM(shipped) AS shipped FROM orders GROUP BY week, dc, sku) "
                "SELECT i.week, i.dc, i.sku, i.prev, i.on_hand, r.received, COALESCE(s.shipped, 0) AS shipped "
                "FROM inv i JOIN receipts r ON r.week = i.week AND r.dc = i.dc AND r.sku = i.sku "
                "LEFT JOIN shipped s ON s.week = i.week AND s.dc = i.dc AND s.sku = i.sku "
                "WHERE i.prev > 0 AND i.on_hand < 0.2 * i.prev AND r.received >= 0.8 * COALESCE(s.shipped, 0) ORDER BY i.week, i.dc, i.sku"},
        {"check": "book_balance", "severity": "high",
         "words": "snapshots that disagree with the stock rebuilt from flows (week 1 + received - shipped) by more than 5 cases",
         # added 8 Nov 2026 (Talk to Data, chapter 7): the running-sum reconciliation, the check that proves a snapshot
         # wrong instead of only suspecting it; it assumes week 1's snapshot is right, which the other checks watch
         "sql": "WITH f AS (SELECT i.week, i.dc, i.sku, i.on_hand, r.received, COALESCE(o.shipped, 0) AS shipped FROM inventory i "
                "JOIN receipts r ON r.week = i.week AND r.dc = i.dc AND r.sku = i.sku "
                "LEFT JOIN (SELECT week, dc, sku, SUM(shipped) AS shipped FROM orders GROUP BY week, dc, sku) o "
                "ON o.week = i.week AND o.dc = i.dc AND o.sku = i.sku), "
                "b AS (SELECT week, dc, sku, on_hand, FIRST_VALUE(on_hand) OVER w + SUM(received - shipped) OVER w "
                "- (FIRST_VALUE(received) OVER w - FIRST_VALUE(shipped) OVER w) AS book FROM f "
                "WINDOW w AS (PARTITION BY dc, sku ORDER BY week ROWS UNBOUNDED PRECEDING)) "
                "SELECT week, dc, sku, on_hand, book, on_hand - book AS gap FROM b WHERE ABS(on_hand - book) > 5 ORDER BY week, dc, sku"},
        {"check": "shipped_over_requested", "severity": "high",
         "words": "orders that shipped more cases than were requested",
         "sql": "SELECT week, dc, sku, order_id, requested, shipped FROM orders WHERE shipped > requested ORDER BY week, dc"},
        {"check": "negative_on_hand", "severity": "high",
         "words": "negative stock on hand",
         "sql": "SELECT week, dc, sku, on_hand FROM inventory WHERE on_hand < 0 ORDER BY week, dc, sku"},
        {"check": "received_over_planned", "severity": "medium",
         "words": "plant receipts above plan by more than 2% plus 5 cases (truck rounding is allowed)",
         "sql": "SELECT week, dc, sku, planned, received FROM receipts WHERE received > 1.02 * planned + 5 ORDER BY week, dc, sku"},
        {"check": "demand_without_orders", "severity": "medium",
         "words": "weeks with sell-out recorded but no orders at that DC and SKU",
         "sql": "WITH o AS (SELECT DISTINCT week, dc, sku FROM orders) SELECT d.week, d.dc, d.sku, d.actual FROM demand d "
                "LEFT JOIN o ON o.week = d.week AND o.dc = d.dc AND o.sku = d.sku WHERE d.actual > 0 AND o.sku IS NULL ORDER BY d.week, d.dc, d.sku"},
        {"check": "orphan_sku", "severity": "medium",
         "words": "SKUs in the fact tables that the SKU dimension does not know",
         "sql": "SELECT DISTINCT sku FROM (SELECT sku FROM orders UNION SELECT sku FROM inventory UNION SELECT sku FROM demand) "
                "WHERE sku NOT IN (SELECT sku FROM dim_sku) ORDER BY sku"},
        {"check": "missing_weeks", "severity": "low",
         "words": "DC and SKU pairs with fewer inventory weeks than the warehouse holds",
         "sql": "SELECT dc, sku, COUNT(DISTINCT week) AS weeks_present, (SELECT COUNT(DISTINCT week) FROM inventory) AS weeks_expected "
                "FROM inventory GROUP BY dc, sku HAVING COUNT(DISTINCT week) < (SELECT COUNT(DISTINCT week) FROM inventory) ORDER BY dc, sku"},
        {"check": "late_without_carrier", "severity": "low",
         "words": "late trucks with no carrier named (the alternate-carrier playbook needs it)",
         "sql": "SELECT week, truck_id, lane_from, lane_to, late_days FROM shipments WHERE late_days > 0 AND (carrier IS NULL OR TRIM(carrier) = '') ORDER BY week"},
    ]


def checks(conn, profile=None) -> list[dict]:
    """Run every check; return one row per check with its failing count and up to five samples."""
    max_age = float((getattr(profile, "diagnosis", None) or {}).get("max_snapshot_age_h", 24)) if profile else 24.0
    out = []
    for c in _checks(max_age):
        res = run_sql(conn, c["sql"], max_rows=200)
        out.append({"check": c["check"], "severity": c["severity"], "words": c["words"], "rows_failing": res["row_count"],
                    "truncated": res["truncated"], "columns": res["columns"], "sample": res["rows"][:5], "sql": res["sql"]})
    out.sort(key=lambda r: (r["rows_failing"] == 0, SEVERITY_ORDER[r["severity"]], r["check"]))
    return out


def summary(results: list[dict]) -> dict:
    failing = [r for r in results if r["rows_failing"]]
    return {"checks": len(results), "failing": len(failing),
            "high": sum(1 for r in failing if r["severity"] == "high"),
            "lines": [f"{r['check']}: {r['rows_failing']} row(s)" for r in failing]}
