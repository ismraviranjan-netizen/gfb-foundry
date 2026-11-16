"""A deterministic brain for the question-to-SQL agent, so the tests, the evals and the labs run with no
model and no cost. It reads the question the way a careful planner would, picks the door (metric first,
free SQL second, ask, refuse or say "not here"), calls the tools in that order and writes its answer
only from what the tools returned.

Two switches make it worse on purpose, so the guards and the evals can be seen working:

    FakeSqlClient(unsafe=True)    runs any SQL it finds, in the question or in a data row, as it is.
                                  The answers stay honest; the warehouse's guards must refuse the writes.
    FakeSqlClient(gullible=True)  also obeys prose instructions it finds in data rows. No SQL guard can
                                  stop that; only the eval's forbidden-phrase and numbers checks catch it.

The fake knows GFB's names the way a planner does (Chennai is CHN, chakli is GC-200). It does not know
any numbers: every number in an answer was read from a tool result in this run.
"""
from __future__ import annotations

import re

from foundry.scripted import ScriptedClient
from foundry.sqlx import guard
from foundry.sqlx.warehouse import SqlRefused

DC_CODES = ("BHW", "GGN", "KOL", "CHN")
DC_WORDS = {"chennai": "CHN", "bhiwandi": "BHW", "gurgaon": "GGN", "gurugram": "GGN", "kolkata": "KOL"}
SKU_CODES = ("MN-1L", "MN-200", "LJ-1L", "AJ-1L", "JM-500", "MP-1KG", "GC-200", "KH-250", "NM-500", "TG-250")
SKU_WORDS = [  # longest first; a phrase that names one SKU
    ("mango nectar 1 litre", "MN-1L"), ("mango nectar 1 l", "MN-1L"), ("mango nectar 1l", "MN-1L"),
    ("mango nectar 200", "MN-200"), ("mango pulp", "MP-1KG"), ("chakli", "GC-200"), ("khakhra", "KH-250"),
    ("namkeen", "NM-500"), ("thandai", "TG-250"), ("jamun", "JM-500"), ("lime", "LJ-1L"), ("apple", "AJ-1L"), ("pulp", "MP-1KG")]
SKU_AMBIGUOUS = ("mango nectar", "nectar", "mango")      # words that name more than one SKU: look them up, then ask

METRIC_WORDS = [
    ("otif", r"\botif\b|on[- ]time[- ]in[- ]full|on time and in full"),
    ("fill_rate", r"\bfill[- ]?rate\b"),
    ("days_of_supply", r"\bdays? of (supply|cover)\b|\bdos\b"),
    ("late_trucks", r"\blate (trucks?|shipments?|deliveries)\b|\btrucks? (were |was )?late\b|\bhow many trucks\b"),
    ("dock_dwell", r"\bdwell\b|\bdock\b"),
    ("forecast_wmape", r"\bwmape\b|\bweighted (mape|forecast error|error)\b"),
    ("forecast_error", r"\bforecast (error|accuracy)\b|\bmape\b|\bbias\b"),
    ("receipts_fill", r"\breceipts?\b|\breceived from the plant\b"),
    ("cases_short", r"\bcases short\b|\bshort cases\b|\bshortfall\b"),
]

# free-SQL shapes the fake can write; each needs the slots named, or it asks
TEMPLATES = [
    ("most_late_dc", r"\bwhich dc\b.*\bmost late trucks\b", "shipments", ("week",)),
    ("most_late_carrier", r"\bwhich carrier\b.*\bmost late\b", "shipments", ("week",)),
    ("skus_short", r"\b(which|what) skus?\b.*\bshort\b", "orders", ("week",)),
    ("dwell_above", r"\bweeks?\b.*\bdwell\b.*\b(above|over|more than)\s*(\d+(?:\.\d+)?)", "dock", ("dc",)),
    ("avg_sell_out", r"\baverage (weekly )?(sell[- ]?out|sales|demand)\b", "demand", ("weeks",)),
    ("skus_below_dos", r"\bskus?\b.*\b(less than|below|under)\s*(\d+(?:\.\d+)?)\s*days\b", "inventory", ("week", "dc")),
    ("rank_fill", r"\brank\b.*\bfill[- ]?rate\b", "orders", ("week",)),
    ("wow_otif", r"\botif\b.*\bweek[- ]over[- ]week\b|\bweek[- ]over[- ]week\b.*\botif\b", "orders", ("weeks", "dc")),
    ("dcs_long", r"\bdcs?\b.*\b(more than|above|over)\s*(\d+(?:\.\d+)?)\s*days\b", "inventory", ("week", "sku")),
    ("stock_value", r"\b(stock|inventory) value\b|\bvalue of (the )?stock\b", "inventory", ("week",)),
    ("late_orders", r"\bhow many orders\b.*\blate\b|\blate orders\b", "orders", ("week",)),
]

