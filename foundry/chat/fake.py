"""A scripted brain for Ask the tower: it routes a question to the right tool by its words, then writes
the answer only from what the tool returned (templates, no arithmetic). Good enough for the demo, the
tests and the evals; a live model replaces it with the same tools and the same rules."""
from __future__ import annotations

import re

from foundry.scripted import ScriptedClient, result_of

DCS = {"chennai": "CHN", "bhiwandi": "BHW", "gurgaon": "GGN", "gurugram": "GGN", "kolkata": "KOL"}
SKUS = {"mango nectar": "MN-1L", "chakli": "GC-200", "pulp": "MP-1KG", "mango pulp": "MP-1KG"}
ACT = ("approve", "reject", "sign it", "book the truck", "move the stock", "move stock", "send the truck", "cancel")
DATA = ("otif", "fill rate", "days of supply", "dwell", "late truck", "forecast error", "how many orders", "sql", "table")


def _dc_sku(q: str, codes=("BHW", "GGN", "KOL", "CHN"), dc_names=None, sku_names=None, sku_codes=()) -> tuple[str | None, str | None]:
    ql = q.lower()
    dc_names = DCS if dc_names is None else dc_names
    sku_names = SKUS if sku_names is None else sku_names
    dc = next((c for c in codes if re.search(rf"\b{c}\b", q)), None) or next((v for k, v in dc_names.items() if k in ql), None)
    # the company's own SKU codes first, longest first (LG-30CR, SD-10CI: codes the GFB pattern does not know)
    sku = (next((c for c in sorted(sku_codes, key=len, reverse=True) if re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", q)), None)
           or next((m.group(0) for m in [re.search(r"\b[A-Z]{2}-\d+(?:L|KG|C|B|K)?\b", q)] if m), None)
           or next((v for k, v in sku_names.items() if k in ql), None))
    return dc, sku


class FakeChatClient(ScriptedClient):
    def __init__(self, codes=None, dc_names=None, sku_names=None, sku_codes=(), **kw):
        super().__init__(**kw)
        self.codes = tuple(codes or ("BHW", "GGN", "KOL", "CHN"))
        self.dc_names, self.sku_names, self.sku_codes = dc_names, sku_names, tuple(sku_codes)

    @classmethod
    def for_network(cls, net) -> "FakeChatClient":
        """Any company: DC codes and the first word of each DC's name ('Lakeside DC' -> lakeside),
        SKU names from the parts table. GFB keeps its hand-made aliases (gurugram, chakli)."""
        if getattr(net, "code", "") == "GFB":
            return cls()
        names = {s["name"].split()[0].lower(): c for c, s in net.sites.items() if s["kind"] == "dc"}
        skus = {v[0].lower(): k for k, v in net.parts.items()}
        return cls(codes=net.dcs, dc_names=names, sku_names=skus, sku_codes=tuple(net.parts))

    def next_call(self, task: dict, results: list[dict], seen: list[str]) -> tuple[str, dict]:
        q = task["question"]
        ql = q.lower()
        dc, sku = _dc_sku(q, self.codes, self.dc_names, self.sku_names, self.sku_codes)
        pid = (re.search(r"PRP-[0-9A-Za-z-]+", q) or [None])[0]
        if any(w in ql for w in ACT):
            if dc and "find_plan" not in seen:
                return "find_plan", {"dc": dc, "sku": sku}
            fp = result_of(results, "find_plan", {}) or {}
            if fp.get("plan_id") and fp["status"] not in ("READY", "PARTIAL"):
                return "answer", {"text": f"I can't sign or move anything. {fp['plan_id']} is already {fp['status'].lower()}; nothing waits on anyone.", "cited": [fp["plan_id"]]}
            if fp.get("plan_id"):
                waiting = [s for s in fp["steps"] if s["decision"] is None]
                who = ", ".join(sorted({s["approver"] for s in waiting})) or "nobody"
                return "answer", {"text": f"I can't sign or move anything; only the named people can. {fp['plan_id']} ({fp['status']}) waits on {who}, due {fp['due']}: they can sign from the console or the email link.",
                                  "cited": [fp["plan_id"]]}
            return "answer", {"text": "I can't sign, move stock or book trucks: the tower only proposes and named people sign. Tell me the DC and SKU and I'll say who must sign.", "cited": []}
        if any(w in ql for w in DATA):
            if "ask_data" not in seen:
                return "ask_data", {"question": q}
            d = result_of(results, "ask_data", {})
            return "answer", {"text": (d.get("answer") or "The data agent could not answer that.") + (f" (SQL: {d['sql']})" if d.get("sql") else ""), "cited": ["warehouse"]}
        if ("changed" in ql or "what happened" in ql or "timeline" in ql) and "whats_changed" not in seen:
            return "whats_changed", {"dc": dc, "sku": sku, "limit": 8}
        if "whats_changed" in seen:
            ev = result_of(results, "whats_changed")["events"]
            return "answer", {"text": "In order: " + " | ".join(e.split(": ", 1)[0] + ": " + e.split(": ", 1)[1][:120] for e in ev[-5:]) if ev else "Nothing in the journal for that.",
                              "cited": [e.split()[0] for e in ev[-5:]]}
        if pid or (dc and any(w in ql for w in ("why", "plan", "explain", "what is the tower doing", "decide"))):
            if pid and "explain_plan" not in seen:
                return "explain_plan", {"plan_id": pid}
            if not pid and "find_plan" not in seen:
                return "find_plan", {"dc": dc, "sku": sku}
            p = result_of(results, "explain_plan") or result_of(results, "find_plan")
            if p.get("error"):
                return "answer", {"text": p["error"], "cited": []}
            steps = "; ".join(f"{s['n']}. {s['code']} ({s['approver']}{', ' + s['decision'].lower() if s['decision'] else ''})"
                              + (f": {s['cases']:,} cases from {s['from_dc']}, saves {s['margin_saved']} for {s['lane_cost']}" if s.get("cases") else "")
                              for s in p["steps"]) or "no steps"
            dropped = "; ".join(f"{d['code']} dropped by {d['by']}: {d['reason'][:110]}" for d in p["dropped"]) or "nothing dropped"
            wi = f" The radar: {p['what_if']['before']} at risk, {p['what_if']['after']} after the move." if p.get("what_if") else ""
            lineage = f" It is v{p['version']}" + (f", replacing {p['supersedes']}" if p.get("supersedes") else "") + (f"; replaced by {p['superseded_by']}" if p.get("superseded_by") else "") + "."
            cause = p["caused_by"][-1] if p.get("caused_by") else ""
            return "answer", {"text": f"{p['plan_id']} ({p['status']}, {p['priority']}, due {p['due']}): {p['cause'].replace('_', ' ').lower()} at {p['dc']} {p['sku'] or 'all SKUs'}, {p['money_at_stake']} at stake. Steps: {steps}. {dropped}.{wi}{lineage} Triggered by {cause[:140]}",
                              "cited": [p["plan_id"]] + [c.split()[0] for c in p.get("caused_by", [])[-2:]]}
        if dc and sku and any(w in ql for w in ("risk", "radar", "short", "run out", "stockout", "cover", "long", "spare")):
            if "radar_cell" not in seen:
                return "radar_cell", {"dc": dc, "sku": sku}
            r = result_of(results, "radar_cell")
            if r.get("error"):
                return "answer", {"text": r["error"], "cited": []}
            return "answer", {"text": f"{dc} {sku} is {r['risk']} with {r['dos0']} days of cover on hand: {r['reason']}. Arrivals: " + ", ".join(f"day {a['day']} {a['cases']:,} ({a['source']})" for a in r["arrivals"][:4]) + ".",
                              "cited": [f"radar {dc} {sku} week {r['week']}"]}
        if "tower_state" not in seen:
            return "tower_state", {}
        st = result_of(results, "tower_state")
        live = st["live_plans"]
        lines = "; ".join(f"{p['plan_id']} {p['priority']} {p['dc']} {p['sku'] or 'all'} {p['cause'].replace('_', ' ').lower()}, waiting on {', '.join(p['waiting_on']) or 'nobody'}, due {p['due']}" for p in live[:6])
        red = ", ".join(f"{r['dc']} {r['sku']} ({r['money_at_risk']}, day {r['first_short_day']})" for r in (st.get("radar_red") or [])[:5])
        return "answer", {"text": f"At {st['clock']}: {len(live)} live plan(s), {st['money_at_stake']} at stake. {lines or 'Nothing waiting.'} Radar red: {red or 'none'}. Model cost this week {st['model_cost_this_week']}.",
                          "cited": [p["plan_id"] for p in live[:6]]}
