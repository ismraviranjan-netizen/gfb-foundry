"""Talk to Data, part 4: the drill deck and the practice CLI. Every key in the book runs here."""
import pytest

from foundry.sqlx import drills, guard, practice


@pytest.mark.parametrize("d", drills.D, ids=[d["id"] for d in drills.D])
def test_every_key_runs_read_only_and_matches_itself(d):
    assert guard.check(d["sql"])                              # the agent's own door accepts it
    res = practice.run(d["sql"], d["store"])
    assert not res.get("error"), res.get("error")
    assert res["row_count"] >= 1 and not res["truncated"]
    assert practice.same_rows(res, practice.run(d["sql"], d["store"]), d["ordered"])[0]   # deterministic
    assert all(len(line) < 92 for line in (d["sql"] + "\n" + (d["bad_sql"] or "")).splitlines())   # fits a page


@pytest.mark.parametrize("d", [d for d in drills.D if d["bad_sql"]], ids=[d["id"] for d in drills.D if d["bad_sql"]])
def test_every_broken_query_is_really_broken(d):
    key = practice.run(d["sql"], d["store"])
    bad = practice.run(d["bad_sql"], d["store"])
    assert not practice.same_rows(key, bad, d["ordered"])[0]


def test_the_deck_has_thirty_plus_part_one_drills_and_unique_ids():
    ids = [d["id"] for d in drills.D]
    assert len(ids) == len(set(ids))
    assert sum(1 for d in drills.D if int(d["id"].split(".")[0]) <= 9) >= 30


def test_pinned_numbers_the_book_quotes():
    v = lambda i: practice.run(drills.DRILLS[i]["sql"])["rows"]          # noqa: E731
    assert [r[-1] for r in v("3.2")] == [0.9375, 0.9, 0.75, 0.975]
    assert v("7.3") == [[12, "KOL", "MN-1L", 400, 9322, -8922]]
    assert v("8.3")[-1] == [12, 0.6667, 0.9792, 0.0386, -8.1]
    assert v("10.1") == [[0.6667, 1.0, 0.6473]]
    assert v("10.2") == [["BHW", 17, 0, 0], ["CHN", 14, 3, 2], ["GGN", 15, 1, 0], ["KOL", 11, 1, 0]]
    assert v("13.1") == [[0.3945, 0.4356]]
    assert v("26.10") == [["MN-1L", "KOL", 60.2, "CHN", 3.8]]
    assert "step budget" in practice.run(drills.DRILLS["6.4"]["bad_sql"])["error"]
    assert practice.run(drills.DRILLS["2.3"]["bad_sql"])["rows"] == [[1040]]


def test_same_rows_ignores_names_and_column_order_but_not_values():
    key = {"columns": ["dc", "otif"], "rows": [["CHN", 0.9], ["GGN", 0.75]]}
    assert practice.same_rows(key, {"columns": ["x", "dc_code", "v"], "rows": [[1, "GGN", 0.75], [2, "CHN", 0.9]]})[0]
    assert not practice.same_rows(key, {"columns": ["dc", "otif"], "rows": [["CHN", 0.9], ["GGN", 0.7]]})[0]
    assert not practice.same_rows(key, {"columns": ["dc", "otif"], "rows": [["GGN", 0.75], ["CHN", 0.9]]}, ordered=True)[0]
    assert "rows" in practice.same_rows(key, {"columns": ["dc"], "rows": [["CHN"]]})[1]


def test_cli_checks_an_answer_and_refuses_a_write(tmp_path, capsys):
    mine = tmp_path / "mine.sql"
    mine.write_text("SELECT dc AS site, ROUND(AVG(on_time * in_full), 4) AS v, COUNT(*) AS n, "
                    "SUM(on_time * in_full) AS ok FROM orders WHERE week = 12 GROUP BY dc")
    assert practice.main(["drill", "3.2", "--check", str(mine)]) == 0
    assert "PASS" in capsys.readouterr().out
    mine.write_text("SELECT dc, ROUND(AVG(on_time), 4) FROM orders WHERE week = 12 GROUP BY dc")
    assert practice.main(["drill", "3.2", "--check", str(mine)]) == 1
    assert practice.main(["sql", "DELETE FROM orders"]) == 1
    assert "refused" in capsys.readouterr().out
    assert practice.main(["sql", "SELECT REPLACE(sku, '-', '') AS code FROM dim_sku WHERE sku = 'MN-1L'"]) == 0
    assert "MN1L" in capsys.readouterr().out


def test_cli_ask_metric_and_quality_print(capsys):
    assert practice.main(["ask", "What was OTIF at Chennai for MN-1L in week 12?"]) == 0
    out = capsys.readouterr().out
    assert "0.6667" in out and "metric -> answer" in out
    assert practice.main(["metric", "late_trucks", "--week", "12", "--dc", "GGN"]) == 0
    assert "15" in capsys.readouterr().out
    assert practice.main(["quality"]) == 0
    assert '"failing": 3' in capsys.readouterr().out