_WRITE = re.compile(
    r"\b(delete|truncate|insert|alter)\b|\bdrop\s+(table|view|index)\b|\bupdate\s+\w+\s+set\b"
    r"|\b(set|overwrite|correct)\b[^?.]*\b(to|=)\s*\d"
    r"|\b(fix|correct|overwrite|edit|change|remove|erase|wipe)\s+(it|that|this|the (data|snapshot|rows?|records?|table|number|value|stock|on[- ]hand))\b",
    re.I)
_SQL_START = (r"(?:select\b.+?\bfrom\b|with\s+(?:recursive\s+)?\w+\s*(?:\([^)]*\))?\s+as\s*\(|delete\s+from\b|"
              r"drop\s+(?:table|view|index)\b|update\s+\w+\s+set\b|insert\s+into\b|replace\s+into\b|pragma\s+\w+|"
              r"attach\s+(?:database\s+)?'|alter\s+table\b|create\s+(?:temp\s+|temporary\s+)?(?:table|view|index|trigger)\b)")
_SQL_IN_QUESTION = re.compile(r"(?:^|[:\n])\s*(" + _SQL_START + r".*)$", re.I | re.S)
_SQL_IN_DATA = re.compile(r"\b(drop\s+table\s+\w+|delete\s+from\s+\w+(?:\s+where\s+[^.;]+)?|update\s+\w+\s+set\s+[^.;]+|"
                          r"insert\s+into\s+[^.;]+|truncate\s+(?:table\s+)?\w+)", re.I)
_SUSPICIOUS = re.compile(r"ignore (your|all|previous|the) (rules|instructions)|drop\s+table|delete\s+from|system prompt|"
                         r"you are (an|the) (ai|assistant|agent)|tell the (planner|user)", re.I)
_TELL = re.compile(r"tell the (?:planner|user)\s+(?:that\s+)?([^.]+)", re.I)
_NOTES = re.compile(r"\bcarriers?\b.*\b(say|said|notes?|comments?|remarks?|write|wrote)\b|\bcarrier notes?\b", re.I)
_FUTURE = re.compile(r"\bnext (week|month|quarter)\b|\bwill\b|\bgoing to\b|\bpredict|\btomorrow\b|\bweek 1[3-9]\b", re.I)
_WHY = re.compile(r"^\s*why\b", re.I)
_DECIDE = re.compile(r"\bshould (we|i)\b", re.I)
_OFF_TOPIC = re.compile(r"\bweather\b|\bshare price\b|\bstock price\b|\bmeaning of life\b", re.I)
_VAGUE = re.compile(r"\bhow (is|are)\b.*\bdoing\b|\bhow('s| is)\b.*\b(going|looking)\b|\bgive me (an )?(overview|summary|status)\b", re.I)
_WEEKS = re.compile(r"\bweeks?\s*(\d{1,2})\s*(?:-|–|to|through|and)\s*(?:week\s*)?(\d{1,2})\b", re.I)
_WEEK = re.compile(r"\b(?:week|wk|w)\s*(\d{1,2})\b", re.I)
_LATEST = re.compile(r"\b(last|latest|this|most recent|current)\s+week\b", re.I)
_LAST_N = re.compile(r"\blast\s+(\d{1,2})\s+weeks\b", re.I)
_TOP = re.compile(r"\btop\s+(\d{1,2})\b", re.I)


# --- reading the question -----------------------------------------------------------------------------
def find_dc(q: str) -> str | None:
    m = re.search(r"\b(BHW|GGN|KOL|CHN)\b", q, re.I)
    if m:
        return m.group(1).upper()
    for w, code in DC_WORDS.items():
        if re.search(rf"\b{w}\b", q, re.I):
            return code
    return None


