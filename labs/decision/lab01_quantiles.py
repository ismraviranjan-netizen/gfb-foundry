"""Lab 1 · Quantiles from three places: the planning master, the series' own history, the network.

Question: how many cases of mango nectar will Chennai sell next week (week 17)?
  (a) the planning master says N(2,600, 450): read P10, P50, P90 off the normal
  (b) the series' own sixteen weeks: sort them and read the 10th, 50th and 90th percentiles
  (c) the network: pool actual / consensus over every ordinary week of all forty series, take its
      quantiles, multiply by Chennai's consensus
and how sure can we be of a P90 read from sixteen numbers (a bootstrap).
"""
from __future__ import annotations

import numpy as np

from labs.decision import core
from labs.decision.data import history

TAUS = (0.1, 0.5, 0.9)


def run(series: str = "CHN MN-1L", upto: int = 16, seed: int = 1) -> dict:
    h = history()
    me = h[(h.series == series) & (h.week <= upto)]
    mu, sigma, cons = float(me.mu.iloc[0]), float(me.sigma.iloc[0]), float(me.consensus.iloc[-1])
    master = {t: core.normal_stock(cons, sigma, t) for t in TAUS}
    own = {t: core.quantile(list(me.actual), t) for t in TAUS}
    ordinary = h[(h.week <= upto) & (h.uplift == 0)]
    ratio = list(ordinary.actual / ordinary.consensus)
    pooled_r = {t: core.quantile(ratio, t) for t in TAUS}
    pooled = {t: cons * pooled_r[t] for t in TAUS}
    rng = np.random.default_rng(seed)
    x = me.actual.to_numpy()
    boot = np.array([np.quantile(rng.choice(x, size=len(x), replace=True), 0.9) for _ in range(5000)])
    nxt = h[(h.series == series) & (h.week == upto + 1)].actual.iloc[0]
    return {"series": series, "upto": upto, "consensus": cons, "sigma": sigma, "n_own": len(x), "own_mean": float(x.mean()),
            "own_sd": float(x.std(ddof=1)), "master": master, "own": own, "pooled_ratio": pooled_r, "pooled": pooled,
            "n_pooled": len(ratio), "boot_p90_lo": float(np.quantile(boot, 0.05)), "boot_p90_hi": float(np.quantile(boot, 0.95)),
            "actual_next": float(nxt), "own_values": [float(v) for v in x]}


def main() -> None:
    r = run()
    print(f"{r['series']}, weeks 1-{r['upto']}: consensus {r['consensus']:,.0f}, planning sigma {r['sigma']:,.0f}")
    print(f"  own history: n {r['n_own']}, mean {r['own_mean']:,.0f}, sd {r['own_sd']:,.0f}")
    print(f"  {'':16}{'P10':>8}{'P50':>8}{'P90':>8}")
    for name in ("master", "own", "pooled"):
        q = r[name]
        print(f"  {name:16}{q[0.1]:8,.0f}{q[0.5]:8,.0f}{q[0.9]:8,.0f}")
    pr = r["pooled_ratio"]
    print(f"  pooled ratio actual/consensus over {r['n_pooled']} ordinary weeks: P10 {pr[0.1]:.3f} P50 {pr[0.5]:.3f} P90 {pr[0.9]:.3f}")
    print(f"  bootstrap 90% range of the own-history P90: {r['boot_p90_lo']:,.0f} to {r['boot_p90_hi']:,.0f}")
    print(f"  week {r['upto'] + 1} actual: {r['actual_next']:,.0f}")
    print(f"Decision: do not plan week {r['upto'] + 1} on a P90 read from {r['n_own']} weeks: across "
          f"bootstraps it moves")
    print(f"  from {r['boot_p90_lo']:,.0f} to {r['boot_p90_hi']:,.0f}. Take the shape from the network's "
          f"{r['n_pooled']} weeks and check the level")
    print(f"  against the series (own mean {r['own_mean']:,.0f}, consensus {r['consensus']:,.0f}).")

if __name__ == "__main__":
    main()
