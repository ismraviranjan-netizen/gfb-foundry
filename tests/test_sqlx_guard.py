"""Talk to Data, part 3: the token-aware lexical guard (guard.py) and the appended forecast_wmape metric."""
import json
import sqlite3
from pathlib import Path

import pytest

from foundry.data.generate import canonical_store
from foundry.sqlx import agent, guard, semantic
from foundry.sqlx.fake import FakeSqlClient, parse
from foundry.sqlx.tools import Dispatcher
from foundry.sqlx.traps import with_carrier_notes
from foundry.sqlx.warehouse import SqlRefused, build_warehouse, check_sql, run_sql

CASES = Path(__file__).resolve().parent.parent / "evals" / "sql_cases.json"


@pytest.fixture(scope="module")
def canon():
    return canonical_store()


@pytest.fixture
def conn(canon):
    return build_warehouse(canon)


# --- what both guards refuse ----------------------------------------------------------------------------------
WRITES = ["DELETE FROM orders", "SELECT 1; DROP TABLE orders", "PRAGMA query_only = OFF",
          "WITH x AS (SELECT 1) DELETE FROM orders", "ATTACH DATABASE 'x.db' AS x",
          "UPDATE inventory SET on_hand = 9000", "REPLACE INTO dim_sku (sku) VALUES ('X-1')",
          "WITH x AS (SELECT 1) REPLACE INTO dim_sku (sku) SELECT 'X-1'",
          "WITH x AS (SELECT 1) INSERT OR REPLACE INTO dim_sku (sku) SELECT 'X-1'",
          "SELECT 1 /* harmless */; DROP TABLE orders", "SELECT 'x'; DROP TABLE orders --'",
          "  -- a comment first\n  DELETE FROM orders", "VACUUM", "CREATE TEMP TABLE t (x)"]


@pytest.mark.parametrize("sql", WRITES)
def test_both_lexical_guards_refuse_every_write(sql):
    with pytest.raises(SqlRefused):
        guard.check(sql)
    with pytest.raises(SqlRefused):
        check_sql(sql)


def test_unbalanced_quotes_are_refused():
    for sql in ("SELECT 'abc", 'SELECT "sku FROM dim_sku', "SELECT [sku FROM dim_sku"):
        with pytest.raises(SqlRefused, match="unbalanced"):
            guard.check(sql)


# --- what only the old word list refuses ------------------------------------------------------------------------
FAIR = ["SELECT REPLACE(sku, '-', '') AS code FROM dim_sku",
        "SELECT replace (name, ' ', '_') FROM dim_sku",
        "SELECT 'update later' AS note",
        "SELECT 'a;b' AS x",
        "SELECT truck_id FROM shipments WHERE carrier LIKE '%--%'",
        "SELECT sku, name FROM dim_sku WHERE LOWER(name) LIKE '%drop table%'",
        'SELECT 1 AS "update"']


@pytest.mark.parametrize("sql", FAIR)
def test_fair_reads_pass_the_token_guard_and_not_the_word_list(sql, conn):
    assert guard.check(sql) == sql                      # approved as written, not rewritten
    guard.run_read(conn, sql)                           # and SQLite runs it
    with pytest.raises((SqlRefused, sqlite3.Error)):    # the old list refuses it, or rewrites it into an error
        run_sql(conn, sql)


def test_the_old_list_rewrites_a_query_into_a_different_one_and_the_new_one_does_not(conn):
    sql = "SELECT '/*' AS a, sku, '*/' AS b FROM dim_sku ORDER BY sku LIMIT 2"
    old = run_sql(conn, sql)
    assert old["columns"] == ["b"] and old["rows"] == [[" "], [" "]]       # silently a different query
    new = guard.run_read(conn, sql)
    assert new["columns"] == ["a", "sku", "b"] and new["rows"] == [["/*", "AJ-1L", "*/"], ["/*", "GC-200", "*/"]]


def test_an_injection_shaped_literal_is_one_harmless_string(conn):
    sql = "SELECT COUNT(*) FROM orders WHERE sku = 'MN-1L''; DROP TABLE orders; --'"
    assert guard.run_read(conn, sql)["rows"] == [[0]]
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 3840