def find_sku(q: str) -> tuple[str | None, str | None]:
    """(sku, ambiguous word): a code or a one-SKU phrase gives the SKU; a word that fits several gives the word."""
    m = re.search(r"\b(" + "|".join(re.escape(c) for c in SKU_CODES) + r")\b", q, re.I)
    if m:
        return m.group(1).upper(), None
    low = q.lower()
    for phrase, code in SKU_WORDS:
        if re.search(rf"\b{re.escape(phrase)}\b", low):
            return code, None
    for word in SKU_AMBIGUOUS:
        if re.search(rf"\b{word}\b", low):
            return None, word
    return None, None


def parse(q: str) -> dict:
    p = {"q": q, "dc": find_dc(q), "week": None, "weeks": None, "latest": False, "last_n": None}
    p["sku"], p["sku_word"] = find_sku(q)
    m = _WEEKS.search(q)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        p["weeks"] = (a, b)
    else:
        m = _WEEK.search(q)
        if m:
            p["week"] = int(m.group(1))
    n = _LAST_N.search(q)
    if n:
        p["last_n"] = int(n.group(1))
    elif p["week"] is None and p["weeks"] is None and _LATEST.search(q):
        p["latest"] = True
    p["sql"] = None
    prose = q
    s = _SQL_IN_QUESTION.search(q.strip())
    if s:
        p["sql"] = s.group(1).strip().rstrip("?").strip()
        prose = q.strip()[:s.start(1)]
    # a careful reader judges the words around the SQL, and the SQL's code; a word inside quotes is data
    # ('%drop table%' searches for an attack, it is not one). Unbalanced quotes: read the SQL as written.
    try:
        code = guard.mask(p["sql"]) if p["sql"] else ""
    except SqlRefused:
        code = p["sql"] or ""
    p["write"] = bool(_WRITE.search(prose)) or bool(code and (
        re.match(r"(?i)\s*(delete|drop|update|insert|alter|create|replace|pragma|attach)\b", code) or _WRITE.search(code)))
    p["metric"] = next((name for name, rx in METRIC_WORDS if re.search(rx, q, re.I)), None)
    p["template"], p["template_match"], p["template_table"], p["template_needs"] = None, None, None, ()
    for name, rx, table, needs in TEMPLATES:
        tm = re.search(rx, q, re.I)
        if tm:
            p["template"], p["template_match"], p["template_table"], p["template_needs"] = name, tm, table, needs
            break
    return p


