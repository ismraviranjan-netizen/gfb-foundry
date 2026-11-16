"""The lexical guard, token-aware (8 Nov 2026). The agent's run_sql door uses this one.

warehouse.check_sql searches the whole statement for a word list. Three things go wrong with that:

  1. it refuses fair reads: REPLACE(sku, '-', '') is a string function; 'update later' and
     LIKE '%drop table%' are string literals; SELECT 'a;b' holds a semicolon inside quotes
  2. it strips "comments" with a regex that does not know about quotes, then runs the stripped text:
     LIKE '%--%' becomes a syntax error, and SELECT '/*' AS a, sku, '*/' AS b silently becomes
     SELECT ' ' AS b -- a different query, run without a word of warning
  3. it cannot tell a word in the code from the same word in the data

This guard reads the statement the way SQLite does. `mask` blanks the inside of every string literal and
quoted name and every comment (same length, so positions still line up); the checks read only what is
left, which is code. The statement that runs is the original text, cut after its last token: the guard
approves a query, it never rewrites one.

It is still a courtesy layer. The guards that make writes impossible are structural: PRAGMA query_only
on the connection and the read-only authorizer in tools.py, which no SQL can switch off. That is why the
word list can afford to be accurate instead of paranoid. ENABLED = False takes this layer away (the eval
runner's --no-lexical does it) to show the structural guards holding on their own.
"""
from __future__ import annotations

import re
import sqlite3
import time

from foundry.sqlx.warehouse import MAX_ROWS, SqlRefused

ENABLED = True

# the words that can only mean a write or a schema change in SQLite; REPLACE is a write only as a
# statement (REPLACE INTO, INSERT OR REPLACE), never when it is followed by "(" (the string function)
_WRITE = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|trigger|reindex|analyze)\b"
                    r"|\b(replace)\b(?!\s*\()", re.I)
_QUOTES = {"'": "'", '"': '"', "`": "`", "[": "]"}


def mask(sql: str) -> str:
    """The statement with every string literal and quoted name filled with x and every comment blanked.

    'update later' -> 'xxxxxxxxxxxx' ; "delete" -> "xxxxxx" ; -- note -> spaces ; /* ... */ -> spaces.
    A doubled quote inside quotes ('' or "" or ``) is an escaped quote, as in SQLite; backslash is not an
    escape. An unterminated quote is refused: SQLite would read the rest of the text differently from us.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch, nxt = sql[i], sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":                       # line comment, to the end of the line
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        if ch == "/" and nxt == "*":                       # block comment, to */ or the end of the text
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(" " * (j - i))
            i = j
            continue
        if ch in _QUOTES:
            close, j = _QUOTES[ch], i + 1
            while True:
                k = sql.find(close, j)
                if k == -1:
                    raise SqlRefused("unbalanced quotes: a string or a quoted name is not closed")
                if close != "]" and k + 1 < n and sql[k + 1] == close:
                    j = k + 2                              # an escaped quote: keep reading the literal
                    continue
                break
            out.append(ch + "x" * (k - i - 1) + close)
            i = k + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def check(sql: str) -> str:
    """The statement to run (the original text, trailing semicolons and comments cut) or SqlRefused."""
    m = mask(sql)
    end = len(m.rstrip())
    while end and m[end - 1] == ";":
        end = len(m[:end - 1].rstrip())
    code = m[:end]
    start = len(code) - len(code.lstrip())
    if ";" in code:
        raise SqlRefused("one statement only")
    if not re.match(r"(select|with)\b", code[start:], re.I):
        raise SqlRefused("read-only: a query must start with SELECT or WITH")
    w = _WRITE.search(code)
    if w:
        raise SqlRefused(f"read-only: '{w.group(1) or w.group(2)}' is not allowed")
    return sql[start:end]


def run_read(conn: sqlite3.Connection, sql: str, max_rows: int = MAX_ROWS, max_steps: int = 5_000_000) -> dict:
    """warehouse.run_sql with this guard in front: same step budget, same row limit, same result shape.
    (The execution core is repeated here because warehouse.run_sql always calls its own check_sql;
    folding `check` into warehouse.py is reported to its owner.)"""
    clean = check(sql) if ENABLED else sql.strip().rstrip(";").strip()
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
