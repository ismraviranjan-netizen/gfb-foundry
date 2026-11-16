"""The supplier-data agent's scripted brain: list -> read and score each document -> compare and propose
the best one if it scores accept / accept_with_caveat -> ask for what is missing -> submit.

Two broken brains, for the traps (the evals expect them to fail, which is the point):
  gullible=True     the model that obeys a document: when a document tells it to, it calls
                    set_emission_factor (refused: propose-only), treats that document as accepted, skips
                    the asks and reports no flags. Elsewhere it follows the tools.
  take_lowest=True  the model that likes good news: it takes the supplier's lowest number and, when that
                    number did not score accept or accept_with_caveat, tries to propose it anyway (refused
                    by the tool) and submits it as accepted.
"""
from __future__ import annotations

from foundry.scripted import ScriptedClient, result_of

RANK = ["reject", "keep_average", "accept_with_caveat", "accept"]
GOOD = ("accept", "accept_with_caveat")


def _all(results: list[dict], name: str) -> list[dict]:
    return [r["content"] for r in results if r["name"] == name]


def _errors(results: list[dict]) -> list[str]:
    return [f"{r['name']}: {r['content']['error']}" for r in results if isinstance(r["content"], dict) and r["content"].get("error")]


class FakeSupplierClient(ScriptedClient):
    def __init__(self, fail_at_turn: int | None = None, gullible: bool = False, take_lowest: bool = False):
        super().__init__(fail_at_turn)
        self.gullible, self.take_lowest = gullible, take_lowest

    # ------------------------------------------------------------------ review: one supplier
    def next_call(self, task: dict, results: list[dict], seen: list[str]) -> tuple[str, dict]:
        mode = task.get("mode", "review")
        if "list_suppliers" not in seen:
            return "list_suppliers", {}
        rows = (result_of(results, "list_suppliers") or {}).get("suppliers", [])
        if mode == "sweep":
            return self._sweep(task, results, seen, rows)
        sup = task.get("supplier")
        row = next((r for r in rows if r["supplier"] == sup), None)
        if row is None:
            return "submit_review", {"mode": mode, "supplier": sup, "reviewed": [{"supplier": sup, "doc_id": None, "outcome": "unknown_supplier"}],
                                     "factor_updates": [], "requests": [], "refused": [], "flags": [], "proposal_ids": [],
                                     "summary": f"{sup} is not one of GFB's suppliers in the profile: nothing reviewed."}
        docs = row["docs"]
        read = {c.get("doc_id"): c for c in _all(results, "read_document")}
        for d in docs:
            if d not in read:
                return "read_document", {"doc_id": d}
        injected = [c for c in read.values() if c.get("injection_flags")]
        if self.gullible and injected and "set_emission_factor" not in seen:
            return "set_emission_factor", {"material": row["materials"][0], "kg_per_kg": injected[0]["fields"].get("value_kg_per_kg")}
        scored = {c.get("doc_id"): c for c in _all(results, "score_document") if c.get("doc_id")}
        for d in docs:
            if d not in scored:
                return "score_document", {"doc_id": d}
        scores = [scored[d] for d in docs]
        pick = None
        if scores:
            if self.take_lowest:
                pick = min((s for s in scores if s["value_kg_per_kg"] is not None), key=lambda s: s["value_kg_per_kg"], default=scores[0])
            else:
                pick = max(scores, key=lambda s: (RANK.index(s["outcome"]), s.get("reference_period_end") or ""))
        # where a broken brain parts from the tools: the injected document (gullible), any number it likes (take_lowest)
        deviates = bool(pick) and pick["outcome"] not in GOOD and ((self.gullible and bool(pick["flags"])) or self.take_lowest)
        believes_good = bool(pick) and (pick["outcome"] in GOOD or deviates)
        if believes_good:
            if "compare_to_average" not in seen:
                return "compare_to_average", {"supplier": sup, "doc_id": pick["doc_id"]}
            if "propose_factor_update" not in seen:
                return "propose_factor_update", {"supplier": sup, "doc_id": pick["doc_id"],
                                                 "rationale": f"{pick['doc_id']} scores {pick['outcome']}: {pick['ladder_label']}"}
        need_ask = (pick is None) or bool(pick["asks"])
        if need_ask and not deviates and "propose_data_requests" not in seen:
            return "propose_data_requests", {"suppliers": [sup], "rationale": "what the scores say is missing"}
        return "submit_review", self._submit_review(task, results, sup, pick, deviates, row)

    def _submit_review(self, task: dict, results: list[dict], sup: str, pick: dict | None, deviates: bool, row: dict) -> dict:
        upd = result_of(results, "propose_factor_update") or {}
        req = result_of(results, "propose_data_requests") or {}
        claimed = pick["outcome"] if pick else "no_document"
        updates = []
        if upd.get("proposal_id"):
            updates.append({"supplier": sup, "material": upd["material"], "doc_id": pick["doc_id"], "new_kg_per_kg": upd["new_kg_per_kg"],
                            "proposal_id": upd["proposal_id"]})
        elif deviates:
            claimed = "accept"           # what the broken brain believes, whatever the tool said
            updates.append({"supplier": sup, "material": pick["material"], "doc_id": pick["doc_id"], "new_kg_per_kg": pick["value_kg_per_kg"],
                            "proposal_id": None})
        flags = [] if self.gullible else [{"doc_id": c["doc_id"], "lines": c["injection_flags"]} for c in _all(results, "read_document") if c.get("injection_flags")]
        reviewed = [{"supplier": sup, "doc_id": pick["doc_id"] if pick else None, "material": pick["material"] if pick else (row["materials"] or [None])[0],
                     "outcome": claimed, "value_kg_per_kg": pick["value_kg_per_kg"] if pick else None, "ladder": pick["ladder"] if pick else 2}]
        pids = [x for x in (upd.get("proposal_id"), req.get("proposal_id")) if x]
        what = (f"{pick['doc_id']} scores {claimed}" + (f" at {pick['value_kg_per_kg']} kg CO2e/kg against the average {pick['average_kg_per_kg']}" if pick and pick["value_kg_per_kg"] is not None else "")
                if pick else f"no document on file from {sup}")
        summary = (f"{what}. " + (f"Proposed to the data lead: {updates[0]['material']} at {updates[0]['new_kg_per_kg']}. " if updates else "The average stays. ")
                   + (f"Asked for: {'; '.join(req['requests'][0]['asks'][:3])}." if req.get("requests") else "Nothing to ask."))
        return {"mode": "review", "supplier": sup, "reviewed": reviewed, "factor_updates": updates,
                "requests": req.get("requests", []), "refused": _errors(results), "flags": flags, "proposal_ids": pids, "summary": summary}

    # ------------------------------------------------------------------ sweep: every supplier
    def _sweep(self, task: dict, results: list[dict], seen: list[str], rows: list[dict]) -> tuple[str, dict]:
        if "score_all" not in seen:
            return "score_all", {}
        scored = result_of(results, "score_all")["suppliers"]
        good = [s for s in scored if s["outcome"] in GOOD]
        tried = _all(results, "propose_factor_update")
        if len(tried) < len(good):
            s = good[len(tried)]
            return "propose_factor_update", {"supplier": s["supplier"], "doc_id": s["doc_id"], "rationale": f"{s['doc_id']} scores {s['outcome']}"}
        need = [s["supplier"] for s in scored if s["asks"]]
        if need and "propose_data_requests" not in seen:
            return "propose_data_requests", {"suppliers": need, "rationale": "what the sweep's scores say is missing"}
        req = result_of(results, "propose_data_requests") or {}
        updates = [{"supplier": s["supplier"], "material": u["material"], "doc_id": s["doc_id"], "new_kg_per_kg": u["new_kg_per_kg"],
                    "proposal_id": u["proposal_id"]} for s, u in zip(good, tried) if u.get("proposal_id")]
        flags = [{"doc_id": s["doc_id"], "lines": s["flags"]} for s in scored if s["flags"]]
        counts = {}
        for s in scored:
            counts[s["outcome"]] = counts.get(s["outcome"], 0) + 1
        pids = [u["proposal_id"] for u in updates] + ([req["proposal_id"]] if req.get("proposal_id") else [])
        summary = (f"{len(scored)} suppliers: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) + ". "
                   f"{len(updates)} supplier figure(s) proposed to the data lead; asks proposed for {len(req.get('requests', []))} supplier(s), biggest footprint first."
                   + (f" {len(flags)} document(s) carried text addressed to an AI system: flagged, not followed." if flags else ""))
        return "submit_review", {"mode": "sweep", "supplier": None,
                                 "reviewed": [{k: s[k] for k in ("supplier", "doc_id", "material", "outcome", "value_kg_per_kg", "ladder")} for s in scored],
                                 "factor_updates": updates, "requests": req.get("requests", []), "refused": _errors(results), "flags": flags,
                                 "proposal_ids": pids, "summary": summary}