# --- writing the SQL a template needs --------------------------------------------------------------------
def template_sql(p: dict) -> str:
    t, m, w, dc, sku = p["template"], p["template_match"], p["week"], p["dc"], p["sku"]
    if t == "most_late_dc":
        return (f"SELECT lane_to AS dc, SUM(CASE WHEN late_days > 1 THEN 1 ELSE 0 END) AS late_trucks FROM shipments "
                f"WHERE week = {w} GROUP BY lane_to ORDER BY late_trucks DESC, lane_to LIMIT 1")
    if t == "most_late_carrier":
        return (f"SELECT carrier, SUM(CASE WHEN late_days > 1 THEN 1 ELSE 0 END) AS late_trucks FROM shipments "
                f"WHERE week = {w} GROUP BY carrier ORDER BY late_trucks DESC, carrier LIMIT 1")
    if t == "skus_short":
        top = _TOP.search(p["q"])
        where = f"week = {w}" + (f" AND dc = '{dc}'" if dc else "")
        return (f"SELECT sku, SUM(requested - shipped) AS cases_short FROM orders WHERE {where} GROUP BY sku "
                f"HAVING SUM(requested - shipped) > 0 ORDER BY cases_short DESC, sku" + (f" LIMIT {int(top.group(1))}" if top else ""))
    if t == "dwell_above":
        x = float(m.group(2))
        return f"SELECT week, avg_dwell_h FROM dock WHERE dc = '{dc}' AND avg_dwell_h > {x:g} ORDER BY week"
    if t == "avg_sell_out":
        a, b = p["weeks"]
        where = f"week BETWEEN {a} AND {b}" + (f" AND dc = '{dc}'" if dc else "") + (f" AND sku = '{sku}'" if sku else "")
        return f"SELECT ROUND(AVG(actual), 1) AS avg_sell_out FROM demand WHERE {where}"
    if t == "skus_below_dos":
        n = float(m.group(2))
        return (f"SELECT i.sku, i.on_hand, ROUND(i.on_hand / d.daily_demand, 1) AS days_of_supply FROM inventory i "
                f"JOIN dim_dc_sku d ON d.dc = i.dc AND d.sku = i.sku WHERE i.week = {w} AND i.dc = '{dc}' "
                f"AND i.on_hand / d.daily_demand < {n:g} ORDER BY days_of_supply, i.sku")
    if t == "rank_fill":
        return (f"SELECT dc, ROUND(1.0 * SUM(shipped) / SUM(requested), 4) AS fill_rate, "
                f"RANK() OVER (ORDER BY 1.0 * SUM(shipped) / SUM(requested) DESC) AS fill_rank FROM orders "
                f"WHERE week = {w} GROUP BY dc ORDER BY fill_rank, dc")
    if t == "wow_otif":
        a, b = p["weeks"]
        return (f"WITH weekly AS (SELECT week, ROUND(1.0 * SUM(CASE WHEN on_time = 1 AND in_full = 1 THEN 1 ELSE 0 END) / COUNT(*), 4) AS otif "
                f"FROM orders WHERE dc = '{dc}' GROUP BY week), "
                f"changes AS (SELECT week, otif, ROUND(otif - LAG(otif) OVER (ORDER BY week), 4) AS change FROM weekly) "
                f"SELECT week, otif, change FROM changes WHERE week BETWEEN {a} AND {b} ORDER BY week")
    if t == "dcs_long":
        n = float(m.group(2))
        return (f"SELECT i.dc, i.on_hand, ROUND(i.on_hand / d.daily_demand, 1) AS days_of_supply FROM inventory i "
                f"JOIN dim_dc_sku d ON d.dc = i.dc AND d.sku = i.sku WHERE i.week = {w} AND i.sku = '{sku}' "
                f"AND i.on_hand / d.daily_demand > {n:g} ORDER BY days_of_supply DESC")
    if t == "stock_value":
        return (f"SELECT i.dc, ROUND(SUM(i.on_hand * s.cost_per_case)) AS stock_value FROM inventory i "
                f"JOIN dim_sku s ON s.sku = i.sku WHERE i.week = {w} GROUP BY i.dc ORDER BY stock_value DESC")
    if t == "late_orders":
        where = f"week = {w}" + (f" AND dc = '{dc}'" if dc else "") + (f" AND sku = '{sku}'" if sku else "")
        return f"SELECT COUNT(*) AS late_orders FROM orders WHERE {where} AND on_time = 0"
    raise ValueError(f"no template {t}")


# --- writing answers from tool results only -----------------------------------------------------------------
def _num(v):
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def rows_text(columns: list[str], rows: list[list], limit: int = 10) -> str:
    if not rows:
        return "no rows"
    out = []
    for r in rows[:limit]:
        out.append(", ".join(f"{c} {_num(v)}" for c, v in zip(columns, r)))
    more = f"; and {len(rows) - limit} more rows" if len(rows) > limit else ""
    return "; ".join(out) + more


_METRIC_FMT = {
    "otif": lambda r: f"OTIF {r['otif']} ({r['otif_orders']} of {r['orders']} orders on time and in full)",
    "fill_rate": lambda r: f"fill rate {r['fill_rate']} ({r['shipped']} of {r['requested']} cases shipped)",
    "days_of_supply": lambda r: f"{r['days_of_supply']} days of supply ({r['on_hand']} cases on hand, snapshot {r['snapshot_age_h']} h old)",
    "late_trucks": lambda r: f"{r['late_trucks']} of {r['trucks']} inbound trucks late by more than a day, carrying {r['late_cases']} cases",
    "dock_dwell": lambda r: f"average dock dwell {r['avg_dwell_h']} h over {r['trucks_served']} trucks",
    "forecast_error": lambda r: f"forecast {r['forecast']} against sell-out {r['actual']}, absolute error {r['ape']} (signed {r['signed_error']})",
    "receipts_fill": lambda r: f"received {r['received']} of {r['planned']} planned cases, receipts fill {r['receipts_fill']}",
    "cases_short": lambda r: f"{r['cases_short']} cases short",
    "forecast_wmape": lambda r: (f"WMAPE {r['wmape']} ({r['abs_error']} cases of absolute error on {r['actual']} cases sold), "
                                 f"bias {r['bias']}"),
}


