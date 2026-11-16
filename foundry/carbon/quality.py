"""How much a supplier's number is worth: PACT-style checks, a data-quality rating, the validity period,
the rung on the data-quality ladder, and what to ask the supplier for next.

The checks are GFB's rules (profile carbon.supplier_data), modelled on the PACT Methodology's ideas
(S19): a declared unit, a boundary, a reference period, a validity period, a method, the primary data
share, data-quality ratings, land-use change and biogenic emissions stated apart, assurance. They are
not PACT's own conformance rules; the book must say "PACT-style".

Outcome, from the worst cap any check sets:
  reject              the number is not comparable (wrong product, unit, or boundary)
  keep_average        comparable but not usable yet (expired, too old, no period, poor quality, implausible
                      and unassured): the average stays and GFB asks again
  accept_with_caveat  usable, proposed to the data lead with its caveats
  accept              proposed to the data lead to replace the average
The agent proposes; the data lead (approve_emission_factor) decides; nothing here changes a factor.

The ladder (worst to best): L1 spend-based (EEIO), L2 average data, L3 supplier-specific built mostly
on secondary data, L4 supplier-specific primary, L5 supplier-specific primary and third-party assured.
"""
from __future__ import annotations

import datetime as dt

from foundry.carbon.factors import load
from foundry.profile import Profile

ORDER = ["reject", "keep_average", "accept_with_caveat", "accept"]
LADDER = {1: "L1 spend-based (EEIO)", 2: "L2 average data", 3: "L3 supplier-specific, mostly secondary data",
          4: "L4 supplier-specific, primary data", 5: "L5 supplier-specific, primary, third-party assured"}
SERVICE_PREFIX = "co_pack"          # a purchased service: its own energy, gate to gate


def rules(profile: Profile) -> dict:
    f = load(profile)
    r = {"accept_dqr": 2.0, "caveat_dqr": 3.0, "max_age_years": 3, "reporting_year": 2026, "primary_min_share": 0.5,
         "plausible_ratio": [0.3, 3.0], "default_validity_years": 3, "land_use_materials": [], "dqr_scale": "1 best to 5 worst"}
    r.update(f.raw.get("supplier_data") or {})
    r["as_of"] = str(f.raw.get("checked", "2026-11-08"))
    return r


def _date(s: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)) if s else None
    except ValueError:
        return None


def expected_boundary(material: str) -> str:
    return "gate-to-gate" if material.startswith(SERVICE_PREFIX) else "cradle-to-gate"


def materials_of(profile: Profile, supplier: str) -> list[str]:
    mats = load(profile).raw.get("materials") or {}
    return [m for m, spec in mats.items() if spec.get("supplier") == supplier]


