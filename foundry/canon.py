"""The GFB canon: the tables every agent reads, reproduced from The Planner's Engine, Appendix F.

Change a number here and the worked examples in the book change with it; the tests assert
against these values, so a change you did not mean to make fails loudly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

COMPANY = "Gokul Foods & Beverages (GFB), Nashik"

# F.2 Sites -------------------------------------------------------------------
SITES = {
    "NSK": {"name": "Nashik plant", "kind": "plant", "serves": "beverages: nectar, juice, squash, pulp"},
    "BDI": {"name": "Baddi co-packer", "kind": "plant", "serves": "snacks: chakli, khakhra, namkeen, thandai"},
    "BHW": {"name": "Bhiwandi DC", "kind": "dc", "serves": "Mumbai, Pune, Gujarat"},
    "GGN": {"name": "Gurgaon DC", "kind": "dc", "serves": "Delhi NCR, Punjab, UP"},
    "KOL": {"name": "Kolkata DC", "kind": "dc", "serves": "East and North-East"},
    "CHN": {"name": "Chennai DC", "kind": "dc", "serves": "Tamil Nadu, Kerala, Karnataka"},
}
DCS = ["BHW", "GGN", "KOL", "CHN"]
PLANTS = ["NSK", "BDI"]

# F.3 Lanes: (from, to) -> transit days, rupees per case. ~1,100 cases per 32-foot truck.
LANES = {
    ("NSK", "BHW"): (2, 6),
    ("NSK", "CHN"): (5, 16),
    ("NSK", "KOL"): (4, 14),
    ("NSK", "GGN"): (3, 12),
    ("BDI", "GGN"): (2, 5),
    ("BDI", "CHN"): (7, 22),
    ("BDI", "KOL"): (5, 18),
    ("BDI", "BHW"): (3, 9),
    ("BHW", "CHN"): (3, 18),
    ("KOL", "CHN"): (4, 22),
    ("GGN", "CHN"): (4, 26),
    ("BHW", "GGN"): (3, 12),
}
TRUCK_CASES = 1100

# F.4 Parts: code -> (name, class, made_at, cost per case, units per case, annual value in crore)
PARTS = {
    "MN-1L": ("Mango nectar 1 L", "A", "NSK", 480, 12, 27.3),
    "MN-200": ("Mango nectar 200 ml", "A", "NSK", 300, 24, 12.6),
    "LJ-1L": ("Lime cooler 1 L", "B", "NSK", 420, 12, 6.3),
    "AJ-1L": ("Apple juice 1 L", "B", "NSK", 560, 12, 6.2),
    "JM-500": ("Jamun squash 500 ml", "C", "NSK", 390, 12, 2.0),
    "MP-1KG": ("Mango pulp 1 kg (B2B)", "B", "NSK", 1150, 10, 6.9),
    "GC-200": ("Chakli 200 g", "A", "BDI", 360, 24, 10.8),
    "KH-250": ("Khakhra 250 g", "C", "BDI", 330, 20, 3.1),
    "NM-500": ("Namkeen mix 500 g", "C", "BDI", 410, 12, 2.5),
    "TG-250": ("Thandai mix 250 g", "C", "BDI", 520, 24, 1.6),
}

# F.5 MN-1L by DC: weekly mean, weekly sd, lead time (weeks), on hand, open receipt
MN1L = {
    "BHW": {"mu": 4200, "sigma": 500, "L": 1, "on_hand": 5400, "open_receipt": 1200},
    "GGN": {"mu": 3100, "sigma": 420, "L": 1, "on_hand": 4650, "open_receipt": 0},
    "KOL": {"mu": 1050, "sigma": 210, "L": 1, "on_hand": 9450, "open_receipt": 0},
    "CHN": {"mu": 2600, "sigma": 450, "L": 2, "on_hand": 1480, "open_receipt": 0},
}

# F.6 Cost parameters
HOLDING_RATE = 0.20          # of case cost per year
TRADE_PRICE_MN1L = 560       # rupees per case
CONTRIBUTION_MN1L = 62       # rupees per case after freight and holding
CONTRIBUTION_SHARE = CONTRIBUTION_MN1L / PARTS["MN-1L"][3]   # ~0.129, used for the other SKUs
ORDER_COST = 18000
Z = {0.80: 0.842, 0.85: 1.036, 0.90: 1.282, 0.95: 1.645, 0.975: 1.960, 0.98: 2.054, 0.99: 2.326, 0.995: 2.576}


# Derived numbers --------------------------------------------------------------
def daily_demand(mu_week: float) -> float:
    """Weekly mean to a daily rate, as the book does (7-day week)."""
    return mu_week / 7.0


def days_of_supply(on_hand: float, mu_week: float) -> float:
    """Days of cover: on hand divided by daily demand. F.5: BHW 9.0, GGN 10.5, KOL 63.0, CHN 4.0."""
    d = daily_demand(mu_week)
    return on_hand / d if d else float("inf")


def safety_stock(sigma_week: float, lead_weeks: float, csl: float = 0.95) -> float:
    """SS = z * sigma * sqrt(L). F.5 at 95%: BHW 822, GGN 691, KOL 345, CHN 1,047."""
    return Z[csl] * sigma_week * math.sqrt(lead_weeks)


def reorder_point(mu_week: float, sigma_week: float, lead_weeks: float, csl: float = 0.95) -> float:
    """ROP = mu*L + SS. F.5 at 95%: BHW 5,022, GGN 3,791, KOL 1,395, CHN 6,247."""
    return mu_week * lead_weeks + safety_stock(sigma_week, lead_weeks, csl)


def annual_cases(part: str) -> float:
    """Annual value in crore / cost per case = annual cases (MN-1L: 569,400 per F.5)."""
    name, cls, made, cost, units, value_cr = PARTS[part]
    return value_cr * 1e7 / cost


def contribution_per_case(part: str) -> float:
    """MN-1L is 62 in the canon; the others use the same share of cost."""
    if part == "MN-1L":
        return float(CONTRIBUTION_MN1L)
    return round(PARTS[part][3] * CONTRIBUTION_SHARE, 1)


# DC share of demand, taken from the MN-1L row and applied to every SKU.
DC_SHARE = {dc: MN1L[dc]["mu"] / sum(r["mu"] for r in MN1L.values()) for dc in DCS}
# Coefficient of variation per DC, from the MN-1L row (CHN is the noisiest DC).
DC_CV = {dc: MN1L[dc]["sigma"] / MN1L[dc]["mu"] for dc in DCS}


def weekly_mean(part: str, dc: str) -> float:
    """Weekly mean demand for any SKU at any DC. MN-1L returns the canon row exactly."""
    if part == "MN-1L":
        return float(MN1L[dc]["mu"])
    return round(annual_cases(part) / 52.0 * DC_SHARE[dc], 0)


def weekly_sigma(part: str, dc: str) -> float:
    if part == "MN-1L":
        return float(MN1L[dc]["sigma"])
    return round(weekly_mean(part, dc) * DC_CV[dc], 0)


def lead_weeks(part: str, dc: str) -> int:
    """Lead time in weeks from the SKU's plant: transit days + 2 processing days, rounded up."""
    if part == "MN-1L":
        return MN1L[dc]["L"]
    plant = PARTS[part][2]
    transit, _ = LANES[(plant, dc)]
    return max(1, math.ceil((transit + 2) / 7.0))