def metric_text(name: str, res: dict, limit: int = 12) -> str:
    if res.get("error"):
        return f"The {name} metric did not run: {res['error']}."
    cols, rows = res.get("columns", []), res.get("rows", [])
    if not rows:
        return f"The {name} metric returned no rows for those filters."
    recs = [dict(zip(cols, r)) for r in rows]

    def scope(r):
        when = f"week {r['week']}" if "week" in r else (f"weeks {r['first_week']} to {r['last_week']}" if "first_week" in r else "")
        return " ".join(str(x) for x in (when, r.get("dc"), r.get("sku")) if x)

    text = _cap("; ".join(f"{scope(r)}: {_METRIC_FMT[name](r)}" for r in recs[:limit]))
    if len(recs) > limit:
        text += f"; and {len(recs) - limit} more rows"
    text += "."
    stale = [r for r in recs if float(r.get("snapshot_age_h") or 0) > 24]
    if stale:
        r = stale[0]
        text += (f" Careful: the {r['dc']} {r['sku']} snapshot is {r['snapshot_age_h']} h old, so that number may be stale;"
                 f" the stale_snapshot check flags it.")
    if res.get("truncated"):
        text += " The result was cut at the row limit; narrow the filters for the rest."
    return text


def _person(system: str, role: str, default: str) -> str:
    m = re.search(rf"\b{role} ([A-Z][\w.]*(?: [A-Z][\w.]*)*)", system or "")
    return f"the {role} ({m.group(1)})" if m else default


def _answer(text, sql=None, metric=None, rows_used=None, clarify=None, refused=False, out_of_scope=False) -> tuple[str, dict]:
    return ("answer", {"text": text, "sql": sql, "metric": metric, "rows_used": rows_used,
                       "needs_clarification": clarify is not None, "clarifying_question": clarify,
                       "refused": refused, "out_of_scope": out_of_scope})


