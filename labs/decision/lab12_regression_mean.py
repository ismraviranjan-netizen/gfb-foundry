"""Lab 12 · Regression to the mean: why "the worst rows improved after the agent" proves nothing.

The slide every pilot ends with: "we switched the sensing agent on for the ten worst rows and their
forecast error fell by 7.5 points". This lab takes the slide apart on GFB's two-year history.
  1 No agent at all: two years cut into thirteen 8-week blocks. In each block rank the 40 rows (DC x
    SKU) by the consensus's WAPE; follow the worst ten and the best ten into the next block. Nobody
    touched them.
  2 The size of the effect: the block-to-block correlation r of a row's WAPE. A row's distance from the
    average carries into the next block only r of the way; the rest was that block's luck.
  3 The slide: at week 64 the ten worst rows over weeks 57-64 get the agent from week 65. Their WAPE in
    weeks 65-72, with the agent's number, against weeks 57-64. Then the same rows' consensus WAPE in
    weeks 65-72 (the future with no agent, which a simulation can see and a pilot cannot).
  4 The fix: rank, take the twenty worst, and toss a coin for which ten get the agent. Compare the two
    halves over the same weeks. 2,000 coin tosses show how far one pilot can land from the truth.
WAPE here is a row's own WAPE, averaged over the rows (each row counts once). Nothing in this lab is an
assumption except the block length (BLOCK, 8 weeks) and the group sizes (TOP 10, POOL 20): vary them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from labs.decision.data import history2, sensed2

BLOCK, TOP, POOL = 8, 10, 20
RANK_BLOCK = 7                  # weeks 57-64 (block index = (week - 1) // BLOCK)


def _wape(a: np.ndarray, f: np.ndarray) -> float:
    return 100.0 * float(np.abs(a - f).sum() / a.sum())


def block_table(block: int = BLOCK) -> pd.DataFrame:
    """Consensus WAPE per row per block: 40 rows x 13 blocks."""
    h = history2().assign(b=lambda d: (d.week - 1) // block)
    return h.groupby(["series", "b"]).apply(lambda g: _wape(g.actual.to_numpy(), g.consensus.to_numpy()),
                                            include_groups=False).unstack()


def pairs(W: pd.DataFrame, top: int = TOP) -> pd.DataFrame:
    rows = []
    for b in range(W.shape[1] - 1):
        a, n = W[b], W[b + 1]
        worst, best = a.nlargest(top).index, a.nsmallest(top).index
        rows.append({"block": b, "worst_now": a[worst].mean(), "worst_next": n[worst].mean(), "best_now": a[best].mean(),
                     "best_next": n[best].mean(), "all_now": a.mean(), "all_next": n.mean(),
                     "r": float(np.corrcoef(a, n)[0, 1])})
    return pd.DataFrame(rows)


def dc_blocks(block: int = BLOCK) -> pd.DataFrame:
    h = history2().assign(b=lambda d: (d.week - 1) // block)
    return h.groupby(["dc", "b"]).apply(lambda g: _wape(g.actual.to_numpy(), g.consensus.to_numpy()),
                                        include_groups=False).unstack()


def run(seed: int = 12, n_splits: int = 2000) -> dict:
    W = block_table()
    P = pairs(W)
    m = P.mean(numeric_only=True)
    r_pooled = float(np.corrcoef(np.concatenate([W[b] for b in range(W.shape[1] - 1)]),
                                 np.concatenate([W[b + 1] for b in range(W.shape[1] - 1)]))[0, 1])
    predicted = m["all_next"] + r_pooled * (m["worst_now"] - m["all_now"])
    D = dc_blocks()
    worst_dc = [(b, D[b].idxmax(), float(D[b].max()), float(D.loc[D[b].idxmax(), b + 1])) for b in range(D.shape[1] - 1)]
    improved = sum(1 for _, _, now, nxt in worst_dc if nxt < now)
    times_kol = sum(1 for _, dc, _, _ in worst_dc if dc == "KOL")
    biggest = max(worst_dc, key=lambda x: x[2] - x[3])
    # the slide: agent on for the ten worst rows of weeks 57-64, measured in weeks 65-72
    h = history2()
    sn = sensed2(54, 104, 3)[["series", "target", "h1"]].rename(columns={"target": "week"})
    lo, hi = (RANK_BLOCK + 1) * BLOCK + 1, (RANK_BLOCK + 2) * BLOCK          # weeks 65-72
    after = h[(h.week >= lo) & (h.week <= hi)].merge(sn, on=["series", "week"])
    per = after.groupby("series").apply(lambda g: pd.Series({"cons": _wape(g.actual.to_numpy(), g.consensus.to_numpy()),
                                                             "agent": _wape(g.actual.to_numpy(), g.h1.to_numpy())}),
                                        include_groups=False)
    rank = W[RANK_BLOCK]
    worst = rank.nlargest(TOP).index
    before = float(rank[worst].mean())
    with_agent = float(per.loc[worst, "agent"].mean())
    no_agent = float(per.loc[worst, "cons"].mean())
    claim = before - with_agent
    rtm = before - no_agent
    real = no_agent - with_agent
    # the fix: coin tosses inside the twenty worst
    pool = rank.nlargest(POOL).index
    rng = np.random.default_rng(seed)
    est = []
    for _ in range(n_splits):
        t = rng.choice(pool, TOP, replace=False)
        c = pool.difference(t)
        est.append(float(per.loc[c, "cons"].mean() - per.loc[t, "agent"].mean()))
    est = np.array(est)
    truth_pool = float((per.loc[pool, "cons"] - per.loc[pool, "agent"]).mean())
    return {"pairs": P, "mean": m.to_dict(), "r": r_pooled, "predicted_worst_next": float(predicted),
            "dc_table": D, "worst_dc": worst_dc, "dc_improved": improved, "dc_kol": times_kol, "dc_biggest": biggest,
            "before": before, "with_agent": with_agent, "no_agent": no_agent, "claim": claim, "rtm": rtm, "real": real,
            "worst_rows": list(worst), "first_split": float(est[0]), "split_mean": float(est.mean()), "split_sd": float(est.std()),
            "split_q": [float(np.quantile(est, q)) for q in (0.05, 0.95)], "truth_pool": truth_pool,
            "all_cons_after": float(per.cons.mean()), "all_agent_after": float(per.agent.mean()), "n_splits": n_splits}


def main() -> None:
    r = run()
    m = r["mean"]
    n = len(r["pairs"])
    print(f"no agent anywhere: {n} pairs of 8-week blocks, consensus WAPE per row (40 rows)")
    print(f"  ten worst rows:  {m['worst_now']:5.1f}% -> next block {m['worst_next']:5.1f}%"
          f"  ({m['worst_next'] - m['worst_now']:+.1f} points)")
    print(f"  ten best rows:   {m['best_now']:5.1f}% -> next block {m['best_next']:5.1f}%"
          f"  ({m['best_next'] - m['best_now']:+.1f} points)")
    print(f"  all forty rows:  {m['all_now']:5.1f}% -> next block {m['all_next']:5.1f}%")
    print(f"  block-to-block correlation r = {r['r']:.2f}: {r['r']:.0%} of a row's gap to the average carries")
    print(f"  over; the worst ten should land near {r['predicted_worst_next']:.1f}% (they land at {m['worst_next']:.1f}%)")
    print(f"  by DC: the worst DC improved the next block in {r['dc_improved']} of {n} pairs; it was Kolkata in "
          f"{r['dc_kol']} of {n}")
    b, dc, now, nxt = r["dc_biggest"]
    print(f"  the slide nobody should make: {dc} weeks {b * 8 + 1}-{b * 8 + 8} {now:.1f}% -> {nxt:.1f}% "
          f"with no agent at all")
    print("the pilot slide: ten worst rows of weeks 57-64 get the agent from week 65")
    print(f"  weeks 57-64 {r['before']:.1f}% -> weeks 65-72 with the agent {r['with_agent']:.1f}%: "
          f"claim {r['claim']:.1f} points")
    print(f"  the same rows, weeks 65-72, no agent: {r['no_agent']:.1f}%: regression to the mean "
          f"{r['rtm']:.1f} points, agent {r['real']:.1f}")
    print(f"  (all forty rows in weeks 65-72: consensus {r['all_cons_after']:.1f}%, agent {r['all_agent_after']:.1f}%)")
    q = r["split_q"]
    print(f"the fix: coin toss inside the {POOL} worst rows, {TOP} get the agent ({r['n_splits']:,} tosses):")
    print(f"  estimate {r['split_mean']:.1f} points on average (sd {r['split_sd']:.1f}, 90% of tosses "
          f"{q[0]:.1f} to {q[1]:.1f}); truth {r['truth_pool']:.1f}")
    print(f"  one toss (the first): {r['first_split']:.1f} points")
    print(f"Decision: credit the agent with {r['real']:.1f} points, not {r['claim']:.1f}: "
          f"{r['rtm'] / r['claim']:.0%} of the slide is the worst rows")
    print("  drifting back. Pick pilot rows by coin toss among the bad ones, never 'the worst ten'.")


if __name__ == "__main__":
    main()