@dataclass(frozen=True)
class Rebalancing:
    """F.11, the rebalancing case the book runs: Chennai short, Kolkata long."""
    chn_cover_days: float = 4.0
    chn_next_receipt_days: int = 12
    chn_target_cover_days: int = 14
    kol_surplus_above_days: int = 21


def rebalancing_case() -> dict:
    """Reproduce F.11: transfer 3,720 cases KOL -> CHN, lane cost 81,840, margin saved ~1.84 lakh."""
    chn = MN1L["CHN"]
    kol = MN1L["KOL"]
    d_chn = daily_demand(chn["mu"])
    d_kol = daily_demand(kol["mu"])
    need = (14 - days_of_supply(chn["on_hand"], chn["mu"])) * d_chn            # 3,720 (to 14 days)
    surplus = kol["on_hand"] - 21 * d_kol                                        # 6,300
    qty = min(need, surplus)
    transit, cost = LANES[("KOL", "CHN")]
    lane_cost = qty * cost
    days_short_avoided = 12 - 4                                                  # receipt in 12, cover 4
    margin_saved = days_short_avoided * d_chn * CONTRIBUTION_MN1L
    return {
        "need_cases": round(need), "surplus_cases": round(surplus), "transfer_cases": round(qty),
        "lane_days": transit, "lane_cost_inr": round(lane_cost), "margin_saved_inr": round(margin_saved),
    }


if __name__ == "__main__":
    for dc, r in MN1L.items():
        print(dc, "DOS", round(days_of_supply(r["on_hand"], r["mu"]), 1),
              "SS95", round(safety_stock(r["sigma"], r["L"])),
              "ROP95", round(reorder_point(r["mu"], r["sigma"], r["L"])))
    print(rebalancing_case())