def score(doc: dict, profile: Profile) -> dict:
    """Score one read document (docs.read output) for the supplier it was filed under."""
    f = load(profile)
    r = rules(profile)
    fl = doc["fields"]
    sup = doc["supplier"]
    mats = materials_of(profile, sup)
    material = fl.get("material")
    if not material and len(mats) > 1:      # a letter names no material: match the supplier's materials on the text
        low = str(doc.get("text", "")).lower()
        material = next((m for m in mats if m.split("_")[-1][:4] in low), None)
    material = material or (mats[0] if mats else None)
    avg = float(((f.raw.get("materials") or {}).get(material) or {}).get("kg_per_kg") or 0) or None
    checks, asks, caps = [], [], []

    def check(cid: str, name: str, status: str, why: str, cap: str | None = None, ask: str | None = None) -> None:
        checks.append({"id": cid, "name": name, "status": status, "why": why})
        if cap:
            caps.append(cap)
        if ask:
            asks.append(ask)

    # C1 product
    if material and material in mats:
        check("C1", "product", "pass", f"{material}: what GFB buys from {sup}")
    else:
        check("C1", "product", "fail", f"{material or 'unknown product'} is not what GFB buys from {sup} ({', '.join(mats) or 'nothing'})",
              "reject", f"a figure for the product GFB buys ({', '.join(mats)})")
    # C2 declared unit
    value = fl.get("value_kg_per_kg")
    if value is not None:
        why = "per kg" + (f" (converted: {fl['converted_from']})" if fl.get("converted_from") else "")
        check("C2", "declared unit", "pass", why)
    else:
        check("C2", "declared unit", "fail", f"no per-kg figure (declared unit {fl.get('declared_unit') or 'not stated'})",
              "reject", "the figure per kg, or the mass of one declared unit")
    # C3 boundary
    want = expected_boundary(material or "")
    b = fl.get("boundary")
    if b == want:
        check("C3", "boundary", "pass", b)
    elif b:
        extra = " (GFB counts packaging after use in category 12: a cradle-to-grave figure double counts)" if b == "cradle-to-grave" else ""
        check("C3", "boundary", "fail", f"{b}, GFB's average is {want}{extra}", "reject", f"a {want} figure per kg{extra}")
    else:
        check("C3", "boundary", "fail", "boundary not stated", "keep_average", f"state the boundary ({want}, per kg)")
    # C4 reference period and age
    end = _date(fl.get("reference_period_end"))
    if end:
        age = int(r["reporting_year"]) - end.year
        if age <= int(r["max_age_years"]):
            check("C4", "reference period", "pass", f"{fl.get('reference_period_start')} to {end}: {age} year(s) before {r['reporting_year']}")
        else:
            check("C4", "reference period", "fail", f"ends {end}: {age} years old (limit {r['max_age_years']})", "keep_average",
                  f"a footprint for a reference period ending {int(r['reporting_year']) - 1} or later")
    else:
        age = None
        check("C4", "reference period", "fail", "reference period not stated", "keep_average", "state the reference period (the 12 months the data cover)")
    # C5 validity
    as_of = _date(r["as_of"])
    vend = _date(fl.get("validity_end"))
    basis = "stated"
    if not vend and end:
        vend = end.replace(year=end.year + int(r["default_validity_years"]))
        basis = f"not stated: reference end + {r['default_validity_years']} years (GFB's assumption, S19)"
    if vend and as_of and vend >= as_of:
        check("C5", "validity", "pass", f"valid to {vend} ({basis})")
    elif vend:
        check("C5", "validity", "fail", f"expired {vend} ({basis}); checked as of {as_of}", "keep_average",
              "a refreshed footprint with its validity period")
    else:
        check("C5", "validity", "fail", "no validity period and no reference period to date it from", "keep_average")
    # C6 method
    std = fl.get("standards") or []
    if std:
        check("C6", "method", "pass", ", ".join(std))
    else:
        check("C6", "method", "warn", "no method named", "accept_with_caveat",
              "name the method (ISO 14067, the GHG Protocol Product Standard or the PACT Methodology)")
    # C7 primary data share
    ps = fl.get("primary_share")
    if ps is not None and ps >= float(r["primary_min_share"]):
        check("C7", "primary data share", "pass", f"{ps:.0%} primary")
    else:
        why = f"{ps:.0%} primary (GFB counts primary from {float(r['primary_min_share']):.0%})" if ps is not None else "primary data share not stated"
        check("C7", "primary data share", "warn", why, "accept_with_caveat",
              "the share of primary (own-site) data, and site data for the main process steps")
    # C8 data-quality rating
    dqr = fl.get("dqr")
    dqr_avg = round(sum(dqr.values()) / len(dqr), 2) if dqr else None
    if dqr_avg is None:
        cap = "keep_average" if (ps is None or ps < float(r["primary_min_share"])) else "accept_with_caveat"
        check("C8", "data-quality rating", "warn" if cap != "keep_average" else "fail",
              "no data-quality ratings" + ("; with no primary data either, this is an average by another name" if cap == "keep_average" else ""),
              cap, "data-quality ratings (technology, time, geography, completeness, reliability)")
    elif dqr_avg <= float(r["accept_dqr"]):
        check("C8", "data-quality rating", "pass", f"DQR {dqr_avg} (accept at or below {r['accept_dqr']}; scale {r['dqr_scale']})")
    elif dqr_avg <= float(r["caveat_dqr"]):
        weak = sorted(dqr, key=lambda k: -dqr[k])[:2]
        check("C8", "data-quality rating", "warn", f"DQR {dqr_avg} (caveat up to {r['caveat_dqr']})", "accept_with_caveat",
              f"improve the weakest ratings: {', '.join(weak)}")
    else:
        check("C8", "data-quality rating", "fail", f"DQR {dqr_avg} above {r['caveat_dqr']}", "keep_average",
              "better data for the weakest ratings")
    # C9 land use and biogenic (agricultural materials)
    if material in (r.get("land_use_materials") or []):
        if fl.get("dluc_stated"):
            check("C9", "land-use change", "pass", f"dLUC stated ({fl.get('dluc')} kg CO2e/kg)")
        else:
            check("C9", "land-use change", "warn", "land-use change not stated for a crop-based material", "accept_with_caveat",
                  "land-use change emissions (dLUC), stated separately")
        if fl.get("biogenic_separated"):
            check("C10", "biogenic split", "pass", "fossil and biogenic stated apart")
        else:
            check("C10", "biogenic split", "warn", "biogenic emissions not stated apart", "accept_with_caveat",
                  "fossil and biogenic emissions stated separately")
    # C11 assurance
    if fl.get("assured"):
        check("C11", "assurance", "pass", f"third-party assured ({fl.get('assurance_level') or 'level not stated'})")
    else:
        check("C11", "assurance", "warn", "not assured", "accept_with_caveat", "third-party assurance (limited assurance is enough to start)")
    # C12 plausibility against the average
    ratio = round(value / avg, 2) if (value is not None and avg) else None
    lo, hi = r["plausible_ratio"]
    if ratio is not None and lo <= ratio <= hi:
        check("C12", "plausibility", "pass", f"{ratio}x the average {avg}")
    elif ratio is not None:
        cap = "accept_with_caveat" if fl.get("assured") else "keep_average"
        check("C12", "plausibility", "warn" if fl.get("assured") else "fail",
              f"{ratio}x the average {avg} (outside {lo}-{hi}x" + (", unassured)" if not fl.get("assured") else ")"), cap,
              f"the calculation behind a figure {ratio}x the average: inputs and the emission factors used")
    # C13 instructions inside the document
    if doc.get("injection_flags"):
        check("C13", "instructions in the document", "warn",
              f"{len(doc['injection_flags'])} line(s) addressed to an AI system: treated as data, flagged to the data lead")
    outcome = min(caps, key=ORDER.index) if caps else "accept"
    if ps is not None and ps >= float(r["primary_min_share"]):
        rung = 5 if fl.get("assured") else 4
    else:
        rung = 3
    return {"doc_id": doc["doc_id"], "supplier": sup, "material": material, "value_kg_per_kg": round(value, 4) if value is not None else None,
            "average_kg_per_kg": avg, "ratio_to_average": ratio, "dqr": dqr_avg, "primary_share": ps,
            "reference_period_end": str(end) if end else None, "valid_until": str(vend) if vend else None, "age_years": age,
            "ladder": rung, "ladder_label": LADDER[rung], "checks": checks, "outcome": outcome, "asks": _dedupe(asks),
            "caveats": [c["why"] for c in checks if c["status"] == "warn"], "flags": list(doc.get("injection_flags") or []),
            "as_of": r["as_of"]}


def no_document(profile: Profile, supplier: str) -> dict:
    """A supplier with nothing on file: the average stays and GFB asks for a footprint."""
    r = rules(profile)
    mats = materials_of(profile, supplier)
    m = ", ".join(mats) or "what GFB buys"
    want = expected_boundary(mats[0]) if mats else "cradle-to-gate"
    return {"doc_id": None, "supplier": supplier, "material": mats[0] if mats else None, "value_kg_per_kg": None,
            "average_kg_per_kg": None, "ladder": 2, "ladder_label": LADDER[2], "checks": [], "outcome": "no_document",
            "asks": [f"a product carbon footprint for {m}: {want}, per kg, reference period ending {int(r['reporting_year']) - 1} or later, "
                     f"with data-quality ratings and the primary data share"], "caveats": [], "flags": [], "as_of": r["as_of"]}


def best(scores: list[dict]) -> dict | None:
    """The document to act on: the best outcome, then the most recent reference period."""
    if not scores:
        return None
    return max(scores, key=lambda s: (ORDER.index(s["outcome"]) if s["outcome"] in ORDER else -1, s.get("reference_period_end") or ""))


def _dedupe(xs: list[str]) -> list[str]:
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
