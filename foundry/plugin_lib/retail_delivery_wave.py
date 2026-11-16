"""Grocery retailer: urgency is hours to the next store delivery wave, not days of supply.

Stores are replenished in waves (06:00 and 14:00 at the example retailer). A DC that is short now
costs the next wave, whatever its days of supply says, so hours to impact is the smaller of the two.
"""
from __future__ import annotations


def hours_to_next_wave(store, profile, week, dc, sku, default_hours):
    sec = profile.section("delivery_waves")
    waves = [float(h) for h in sec.get("hours", [6, 14])]
    now = float(sec.get("now_hour", 4.0))
    ahead = [w - now for w in waves if w > now] or [24 - now + waves[0]]
    return min(default_hours, min(ahead))


def register(p) -> None:
    p.set_urgency_clock(hours_to_next_wave)
