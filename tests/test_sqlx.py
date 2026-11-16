"""Talk to Data, part 1: the warehouse, the semantic layer, the quality checks and the guards."""
import math
import sqlite3
import time

import pytest

from foundry.agents.rca_exception import kpis
from foundry.data.generate import canonical_store
from foundry.sqlx import guard, quality, semantic, warehouse
from foundry.sqlx.tools import Dispatcher, lock_down
from foundry.sqlx.traps import INJECTION, with_carrier_notes
from foundry.sqlx.warehouse import SqlRefused, build_warehouse, check_sql, run_sql, schema_card


@pytest.fixture(scope="module")
def canon():
    return canonical_store()


@pytest.fixture
def conn(canon):
    return build_warehouse(canon)


# --- the warehouse ------------------------------------------------------------------------------------------
def test_warehouse_holds_facts_and_dimensions(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"demand", "inventory", "orders", "shipments", "dock", "receipts", "promos",
            "dim_site", "dim_sku", "dim_lane", "dim_dc_sku"} <= tables
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("orders", "inventory", "demand", "shipments", "dock")}
    assert counts == {"orders": 3840, "inventory": 480, "demand": 480, "shipments": 683, "dock": 48}
    assert len(schema_card(conn).splitlines()) == 11


def test_plugin_tables_load_as_tables(canon):
    c = build_warehouse(with_carrier_notes(canon))
    rows = run_sql(c, "SELECT truck_id, note FROM carrier_notes WHERE week = 12 ORDER BY truck_id")["rows"]
    assert [r[0] for r in rows] == ["GFB-T-1670", "GFB-T-1671", "GFB-T-1672"] and rows[2][1] == INJECTION
    assert "carrier_notes" not in canon.extra          # the canonical store is not touched


# --- the semantic layer: one definition, one number ---------------------------------------------------------
def test_otif_and_fill_rate_match_kpis_on_every_row(canon, conn):
    for w, dc, _orders, _ok, v in run_sql(conn, semantic.metric_sql("otif"), max_rows=1000)["rows"]:
        assert v == round(kpis.otif(canon, w, dc), 4)
    for w, dc, _req, _shp, v in run_sql(conn, semantic.metric_sql("fill_rate"), max_rows=1000)["rows"]:
        assert v == round(kpis.fill_rate(canon, w, dc), 4)
    for sku in ("MN-1L", "MP-1KG", "GC-200"):
        for w, dc, s, _o, _k, v in run_sql(conn, semantic.metric_sql("otif", sku=sku), max_rows=1000)["rows"]:
            assert v == round(kpis.otif(canon, w, dc, s), 4)


def test_days_of_supply_agrees_with_kpis_within_a_tenth(canon, conn):
    """Not exact, and the book says why: dim_dc_sku keeps daily demand to 2 dp, and SQLite's ROUND goes half
    away from zero where Python's goes half to even. 5 of 480 rows differ by 0.1 day; none by more."""
    rows = run_sql(conn, semantic.metric_sql("days_of_supply"), max_rows=1000)["rows"]
    diffs = [abs(v - kpis.days_of_supply(canon, w, dc, sku)) for w, dc, sku, _oh, _age, v in rows]
    assert len(rows) == 480 and max(diffs) < 0.06
    assert sum(1 for w, dc, sku, _oh, _age, v in rows if v != round(kpis.days_of_supply(canon, w, dc, sku), 1)) <= 5
    kol = semantic.metric(conn, "days_of_supply", week=12, dc="KOL", sku="MN-1L")["rows"]
    assert kol == [[12, "KOL", "MN-1L", 400, 36.0, 2.7]]


def test_forecast_error_divides_as_reals(conn):
    """Regression: written as ABS(actual - forecast) / actual, SQLite divided integers and every error was 0."""
    rows = semantic.metric(conn, "forecast_error", weeks=(9, 12), dc="BHW", sku="GC-200")["rows"]
    assert [r[5] for r in rows] == [0.1401, 0.4379, 0.5, 0.5]
    assert run_sql(conn, "SELECT ABS(4426 - 2213) / 4426 AS integer_division")["rows"] == [[0]]


def test_metric_sql_takes_codes_not_sql(conn):
    with pytest.raises(ValueError):
        semantic.metric_sql("otif", dc="CHN'; DROP TABLE orders; --")
    with pytest.raises(KeyError):
        semantic.metric_sql("happiness")
    assert {m["metric"] for m in semantic.catalogue()} == set(semantic.METRICS)


def test_late_truck_threshold_is_strictly_more_than_a_day(conn):
    strict = semantic.metric(conn, "late_trucks", week=12, dc="CHN")["rows"][0]
    loose = run_sql(conn, "SELECT SUM(CASE WHEN late_days >= 1 THEN 1 ELSE 0 END) FROM shipments WHERE week = 12 AND lane_to = 'CHN'")["rows"][0][0]
    assert strict[3] == 2 and strict[4] == 1762 and loose == 3      # '>' or '>=' is a definition, not a detail


# --- data quality as SQL --------------------------------------------------------------------------------------
def test_quality_checks_find_the_kolkata_lie(conn):
    res = quality.checks(conn)
    failing = {r["check"]: r for r in res if r["rows_failing"]}
    assert set(failing) == {"stale_snapshot", "impossible_drop", "book_balance"}
    assert failing["stale_snapshot"]["sample"] == [[12, "KOL", "MN-1L", 400, 36.0]]
    assert failing["impossible_drop"]["sample"] == [[12, "KOL", "MN-1L", 9037, 400, 1256, 971]]
    assert failing["book_balance"]["sample"] == [[12, "KOL", "MN-1L", 400, 9322, -8922]]   # the flows say 9,322
    assert quality.summary(res)["high"] == 3 and quality.summary(res)["checks"] == 10