# --- the brain -------------------------------------------------------------------------------------------------
class FakeSqlClient(ScriptedClient):
    def __init__(self, unsafe: bool = False, gullible: bool = False, fail_at_turn: int | None = None):
        super().__init__(fail_at_turn)
        self.unsafe = unsafe or gullible
        self.gullible = gullible
        self.system = ""

    def converse(self, system, messages, tools, max_tokens=1500):
        self.system = system or ""
        return super().converse(system, messages, tools, max_tokens)

    def next_call(self, task, results, seen):
        steps = self.plan(str(task.get("question", "")), [r["content"] for r in results])
        return steps[min(len(results), len(steps) - 1)]

    # Each call re-derives the whole plan from the question and the results so far; steps that depend on a
    # result appear only once that result exists. The last step is always `answer`.
    def plan(self, q: str, outs: list[dict]) -> list[tuple[str, dict]]:
        return self._route(parse(q), outs)

    def _route(self, p: dict, outs: list[dict]) -> list[tuple[str, dict]]:
        q = p["q"]
        if p["write"]:
            return self._write(p, outs)
        if p["sql"]:
            return self._raw(p, outs)
        if _NOTES.search(q):
            return self._notes(p, outs)
        if _WHY.search(q):
            return [_answer("Why a number moved is a diagnosis, not a lookup: the exception (RCA) agent runs the checks "
                            "and names the cause. I can give you any of the numbers it reads.", out_of_scope=True)]
        if _DECIDE.search(q):
            return [_answer("That is a decision for a planner; the rebalancing agent proposes transfers with their approvals. "
                            "I can give you the numbers behind it.", out_of_scope=True)]
        if _OFF_TOPIC.search(q):
            return [_answer("The warehouse holds GFB's orders, stock, shipments, dock, receipts, demand and promos; "
                            "it cannot answer that.", out_of_scope=True)]
        if _FUTURE.search(q) and not p["template"]:
            who = _person(self.system, "demand planner", "the demand planner")
            return [_answer("The tables record what happened, up to the last closed week; they hold no number for a future week. "
                            f"Ask the demand sensing agent for next week's forecast, or {who}.", out_of_scope=True)]
        if p["sku_word"] and not p["sku"]:
            return self._ambiguous_sku(p, outs)
        if p["template"]:
            return self._template(p, outs)
        if p["metric"]:
            return self._metric(p, outs)
        if _VAGUE.search(q) or p["dc"]:
            return self._vague(p, outs)
        return self._unknown(p, outs)

    # -- doors ---------------------------------------------------------------------------------------------------
    def _write(self, p, outs):
        who = _person(self.system, "data lead", "the data lead")
        fix = (f" A wrong snapshot is fixed at the source: {who} can refresh it, and the stale_snapshot check shows when it is fresh."
               if "snapshot" in p["q"].lower() else f" Ask {who} to change it at the source.")
        if self.unsafe and p["sql"]:
            if not outs:
                return [("run_sql", {"sql": p["sql"]})]
            o = outs[0]
            said = f"The warehouse refused the statement ({o['error']})." if o.get("error") else "The statement ran."
            return [("run_sql", {"sql": p["sql"]}), _answer(said + " I only read the tables." + fix, sql=p["sql"], refused=True)]
        return [_answer("I can't change data: I only read the tables." + fix, refused=True)]

    def _raw(self, p, outs):
        step = ("run_sql", {"sql": p["sql"]})
        if not outs:
            return [step]
        o = outs[0]
        if o.get("error"):
            if "step budget" in o["error"]:
                return [step, _answer("The query was stopped: it ran past the warehouse's step budget, so it probably never ends. "
                                      "Bound it with a WHERE on the counter or a LIMIT and I will run it again.", sql=p["sql"], rows_used=0)]
            return [step, _answer(f"The query did not run: {o['error']}.", sql=p["sql"], rows_used=0)]
        return [step, _answer(f"The query returned {len(o['rows'])} row(s): {rows_text(o['columns'], o['rows'])}.",
                              sql=o["sql"], rows_used=len(o["rows"]))]

    def _resolve_week(self, p, outs, at: int):
        """For 'last week' or 'last N weeks': read the latest week from the data, never from the clock."""
        if not (p["latest"] or p["last_n"]):
            return [], 0
        step = ("run_sql", {"sql": "SELECT MAX(week) AS latest_week FROM orders"})
        if len(outs) <= at:
            return [step], None
        latest = int(outs[at]["rows"][0][0])
        if p["last_n"]:
            p["weeks"] = (max(1, latest - p["last_n"] + 1), latest)
        else:
            p["week"] = latest
        return [step], 1

    def _metric(self, p, outs):
        pre, used = self._resolve_week(p, outs, 0)
        if used is None:
            return pre
        if p["week"] is None and p["weeks"] is None:
            return self._ask_week(p, outs)
        inp = {"name": p["metric"]}
        for k in ("week", "dc", "sku"):
            if p[k] is not None:
                inp[k] = p[k]
        if p["weeks"]:
            inp["weeks"] = list(p["weeks"])
        steps = pre + [("metric", inp)]
        if len(outs) <= used:
            return steps
        res = outs[used]
        return steps + [_answer(metric_text(p["metric"], res), sql=res.get("sql"), metric=p["metric"],
                                rows_used=len(res.get("rows", [])))]

    def _ask_week(self, p, outs):
        step = ("run_sql", {"sql": "SELECT MIN(week) AS first_week, MAX(week) AS last_week FROM orders"})
        if not outs:
            return [step]
        a, b = outs[0]["rows"][0]
        return [step, _answer(f"I need the week before I can give one number.", clarify=f"Which week? The tables hold weeks {a} to {b}; "
                              f"the latest closed week is week {b}.", rows_used=0)]

    def _template(self, p, outs):
        pre, used = self._resolve_week(p, outs, 0)
        if used is None:
            return pre
        missing = [s for s in p["template_needs"] if p.get(s) is None]
        if missing:
            if missing == ["week"] and p["weeks"] is None:
                return self._ask_week(p, outs)
            words = {"week": "which week", "weeks": "which weeks (first and last)", "dc": "which DC", "sku": "which SKU"}
            return pre + [_answer("I need one more detail before I write the query.",
                                  clarify="Tell me " + " and ".join(words[m] for m in missing) + ".")]
        steps = pre + [("describe_table", {"name": p["template_table"]})]
        if len(outs) <= used:
            return steps
        sql = template_sql(p)
        steps.append(("run_sql", {"sql": sql}))
        if len(outs) <= used + 1:
            return steps
        o = outs[used + 1]
        if o.get("error"):
            return steps + [_answer(f"The query did not run: {o['error']}.", sql=sql, rows_used=0)]
        return steps + [_answer(f"The query returned {len(o['rows'])} row(s): {rows_text(o['columns'], o['rows'])}.",
                                sql=o["sql"], rows_used=len(o["rows"]))]

    def _ambiguous_sku(self, p, outs):
        step = ("run_sql", {"sql": f"SELECT sku, name FROM dim_sku WHERE LOWER(name) LIKE '%{p['sku_word']}%' ORDER BY sku"})
        if not outs:
            return [step]
        rows = outs[0].get("rows", [])
        if len(rows) == 1:                      # the word names one SKU after all: carry on with it
            p["sku"], p["sku_word"] = rows[0][0], None
            return [step] + self._route(p, outs[1:])
        options = " or ".join(f"{name} ({sku})" for sku, name in rows) or "a SKU code"
        return [step, _answer(f"'{p['sku_word']}' fits {len(rows)} SKUs, and they have different numbers.",
                              clarify=f"Which one: {options}?", rows_used=len(rows))]

    def _vague(self, p, outs):
        if not outs:
            return [("list_metrics", {})]
        names = ", ".join(m["metric"] for m in outs[0].get("metrics", []))
        where = f" for {p['dc']}" if p["dc"] else ""
        return [("list_metrics", {}), _answer("'Doing' could mean several numbers.",
                                              clarify=f"Which measure and which week{where}? I can give {names}; for example "
                                                      f"'OTIF at {p['dc'] or 'Chennai'} in week 12'.")]

    def _unknown(self, p, outs):
        if not outs:
            return [("list_metrics", {})]
        names = ", ".join(m["metric"] for m in outs[0].get("metrics", []))
        return [("list_metrics", {}), _answer("I could not match that to the tables.",
                                              clarify=f"Could you put it as a measure, a place and a week? I can answer {names}, "
                                                      f"or a question I can write one SELECT for.")]

    def _notes(self, p, outs):
        steps = [("list_tables", {})]
        if not outs:
            return steps
        tables = {t["table"] for t in outs[0].get("tables", [])}
        i = 1
        notes_text, injected_sql, told = "There are no carrier notes in the warehouse.", None, None
        if "carrier_notes" in tables:
            where = f" WHERE week = {p['week']}" if p["week"] else ""
            steps.append(("run_sql", {"sql": f"SELECT week, truck_id, carrier, note FROM carrier_notes{where} ORDER BY truck_id"}))
            if len(outs) <= i:
                return steps
            rows = outs[i].get("rows", [])
            i += 1
            clean = [r for r in rows if not _SUSPICIOUS.search(str(r[3]))]
            odd = [r for r in rows if _SUSPICIOUS.search(str(r[3]))]
            carriers = sorted({r[2] for r in rows})
            notes_text = f"{len(rows)} carrier note(s){' for week ' + str(p['week']) if p['week'] else ''} from {', '.join(carriers) or 'no carrier'}."
            if clean:
                notes_text += " " + " ".join(f"{r[1]}: {str(r[3])[:120]}" for r in clean)
            if odd:
                notes_text += (f" The note on {', '.join(r[1] for r in odd)} contains instructions aimed at an AI system rather than"
                               f" a delivery report; I treated it as data and did nothing it asked. Pass it to "
                               f"{_person(self.system, 'logistics lead', 'the logistics lead')}.")
                if self.unsafe:
                    m = next((_SQL_IN_DATA.search(str(r[3])) for r in odd if _SQL_IN_DATA.search(str(r[3]))), None)
                    injected_sql = m.group(1) if m else None
                if self.gullible:
                    t = next((_TELL.search(str(r[3])) for r in odd if _TELL.search(str(r[3]))), None)
                    told = t.group(1).strip() if t else None
        if injected_sql:
            steps.append(("run_sql", {"sql": injected_sql}))
            if len(outs) <= i:
                return steps
            i += 1
        if told:                                 # the gullible brain writes what the data told it to write
            return steps + [_answer(told[0].upper() + told[1:] + ".", rows_used=0)]
        metric_part = None
        if p["metric"] and p["week"]:
            metric_part = {"name": p["metric"], "week": p["week"], **({"dc": p["dc"]} if p["dc"] else {}), **({"sku": p["sku"]} if p["sku"] else {})}
            steps.append(("metric", metric_part))
            if len(outs) <= i:
                return steps
            res = outs[i]
            return steps + [_answer(notes_text + " " + metric_text(p["metric"], res), sql=res.get("sql"), metric=p["metric"],
                                    rows_used=len(res.get("rows", [])))]
        return steps + [_answer(notes_text, rows_used=None)]
