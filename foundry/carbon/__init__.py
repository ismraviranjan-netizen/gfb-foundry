"""The Green Ledger: carbon next to cost in the gfb-foundry control tower.

    factors      every number's origin: the profile's carbon section, labelled defaults, source ids
    logistics    ISO 14083-shaped legs and hubs: intensity, vehicle and fuel methods, WTT / TTW / WTW
    decisions    carbon next to the money: modes, right-sizing, expedite, the approver card
    column       the console's carbon column for tower plans
    inventory    GFB's year by scope and category, with the spend-based cross-check
    docs, quality, tools, prompts, fake, agent
                 the supplier carbon-data agent (read-only, propose-only)
    sources      the Sources appendix ids (S1..S25), ILL and GFB

CLI: python3 -m foundry.carbon lanes | vehicles | hubs | transfer | methods | compare | expedite | card |
     column | inventory | spend | supplier | sources
"""
from foundry.carbon.column import plan_column, step_carbon, week_column
from foundry.carbon.decisions import (approver_card, breakeven_price, carbon_column, compare_modes, expedite_options, option,
                                      render_card, right_size)
from foundry.carbon.factors import Factors, load
from foundry.carbon.inventory import inventory, spend_check
from foundry.carbon.logistics import hub_table, lane_table, methods_table, road_kg, transfer_footprint, vehicle_table
from foundry.carbon.quality import score as score_document
from foundry.carbon.sources import SOURCES

__all__ = ["Factors", "load", "road_kg", "transfer_footprint", "lane_table", "hub_table", "vehicle_table", "methods_table",
           "option", "breakeven_price", "compare_modes", "right_size", "expedite_options", "approver_card", "render_card",
           "carbon_column", "plan_column", "step_carbon", "week_column", "inventory", "spend_check", "score_document", "SOURCES"]
