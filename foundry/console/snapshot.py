"""Run the three storylines and write a console that needs no server: python -m foundry.console.snapshot

Writes foundry/console/static/demo.json (every step of every storyline: the plans, the radar, the
cells behind it, and the messages and journal rows that step added) and out/console_demo.html (the
page with demo.json inlined, so it opens from a file, a GitHub Pages site or an artifact link).
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from foundry.orchestrator.scenarios import STORYLINES
from foundry.risk import projection as risk

STATIC = Path(__file__).resolve().parent / "static"
FAQ = {"f11_monday": ["What changed for CHN MN-1L since Monday?", "Is CHN MN-1L still at risk?", "Is KOL MN-1L still long?"],
       "week12": ["Why is the tower moving stock into Chennai?", "What was OTIF at Chennai for MN-1L in week 12?",
                  "What changed for BHW MP-1KG since Monday?", "Approve the Chennai plan for me"],
       "one_sender": ["Why does Neha have to sign Chennai's move?", "Is BHW MN-1L still long?", "What is waiting right now?"]}


def compact_state(rt, week: int) -> dict:
    st = rt.state(journal_tail=0)
    rad = rt.radar_now(week)
    cells = {}
    for r in rad["rows"]:
        if r["risk"] != "GREEN" or any(p["dc"] == r["dc"] and p["sku"] == r["sku"] for p in rt.plans.values()):
            pr = risk.project(rt.store, rt.profile, week, r["dc"], r["sku"])
            cells[f"{r['dc']}|{r['sku']}"] = {"days": [[d["day"], d["start"], d["demand"], d["short"], d["arrives"], d["end"]] for d in pr.days],
                                              "arrivals": pr.arrivals, "notes": pr.notes, "safety_stock": pr.safety_stock}
    plans = []
    for p in st["plans"]:
        p = dict(p)
        p.pop("meter", None)
        plans.append(p)
    return {"clock": st["clock"], "now": st["now"], "week": week, "counts": st["counts"], "meter": st["meter"], "plans": plans,
            "radar": {"counts": rad["counts"], "rows": [{k: r[k] for k in ("dc", "sku", "risk", "money_at_risk", "first_short_day", "dos0", "cases_short", "long", "reason", "priority")}
                                                        for r in rad["rows"]]},
            "cells": cells, "inflight": st["inflight"], "reservations": st["reservations"], "lane_delays": st["lane_delays"],
            "queue": st["queue"].get(str(week), [])}


def build(out_html: Path | None = None, storylines: dict | None = None, faq: dict | None = None, company: str | None = None,
          currency: str = "INR", brand: str = "GFB", write_static: bool = True) -> dict:
    """GFB's three storylines by default. Another company passes its own storylines (same shape as
    STORYLINES: name -> (title, fn(folder))), its FAQ, its name, currency and brand, and write_static=False
    so GFB's demo.json is left alone."""
    storylines = storylines or STORYLINES
    faq_by_story = FAQ if faq is None else faq
    demo = {"generated": time.strftime("%Y-%m-%d %H:%M"), "company": company or "Gokul Foods & Beverages (GFB)",
            "currency": currency, "brand": brand, "storylines": {}}
    tmp = Path(tempfile.mkdtemp(prefix="tower-demo-"))
    try:
        from foundry.chat import agent as chat
        for name, (title, fn) in storylines.items():
            rt, steps = _replay(fn, tmp / name)
            faq = []
            for q in faq_by_story.get(name, []):
                r = chat.ask(q, rt)
                faq.append({"q": q, "a": (r.answer or {}).get("text"), "cited": (r.answer or {}).get("cited"),
                            "tools": [c.get("tool") for c in r.tool_calls], "cost_inr": r.meter.get("cost_inr")})
            demo["storylines"][name] = {"title": title, "steps": steps, "faq": faq}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if write_static:
        (STATIC / "demo.json").write_text(json.dumps(demo, default=str))
    if out_html:
        page = (STATIC / "index.html").read_text()
        page = page.replace("<!--DEMO-->", "<script>window.DEMO = " + json.dumps(demo, default=str).replace("</", "<\\/") + ";</script>")
        out_html.parent.mkdir(parents=True, exist_ok=True)
        out_html.write_text(page)
    return demo


def _replay(fn, folder: Path):
    """Run a storyline, capturing the compact state after every step (the runtime is patched to call back)."""
    import foundry.orchestrator.scenarios as S
    captured = []
    real = S._step

    def spy(rt, log, label, f, said=""):
        n_msg, n_jr = len(rt.outbox.items), len(rt.bus.journal)
        out = real(rt, log, label, f, said)
        week = max(rt.radars) if rt.radars else 1
        snap = {"label": label, "said": said, "clock": out["clock"], "accepted": out["accepted"], "events": out["events"],
                "new_messages": rt.outbox.items[n_msg:], "new_journal": rt.bus.journal[n_jr:], "state": compact_state(rt, week)}
        captured.append(json.loads(json.dumps(snap, default=str)))      # frozen now: plans keep changing after this step
        return out
    S._step = spy
    try:
        rt, _ = fn(folder)
    finally:
        S._step = real
    return rt, captured


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--html", default="out/console_demo.html")
    a = ap.parse_args(argv)
    demo = build(Path(a.html))
    n = sum(len(s["steps"]) for s in demo["storylines"].values())
    print(f"{len(demo['storylines'])} storylines, {n} steps -> {STATIC / 'demo.json'} ({(STATIC / 'demo.json').stat().st_size // 1024} KB) and {a.html}")


if __name__ == "__main__":
    main()
