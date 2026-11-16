"""The cost ledger (Part 6): every run the loop logged, summarised the way a client reads it.

    python3 ops/ledger.py [runs/runs.jsonl]

Per agent: runs, pass share of stop reasons, cost per run (mean, p95) in USD and INR, latency p95,
tokens per run. The numbers in the book's cost tables come from here, not from a slide.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


def summarise(path: str | Path = "runs/runs.jsonl") -> dict:
    rows = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()] if Path(path).exists() else []
    by = defaultdict(list)
    for r in rows:
        by[r.get("agent", "?")].append(r)
    out = {}
    for agent, rs in by.items():
        cost = [r["meter"].get("cost_usd", 0.0) for r in rs]
        inr = [r["meter"].get("cost_inr", 0.0) for r in rs]
        lat = [r.get("elapsed_s", 0.0) for r in rs]
        toks = [r["meter"].get("input_tokens", 0) + r["meter"].get("output_tokens", 0) for r in rs]
        stops = defaultdict(int)
        for r in rs:
            stops[r.get("stop_reason", "?")] += 1
        out[agent] = {"runs": len(rs), "stop_reasons": dict(stops),
                      "cost_usd_mean": round(sum(cost) / len(rs), 4), "cost_usd_p95": round(p95(cost), 4),
                      "cost_inr_mean": round(sum(inr) / len(rs), 2), "cost_inr_p95": round(p95(inr), 2),
                      "latency_s_p95": round(p95(lat), 2), "tokens_per_run_mean": round(sum(toks) / len(rs))}
    return out


if __name__ == "__main__":
    print(json.dumps(summarise(sys.argv[1] if len(sys.argv) > 1 else "runs/runs.jsonl"), indent=1))
