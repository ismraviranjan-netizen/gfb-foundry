"""The coworker: writes the messages people actually read. Rules, not a model:

  * one message per person per batch, however many plans need them;
  * the clock that runs out first comes first, and the time is a wall-clock time, not "SLA 4 h";
  * every line says what to sign, why in one clause, and the money on both sides;
  * every line carries the plan id and the console link, so a reply or a click lands on the right plan.

The tower's clock: hour 0 is Monday 06:30 IST (the EventBridge cron), so hour 4 is Monday 10:30.
"""
from __future__ import annotations

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


LOCALE = {"start_hour": 6.5, "greet": "first", "brand": "GFB", "horizon_days": 14}   # the tower's clock, greeting, sign-off, radar horizon


def set_brand(brand: str = "GFB", horizon_days: int = 14) -> None:
    """The name a message signs off with ('- the GFB tower') and the radar horizon it quotes."""
    LOCALE["brand"] = brand or "GFB"
    LOCALE["horizon_days"] = int(horizon_days or 14)


def set_clock(start_hour: float = 6.5) -> None:
    """Hour 0 of the tower's clock in the company's local time: 6.5 for GFB (Monday 06:30 IST), 6.0 for a
    brewer whose cron fires at 06:00 CET. Labels stay 'Mon 06:30': the time zone lives in the profile."""
    LOCALE["start_hour"] = float(start_hour)


def set_greeting(mode: str = "first") -> None:
    """'first' greets 'Priya,'; 'full' greets the whole name, for a company whose people are role titles
    ('NL supply planner,') rather than invented names."""
    LOCALE["greet"] = "full" if mode == "full" else "first"


def greet(name: str) -> str:
    return name if LOCALE["greet"] == "full" else name.split()[0]


def clock_label(hours: float) -> str:
    total_min = int(round(LOCALE["start_hour"] * 60 + hours * 60))
    day, rem = divmod(total_min, 24 * 60)
    return f"{DAYS[day % 7]} {rem // 60:02d}:{rem % 60:02d}"


CURRENCY = {"code": "INR"}     # the runtime sets the company's currency (a brewer in Europe reads EUR or GBP)


def set_currency(code: str) -> None:
    CURRENCY["code"] = (code or "INR").upper()


def code() -> str:
    """The company's currency code, for a sentence that prints money with Western grouping ('EUR 18,400')."""
    return CURRENCY["code"]


def reset_locale() -> None:
    """Back to GFB's defaults (INR, 06:30, first names): what a test calls after running another company."""
    set_currency("INR")
    set_clock(6.5)
    set_greeting("first")
    set_brand("GFB", 14)


def inr(x: float | int | None) -> str:
    """Money in the company's currency: INR with Indian grouping (184229 -> INR 1,84,229), anything else
    with thousands grouping (EUR 184,229). The name stays inr() and money fields stay *_inr for history:
    they hold the company's currency, whatever it is."""
    if x is None:
        return "-"
    n = int(round(float(x)))
    if CURRENCY["code"] != "INR":
        return ("-" if n < 0 else "") + f"{CURRENCY['code']} {abs(n):,}"
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if n < 0 else "") + "INR " + s



def model_cost(cost_inr: float | None) -> str:
    """The model meter runs in INR; a company that keeps its books in another currency sees the AWS bill's USD."""
    if CURRENCY == "INR":
        return inr(cost_inr)
    from foundry.bedrock_client import INR_PER_USD
    return f"USD {(cost_inr or 0) / INR_PER_USD:,.2f}"

def approval_digest(name: str, items: list[dict], now: float, console_url: str) -> tuple[str, str]:
    """items: [{plan_id, step, code, text, priority, due_at, why, money_line, revised}] for one person."""
    items = sorted(items, key=lambda i: (i["due_at"], i["priority"]))
    first = greet(name)
    p1 = sum(1 for i in items if i["priority"] == "P1")
    n = len(items)
    kind = "P1 " if p1 == n else ""
    subject = (f"{first}, {n} {kind}sign-off{'s' if n != 1 else ''} before {clock_label(items[0]['due_at'])}"
               + (f" ({p1} P1)" if 0 < p1 < n else ""))
    lines = [f"{first},", "", f"The tower needs you on {len(items)} step{'s' if len(items) != 1 else ''}. Earliest clock first:", ""]
    for n, i in enumerate(items, 1):
        lines.append(f"{n}. [{i['priority']}] {i['text']}" + ("  (REVISED)" if i.get("revised") else ""))
        if i.get("why"):
            lines.append(f"   Why: {i['why']}")
        if i.get("money_line"):
            lines.append(f"   Money: {i['money_line']}")
        lines.append(f"   Due {clock_label(i['due_at'])}. Open: {console_url}/#plan/{i['plan_id']}  or reply APPROVE {i['plan_id']} / REJECT {i['plan_id']} <reason>")
        lines.append("")
    lines.append(f"Nothing moves until every named person on a plan has signed. - the {LOCALE['brand']} tower")
    return subject, "\n".join(lines)


def vp_digest(name: str, week: int, plans: list[dict], radar: dict | None, now: float, cost_inr: float) -> tuple[str, str]:
    live = [p for p in plans if p["status"] in ("READY", "PARTIAL")]
    done = [p for p in plans if p["status"] == "APPLIED"]
    p1 = [p for p in live if p["priority"] == "P1"]
    money = sum(p.get("impact_inr", 0) for p in live)
    first = greet(name)
    subject = f"Week {week} tower: {len(live)} plans waiting, {len(p1)} P1, {inr(money)} at stake"
    lines = [f"{first},", "", f"State of the tower at {clock_label(now)}:"]
    for p in sorted(live, key=lambda p: (p["priority"], p["due_at"])):
        who = ", ".join(sorted({s["approver"].split(' (')[0] for s in p["steps"] if s.get("decision") is None}))
        lines.append(f"  {p['priority']} {p['dc']} {p.get('sku') or 'all SKUs'}: {p['root_cause'].replace('_', ' ').lower()}, "
                     f"{inr(p.get('impact_inr'))}; waiting on {who or 'nobody'}; due {clock_label(p['due_at'])}")
    if done:
        lines.append(f"Applied so far: {len(done)} plan(s).")
    if radar:
        c = radar.get("counts", {})
        lines.append(f"Radar, next {LOCALE['horizon_days']} days: {c.get('RED', 0)} red, {c.get('AMBER', 0)} amber, {c.get('GREY', 0)} grey (stale data).")
    lines.append(f"The tower's model calls this week so far: {model_cost(cost_inr)}.")
    return subject, "\n".join(lines)