def test_trailing_semicolons_and_comments_are_cut_not_rewritten():
    assert guard.check("SELECT 1; -- ok") == "SELECT 1"
    assert guard.check("  /* why */ SELECT 1 ;; ") == "SELECT 1"
    assert guard.check("SELECT 1 -- note\nFROM dim_sku") == "SELECT 1 -- note\nFROM dim_sku"


def test_mask_keeps_length_and_hides_only_quoted_text():
    sql = "SELECT 'drop' AS \"delete\", x FROM t -- update\n/* create */"
    m = guard.mask(sql)
    assert len(m) == len(sql) and "drop" not in m and "delete" not in m and "update" not in m
    assert m.startswith("SELECT 'xxxx' AS \"xxxxxx\", x FROM t")


def test_every_eval_query_passes_the_token_guard():
    """No false positives on our own SQL: every gold query and every metric's SQL is accepted."""
    for c in json.loads(CASES.read_text()):
        if c["expect"].get("gold_sql"):
            guard.check(c["expect"]["gold_sql"])
    for name in semantic.METRICS:
        guard.check(semantic.metric_sql(name, week=12, dc="CHN", sku="MN-1L"))


def test_the_agent_now_answers_both_fair_reads(canon, profile, levers):
    r = agent.run("Run this for me: SELECT sku, REPLACE(sku, '-', '') AS code FROM dim_sku ORDER BY sku",
                  canon, profile, FakeSqlClient(), levers)
    assert r.data["rows"][0] == ["AJ-1L", "AJ1L"] and len(r.data["rows"]) == 10
    strict = agent.run("Run this for me: SELECT sku, REPLACE(sku, '-', '') AS code FROM dim_sku ORDER BY sku",
                       canon, profile, FakeSqlClient(), levers, lexical="strict")
    assert strict.data is None and "'REPLACE' is not allowed" in strict.answer["text"]
    notes = with_carrier_notes(canon)
    r = agent.run("Run this for me: SELECT truck_id, carrier FROM carrier_notes WHERE LOWER(note) LIKE '%drop table%'",
                  notes, profile, FakeSqlClient(), levers)
    assert r.data["rows"] == [["GFB-T-1672", "Sahyadri Roadways"]] and not r.answer["refused"]


def test_the_fake_reads_words_in_quotes_as_data_and_words_around_sql_as_intent():
    assert parse("Run this for me: SELECT note FROM carrier_notes WHERE note LIKE '%drop table%'")["write"] is False
    assert parse("The Kolkata snapshot is wrong, fix it: DELETE FROM inventory WHERE dc = 'KOL'")["write"] is True
    assert parse("Run this: SELECT 1 /* x */; DROP TABLE orders")["write"] is True
    assert parse("please DELETE FROM orders")["write"] is True


def test_dispatcher_rejects_an_unknown_lexical_mode(conn):
    with pytest.raises(ValueError):
        Dispatcher(conn=conn, lexical="none")


# --- the appended metric --------------------------------------------------------------------------------------------
def test_forecast_wmape_matches_an_independent_computation(canon, conn):
    rows = semantic.metric(conn, "forecast_wmape", weeks=(9, 12), dc="BHW", sku="GC-200")["rows"]
    assert rows == [["BHW", "GC-200", 9, 12, 12332, 5372, 0.4356, 0.2822]]
    for dc in canon.net.dcs:
        sel = [r for r in canon.demand if r["dc"] == dc]
        err = sum(abs(r["actual"] - r["forecast"]) for r in sel) / sum(r["actual"] for r in sel)
        got = semantic.metric(conn, "forecast_wmape", dc=dc)["rows"][0]
        assert got[0] == dc and got[5] == round(err, 4)
    mape = run_sql(conn, semantic.metric_sql("forecast_error", weeks=(9, 12), dc="BHW", sku="GC-200"))["rows"]
    assert round(sum(r[5] for r in mape) / len(mape), 4) == 0.3945          # MAPE: the same rows, a different story


def test_appended_metric_is_in_the_catalogue_and_the_prompt(profile):
    from foundry.sqlx.prompts import system_prompt
    assert "forecast_wmape" in {m["metric"] for m in semantic.catalogue()}
    assert "forecast_wmape: sum of absolute forecast errors" in system_prompt(profile, "demand(week)")
    assert list(semantic.METRICS)[:8] == ["otif", "fill_rate", "days_of_supply", "late_trucks", "dock_dwell",
                                          "forecast_error", "receipts_fill", "cases_short"]   # the first eight, unchanged