def test_quality_checks_pass_after_the_refresh():
    res = quality.checks(build_warehouse(canonical_store(refreshed=True)))
    assert all(r["rows_failing"] == 0 for r in res)


def test_tolerances_keep_checks_from_crying_wolf(conn):
    raw = run_sql(conn, "SELECT COUNT(*), SUM(received - planned) FROM receipts WHERE received > planned")["rows"][0]
    assert raw == [25, 27]                                      # truck rounding: +1 or +2 cases
    assert next(r for r in quality.checks(conn) if r["check"] == "received_over_planned")["rows_failing"] == 0
    drops = run_sql(conn, "WITH i AS (SELECT week, dc, sku, on_hand, LAG(on_hand) OVER (PARTITION BY dc, sku ORDER BY week) AS prev "
                          "FROM inventory) SELECT dc, sku FROM i WHERE prev > 0 AND on_hand < 0.2 * prev ORDER BY dc")["rows"]
    assert drops == [["BHW", "MP-1KG"], ["CHN", "MN-1L"], ["KOL", "MN-1L"]]   # two real stock-outs and one lie


# --- the guards ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("sql", ["DELETE FROM orders", "SELECT 1; DROP TABLE orders", "PRAGMA query_only = OFF",
                                 "WITH x AS (SELECT 1) DELETE FROM orders", "ATTACH DATABASE 'x.db' AS x",
                                 "UPDATE inventory SET on_hand = 9000", "select * from orders; -- ok"])
def test_lexical_guard_refuses(sql):
    if sql.endswith("-- ok"):
        assert check_sql(sql).lower().startswith("select")     # a trailing semicolon and comment are fine
        return
    with pytest.raises(SqlRefused):
        check_sql(sql)


def test_lexical_guard_has_false_positives():
    with pytest.raises(SqlRefused):                             # REPLACE() is a string function, refused anyway
        check_sql("SELECT REPLACE(name, ' ', '_') FROM dim_sku")


def test_query_only_refuses_writes_on_the_warehouse(conn):
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM orders")


def test_query_only_alone_can_be_switched_off_by_the_same_connection():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t (x)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    c.execute("PRAGMA query_only = ON")
    with pytest.raises(sqlite3.OperationalError):
        c.execute("DELETE FROM t")
    c.execute("PRAGMA query_only = OFF")                        # why the authorizer exists
    c.execute("DELETE FROM t")
    assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_authorizer_holds_with_the_word_list_switched_off(conn, monkeypatch):
    monkeypatch.setattr(warehouse, "check_sql", lambda s: s.strip().rstrip(";"))
    monkeypatch.setattr(guard, "ENABLED", False)              # both lexical layers off: the old list and guard.check
    d = Dispatcher(conn=conn)
    for sql in ("PRAGMA query_only = OFF", "DELETE FROM orders", "DROP TABLE orders", "CREATE TEMP TABLE t (x)"):
        out = d.run("run_sql", {"sql": sql})
        assert "not authorized" in out["error"], sql
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 3840
    ok = d.run("run_sql", {"sql": "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 5) SELECT COUNT(*) FROM n"})
    assert ok["rows"] == [[5]]                                  # reads, CTEs, recursion and window functions still work
    lag = d.run("run_sql", {"sql": "SELECT week, LAG(otif) OVER (ORDER BY week) FROM (SELECT week, AVG(on_time) AS otif FROM orders GROUP BY week)"})
    assert len(lag["rows"]) == 12 and lag["rows"][0][1] is None
    assert d.run("describe_table", {"name": "orders"})["rows"] == 3840     # PRAGMA table_info is allowed


def test_lock_down_is_idempotent(conn):
    lock_down(lock_down(conn))
    assert run_sql(conn, "SELECT COUNT(*) FROM dim_sku")["rows"] == [[10]]


def test_step_budget_stops_a_query_that_never_ends(conn):
    t0 = time.time()
    with pytest.raises(SqlRefused, match="step budget"):
        run_sql(conn, "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT COUNT(*) FROM n")
    assert time.time() - t0 < 5
    assert run_sql(conn, "SELECT COUNT(*) FROM orders")["rows"] == [[3840]]   # the connection is still usable


def test_row_limits_keep_200_and_show_the_model_50(conn):
    res = run_sql(conn, "SELECT * FROM orders")
    assert res["row_count"] == 200 and res["truncated"] is True
    out = Dispatcher(conn=conn).run("run_sql", {"sql": "SELECT * FROM orders"})
    assert len(out["rows"]) == 50 and out["truncated"] is True and out["rows_total_kept"] == 200


def test_dispatcher_reports_errors_without_raising(conn):
    d = Dispatcher(conn=conn)
    assert "no such column" in d.run("run_sql", {"sql": "SELECT otif FROM orders"})["error"]
    assert "no table" in d.run("describe_table", {"name": "sales"})["error"]
    assert "unknown metric" in d.run("metric", {"name": "happiness"})["error"]
    assert "weeks must be" in d.run("metric", {"name": "otif", "weeks": [3]})["error"]
    assert [e["ok"] for e in d.executed] == [False]             # only statements are recorded, not lookups
    every_dc = d.run("metric", {"name": "otif", "week": 12, "dc": "ALL"})["rows"]
    assert [r[1] for r in every_dc] == ["BHW", "CHN", "GGN", "KOL"] and math.isclose(every_dc[2][4], 0.75)
