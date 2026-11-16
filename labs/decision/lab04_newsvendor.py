"""Lab 4 · The newsvendor: which quantile to stock to, at four companies (all fictional but GFB's
numbers, which come from the network). Demand is normal(mu, sigma) for one period.
  cu  what one case short costs (the margin lost, plus any penalty)
  co  what one case left over costs (a period's holding, plus waste or markdown)
  CR  = cu / (cu + co): stock to that quantile of demand
"""
from __future__ import annotations

from labs.decision import core
from foundry.network import GFB

ARCHETYPES = [
    # name, item, currency, mu, sigma, cu, co, period
    ("GFB (Indian CPG)", "mango nectar 1 L at CHN", "INR", 2600.0, 450.0, GFB.contribution_per_case("MN-1L"),
     round(GFB.holding_cost_per_case_day("MN-1L") * 7, 2), "week"),
    ("Northern Brewing OpCo", "keg lager at one depot", "EUR", 900.0, 150.0, 25.5, 1.2, "week"),
    ("Meridian Pharma", "insulin pens at a regional depot", "INR", 800.0, 120.0, 2800.0, 35.0, "week"),
    ("Everyday Grocers", "strawberry punnets, one store cluster", "INR", 400.0, 110.0, 40.0, 70.0, "day"),
]


def curve(mu, sigma, cu, co, n=61):
    lo, hi = mu - 2.5 * sigma, mu + 3.0 * sigma
    xs = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    return [(x, core.newsvendor_cost(mu, sigma, x, cu, co)) for x in xs]


def run() -> dict:
    rows = []
    for name, item, cur, mu, sigma, cu, co, period in ARCHETYPES:
        cr = core.critical_ratio(cu, co)
        q = core.normal_stock(mu, sigma, cr)
        rows.append({"company": name, "item": item, "currency": cur, "mu": mu, "sigma": sigma, "cu": cu, "co": co, "period": period,
                     "cr": round(cr, 3), "z": round(core.N01.inv_cdf(cr), 2), "stock": round(q),
                     "cost_at_cr": round(core.newsvendor_cost(mu, sigma, q, cu, co)),
                     "cost_at_p50": round(core.newsvendor_cost(mu, sigma, mu, cu, co)),
                     "cost_at_p95": round(core.newsvendor_cost(mu, sigma, core.normal_stock(mu, sigma, 0.95), cu, co)),
                     "curve": curve(mu, sigma, cu, co)})
    # GFB's reorder point is a quantile too: P95 of demand over the two-week lead time into Chennai
    mu_l, sd_l = 2600 * 2, 450 * 2 ** 0.5
    rop = core.normal_stock(mu_l, sd_l, 0.95)
    return {"rows": rows, "chn_rop": round(rop), "chn_rop_network": round(GFB.reorder_point("MN-1L", "CHN")),
            "chn_lead_mu": mu_l, "chn_lead_sd": round(sd_l)}


def main() -> None:
    r = run()
    print(f"  {'company':24}{'cu':>8}{'co':>7}{'CR':>7}{'z':>6}{'stock':>7}{'mean':>7}{'cost@CR':>9}{'cost@P95':>9}{'cost@P50':>9}")
    for x in r["rows"]:
        print(f"  {x['company']:24}{x['cu']:8.2f}{x['co']:7.2f}{x['cr']:7.3f}{x['z']:6.2f}{x['stock']:7,}{x['mu']:7,.0f}"
              f"{x['cost_at_cr']:9,}{x['cost_at_p95']:9,}{x['cost_at_p50']:9,}")
    print(f"  CHN MN-1L reorder point = P95 of lead-time demand: mean {r['chn_lead_mu']:,}, sd {r['chn_lead_sd']}, "
          f"P95 {r['chn_rop']:,} (the network says {r['chn_rop_network']:,})")
    g, e = r["rows"][0], r["rows"][3]
    print(f"Decision: the cost ratio picks the quantile: GFB's nectar to CR {g['cr']:.3f} "
          f"({g['stock']:,} cases),")
    print(f"  {e['company']}' strawberries to CR {e['cr']:.3f} ({e['stock']:,}). Stocking GFB to the mean")
    print(f"  costs {g['cost_at_p50'] / g['cost_at_cr']:.1f}x its cost at the CR.")

if __name__ == "__main__":
    main()
