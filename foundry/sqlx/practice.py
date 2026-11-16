"""Practise SQL on GFB's warehouse from the terminal, through the same read-only door the agent uses.

    python3 -m foundry.sqlx schema                         the tables, their columns and row counts
    python3 -m foundry.sqlx sql "SELECT ..."               run one query (or --file mine.sql)
    python3 -m foundry.sqlx metrics                        the semantic layer's catalogue
    python3 -m foundry.sqlx metric otif --week 12 --dc CHN --sku MN-1L
    python3 -m foundry.sqlx quality                        the data-quality checks (--store refreshed)
    python3 -m foundry.sqlx ask "What was OTIF at Chennai for MN-1L in week 12?"   the agent, scripted brain
    python3 -m foundry.sqlx drills [--chapter 7]           the drill deck
    python3 -m foundry.sqlx drill 7.3 [--check mine.sql | --key]

Stores: canonical (week 12 with the incidents, the default), refreshed (Kolkata's snapshot fixed), notes
(canonical plus the carrier_notes table with the poisoned row). Nothing here can write: the connection is
query_only with the read-only authorizer installed, and every statement passes guard.check first.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
from pathlib import Path

from foundry.sqlx import drills, guard, quality, semantic
from foundry.sqlx.tools import lock_down
from foundry.sqlx.warehouse import SqlRefused, build_warehouse

STORES = ("canonical", "refreshed", "notes")
_CACHE: dict[str, object] = {}


def store_for(kind: str = "canonical"):
    if kind not in STORES:
        raise ValueError(f"store must be one of {', '.join(STORES)}")
    if kind not in _CACHE:
        from foundry.data.generate import canonical_store
        if kind == "notes":
            from foundry.sqlx.traps import with_carrier_notes
            _CACHE[kind] = with_carrier_notes(canonical_store())
        else:
            _CACHE[kind] = canonical_store(refreshed=(kind == "refreshed"))
    return _CACHE[kind]


def open_warehouse(kind: str = "canonical") -> sqlite3.Connection:
    """A fresh read-only warehouse: query_only plus the authorizer."""
    return lock_down(build_warehouse(store_for(kind)))


def run(sql: str, kind: str = "canonical", conn: sqlite3.Connection | None = None, max_rows: int = 200) -> dict:
    """One statement through guard.check, the authorizer and the step budget. Errors come back as {'error': ...}."""
    c = conn or open_warehouse(kind)
    try:
        return guard.run_read(c, sql, max_rows=max_rows)
    except SqlRefused as e:
        return {"error": f"refused: {e}", "sql": sql}
    except sqlite3.Error as e:
        return {"error": f"sql error: {e}", "sql": sql}


# --- printing -------------------------------------------------------------------------------------------------------
def cell(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str) and v and not v.strip():
        return repr(v)                    # a string of spaces would print as nothing
    return str(v)


def table(columns: list[str], rows: list[list], max_rows: int = 20, width: int = 94) -> str:
    """An aligned text table: numbers to the right, text to the left, NULL spelled out."""
    shown = rows[:max_rows]
    cols = [str(c) for c in columns]
    cells = [[cell(v) for v in r] for r in shown]
    num = [all(isinstance(r[j], (int, float)) and not isinstance(r[j], bool) for r in shown if r[j] is not None)
           for j in range(len(cols))]
    w = [max([len(cols[j])] + [len(r[j]) for r in cells]) for j in range(len(cols))]
    while sum(w) + 2 * (len(w) - 1) > width and max(w) > 8:      # squeeze the widest text column, never a number
        j = max((k for k in range(len(w)) if not num[k]), key=lambda k: w[k], default=None)
        if j is None or w[j] <= 8:
            break
        w[j] -= 1
    def fit(s, j):
        s = s if len(s) <= w[j] else s[: w[j] - 1] + "~"
        return s.rjust(w[j]) if num[j] else s.ljust(w[j])
    lines = ["  ".join(fit(c, j) for j, c in enumerate(cols)).rstrip()]
    lines += ["  ".join(fit(c, j) for j, c in enumerate(r)).rstrip() for r in cells]
    if len(rows) > max_rows:
        lines.append(f"... {len(rows) - max_rows} more rows")
    return "\n".join(lines)


def result_text(res: dict, max_rows: int = 20) -> str:
    if res.get("error"):
        return res["error"]
    n = res["row_count"]
    more = " (cut at the row limit)" if res.get("truncated") else ""
    return table(res["columns"], res["rows"], max_rows) + f"\n({n} row{'s' if n != 1 else ''}{more})"


# --- checking an answer against a key ----------------------------------------------------------------------------------
def _eq(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) <= max(5e-4, 1e-4 * abs(float(b)))
    if a is None or b is None:
        return a is b
    return str(a).strip() == str(b).strip()


def _bag_eq(xs: list, ys: list) -> bool:
    left = list(ys)
    for x in xs:
        for i, y in enumerate(left):
            if (all(_eq(p, q) for p, q in zip(x, y)) if isinstance(x, tuple) else _eq(x, y)):
                left.pop(i)
                break
        else:
            return False
    return not left


def same_rows(key: dict, mine: dict, ordered: bool = False) -> tuple[bool, str]:
    """Every key column must match one of your columns by its values (names and order do not matter, extra columns
    are allowed); then the rows must match, in order when the drill says so."""
    if mine.get("error"):
        return False, mine["error"]
    kc, kr, mc, mr = key["columns"], key["rows"], mine["columns"], mine["rows"]
    if len(mr) != len(kr):
        return False, f"you returned {len(mr)} rows; the key returns {len(kr)}"
    cands = []
    for j, name in enumerate(kc):
        col = [r[j] for r in kr]
        cands.append([c for c in range(len(mc)) if _bag_eq(col, [r[c] for r in mr])])
        if not cands[-1]:
            return False, f"none of your columns holds the key's '{name}' values (first: {[cell(v) for v in col[:3]]})"
    for combo in itertools.islice(itertools.product(*cands), 5000):
        if len(set(combo)) != len(combo):
            continue
        proj = [tuple(r[c] for c in combo) for r in mr]
        gold = [tuple(r) for r in kr]
        if ordered and all(all(_eq(a, b) for a, b in zip(p, g)) for p, g in zip(proj, gold)):
            return True, "rows match, in order"
        if not ordered and _bag_eq(gold, proj):
            return True, "rows match"
    return False, "each column matches on its own but the rows do not line up" + (" in the key's order" if ordered else "")


def check(drill_id: str, sql: str) -> tuple[bool, str, dict, dict]:
    d = drills.DRILLS[drill_id]
    key = run(d["sql"], d["store"])
    mine = run(sql, d["store"])
    ok, why = same_rows(key, mine, d["ordered"])
    return ok, why, key, mine


# --- the command line -----------------------------------------------------------------------------------------------------
def _ask(args) -> int:
    from foundry.levers import Levers
    from foundry.profile import load_profile
    from foundry.sqlx import agent
    from foundry.sqlx.fake import FakeSqlClient
    import tempfile
    if args.live:
        from foundry.bedrock_client import BedrockClient
        client = BedrockClient()
    else:
        client = FakeSqlClient(unsafe=args.brain == "unsafe", gullible=args.brain == "gullible")
    with tempfile.TemporaryDirectory(prefix="t2d-ask-") as d:      # the levers' panel and audit, thrown away after
        tmp = Path(d)
        r = agent.run(args.question, store_for(args.store), load_profile("gfb"), client,
                      Levers(tmp / "panel.json", tmp / "audit.jsonl"), lexical=args.lexical)
    a = r.answer or {}
    flags = [f for f in ("needs_clarification", "refused", "out_of_scope") if a.get(f)]
    print(f"answer  {a.get('text') or r.stop_reason}")
    if a.get("clarifying_question"):
        print(f"asks    {a['clarifying_question']}")
    print(f"flags   {', '.join(flags) or 'none'}")
    print(f"tools   {' -> '.join(t['tool'] for t in r.tool_calls)}   stop {r.stop_reason}   "
          f"calls {r.meter.get('calls')}   INR {r.meter.get('cost_inr')}")
    for e in r.executed:
        print(f"ran     {'ok ' if e['ok'] else 'NO '} {e['sql'][:150]}" + ("" if e["ok"] else f"\n        -> {e['error']}"))
    if args.json:
        print(json.dumps(r.as_dict(), indent=1, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m foundry.sqlx", description="Practise SQL on GFB's warehouse.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("schema"); s.add_argument("--store", default="canonical", choices=STORES)
    s = sub.add_parser("sql"); s.add_argument("sql", nargs="?"); s.add_argument("--file")
    s.add_argument("--store", default="canonical", choices=STORES); s.add_argument("--rows", type=int, default=20)
    sub.add_parser("metrics")
    s = sub.add_parser("metric"); s.add_argument("name"); s.add_argument("--week", type=int); s.add_argument("--dc")
    s.add_argument("--sku"); s.add_argument("--weeks", type=int, nargs=2); s.add_argument("--late-days", type=float)
    s.add_argument("--store", default="canonical", choices=STORES)
    s = sub.add_parser("quality"); s.add_argument("--store", default="canonical", choices=STORES)
    s = sub.add_parser("ask"); s.add_argument("question"); s.add_argument("--store", default="canonical", choices=STORES)
    s.add_argument("--brain", default="safe", choices=["safe", "unsafe", "gullible"]); s.add_argument("--live", action="store_true")
    s.add_argument("--lexical", default="tokens", choices=["tokens", "strict"]); s.add_argument("--json", action="store_true")
    s = sub.add_parser("drills"); s.add_argument("--chapter")
    s = sub.add_parser("drill"); s.add_argument("id"); s.add_argument("--check"); s.add_argument("--key", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "schema":
        c = open_warehouse(args.store)
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
            cols = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]
            n = c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            print(f"{t:<14} {n:>5} rows  {', '.join(cols)}")
        return 0
    if args.cmd == "sql":
        sql = Path(args.file).read_text() if args.file else (args.sql or sys.stdin.read())
        res = run(sql, args.store)
        print(result_text(res, args.rows))
        return 1 if res.get("error") else 0
    if args.cmd == "metrics":
        for m in semantic.catalogue():
            print(f"{m['metric']:<16} {m['unit']:<12} {m['grain']:<34} {m['words']}")
        return 0
    if args.cmd == "metric":
        f = {k: v for k, v in (("week", args.week), ("dc", args.dc), ("sku", args.sku)) if v is not None}
        if args.weeks:
            f["weeks"] = tuple(args.weeks)
        if args.late_days is not None:
            f["late_days"] = args.late_days
        res = semantic.metric(open_warehouse(args.store), args.name, **f)
        print(res["sql"])
        print(result_text(res))
        return 0
    if args.cmd == "quality":
        res = quality.checks(open_warehouse(args.store))
        for r in res:
            print(f"{'FAIL' if r['rows_failing'] else 'ok  '}  {r['severity']:<6} {r['check']:<24} {r['rows_failing']:>3} row(s)"
                  + (f"  e.g. {r['sample'][0]}" if r["sample"] else ""))
        print(json.dumps(quality.summary(res)))
        return 0
    if args.cmd == "ask":
        return _ask(args)
    if args.cmd == "drills":
        for d in (drills.chapter(args.chapter) if args.chapter else drills.D):
            print(f"{d['id']:<6} {d['kind']:<8} {d['minutes']:>2} min  {d['title']}")
        return 0
    if args.cmd == "drill":
        d = drills.DRILLS.get(args.id)
        if d is None:
            print(f"no drill {args.id}; try: python3 -m foundry.sqlx drills")
            return 1
        if args.check:
            ok, why, _key, mine = check(args.id, Path(args.check).read_text())
            print(result_text(mine, 12))
            print(("PASS  " if ok else "NOT YET  ") + why)
            return 0 if ok else 1
        print(f"Drill {d['id']} · {d['kind']} · {d['title']} ({d['minutes']} min, store: {d['store']})\n{d['ask']}")
        if d["bad_sql"]:
            print("\n" + d["bad_sql"])
        if d["predict"]:
            print(f"\nPredict: {d['predict']}")
        if args.key:
            print("\n-- key\n" + d["sql"] + "\n" + result_text(run(d["sql"], d["store"])) + "\n\n" + d["why"])
        return 0
    return 1
