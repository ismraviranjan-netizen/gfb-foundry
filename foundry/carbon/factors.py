"""Where every carbon number comes from: the profile's `carbon` section, with labelled defaults.

The core holds no company's distances, weights or prices. It holds one set of defaults (the
Smart Freight Centre's India values, May 2025) so that a profile without a carbon section still
gets an answer, flagged `defaults_used`, instead of a crash or a silent zero.

Fixed 8 Nov 2026 (day-one defect 2): the vehicle method used one payload (15 t) for every vehicle
class, so a 7 t medium truck was charged as if it carried 15 t (2.1x too much per truck-km) and a
25 t tractor-trailer as if it carried 15 t (40% too little). The payload now comes per class from
`payload_t_by_vehicle`, and the number of trucks a move needs is counted in that class's trucks.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from foundry.carbon.sources import GFB, ILL, sid
from foundry.profile import Profile

DEFAULT_ROAD = {"wtw": 0.0823, "ttw": 0.0632, "wtt": 0.0191, "load": 0.694, "empty": 0.120,
                "label": "default: SFC India v1.0, heavy CV 20-30 t diesel, WTW"}
DEFAULT_RAIL = {"wtw": 0.0106, "ttw": 0.0041, "wtt": 0.0064, "label": "default: SFC India v1.0, rail average, WTW"}
DEFAULT_PAYLOAD_T = 15.0        # the default heavy CV's payload, used only when a profile names no payload at all
KM_PER_TRANSIT_DAY = 450.0      # a fallback when a lane has no distance: an Indian FTL truck's day
DEFAULT_SHADOW_INR = 2500.0
DEFAULT_SOURCES = {"road": "S1", "rail": "S1", "diesel": "S1", "grid": "S5", "air": ILL, "lanes_km": ILL, "kg_per_case": ILL,
                   "payload": ILL, "expedite": ILL, "materials": ILL, "end_of_life": ILL, "eeio": ILL, "energy": GFB,
                   "spend": GFB, "distribution": ILL}


@dataclass
class Factors:
    raw: dict
    defaults_used: bool
    notes: list[str] = field(default_factory=list)

    # --- prices, methods, sources -------------------------------------------------------------
    @property
    def shadow_price(self) -> float:
        return float(self.raw.get("shadow_price_inr_per_t", DEFAULT_SHADOW_INR))

    @property
    def decision_method(self) -> str:
        return self.raw.get("decision_method", "vehicle")

    @property
    def reporting_method(self) -> str:
        return self.raw.get("reporting_method", "intensity")

    @property
    def default_vehicle(self) -> str:
        return (self.raw.get("road") or {}).get("vehicle", "heavy_cv2_diesel")

    def source(self, block: str) -> str:
        """The Sources-appendix id behind a block of the profile (S1, S5, ILL, GFB)."""
        return sid((self.raw.get("sources") or {}).get(block, DEFAULT_SOURCES.get(block, ILL)))

    # --- road and rail -----------------------------------------------------------------------
    def payload_t(self, vehicle: str | None = None) -> tuple[float, str]:
        """The rated payload of one vehicle of this class, and where the number came from."""
        name = vehicle or self.default_vehicle
        r = self.raw.get("road") or {}
        by = self.raw.get("payload_t_by_vehicle") or {}
        if name in by:
            return float(by[name]), f"profile payload_t_by_vehicle[{name}]"
        if name == self.default_vehicle and r.get("payload_t"):
            return float(r["payload_t"]), "profile road.payload_t"
        if name == self.default_vehicle:
            return DEFAULT_PAYLOAD_T, "default: 15 t heavy CV"
        raise KeyError(f"no payload for vehicle class {name}: add it to carbon.payload_t_by_vehicle "
                       f"(one payload for every class was day-one defect 2)")

    def road(self, vehicle: str | None = None) -> dict:
        r = self.raw.get("road", {})
        name = vehicle or self.default_vehicle
        f = dict((r.get("factors") or {}).get(name) or (DEFAULT_ROAD if name == self.default_vehicle else {}))
        if not f:
            raise KeyError(f"no road factor for vehicle class {name}")
        f["vehicle"] = name
        f["payload_t"], f["payload_source"] = self.payload_t(name)
        f["source"] = self.source("road")
        return f

    def kg_per_vehicle_km(self, vehicle: str | None = None) -> float:
        """A loaded truck-km, empty running included: the default intensity (kg per tonne-km at the
        default load) times the tonnes that class carries at that load. WTW."""
        f = self.road(vehicle)
        return f["wtw"] * f["payload_t"] * f["load"]

    def cases_per_vehicle(self, store, sku: str, vehicle: str | None = None) -> int:
        """How many cases of this SKU one vehicle of this class takes. The default class takes the
        network's truck (truck_cases, the number the rebalancing agent counts trucks in); another class
        takes the same truck scaled by payload, and never more than its payload by weight."""
        name = vehicle or self.default_vehicle
        tc = int(store.net.truck_cases)
        if name == self.default_vehicle:
            return tc
        p, _ = self.payload_t(name)
        p0, _ = self.payload_t(self.default_vehicle)
        kgc, _ = self.kg_per_case(store, sku)
        return max(1, min(math.floor(tc * p / p0), math.floor(p * 1000.0 / kgc)))

    def km_per_l(self, vehicle: str | None = None) -> tuple[float, str]:
        """Loaded fuel economy for the fuel method. The profile's carrier figure is for its own class;
        another class gets a figure derived back from the default intensity, and says so."""
        name = vehicle or self.default_vehicle
        road = self.raw.get("road") or {}
        by = road.get("km_per_l_by_vehicle") or {}
        if name in by:
            return float(by[name]), f"profile km_per_l_by_vehicle[{name}]"
        if name == self.default_vehicle:
            return float(road.get("km_per_l_loaded", 3.6)), "profile km_per_l_loaded (GFB's carriers, illustrative)"
        ttw_kg_per_vkm = self.road(name)["ttw"] * self.payload_t(name)[0] * self.road(name)["load"]
        kmpl = self.diesel()["ttw"] / ttw_kg_per_vkm
        return round(kmpl, 2), f"derived from the {name} default intensity (no carrier fuel data)"

    def rail(self) -> dict:
        r = self.raw.get("rail", {})
        f = dict(r.get("factor") or DEFAULT_RAIL)
        f["drayage_km"] = float(r.get("drayage_km", 30))
        f["source"] = self.source("rail")
        return f

    def rail_lane(self, a: str, b: str) -> dict | None:
        lane = ((self.raw.get("rail") or {}).get("lanes") or {}).get(f"{a}-{b}")
        return dict(lane) if lane else None

    def air(self) -> dict:
        return dict(self.raw.get("air") or {"wtw": 1.5, "label": "illustrative only"})

    def grid(self) -> tuple[float, str]:
        g = self.raw.get("grid") or {}
        return float(g.get("kg_per_kwh", 0.675)), g.get("label", "default: CEA v22.0 FY 2025-26: NOT VERIFIED")

    def grid_info(self) -> dict:
        g = dict(self.raw.get("grid") or {})
        kg, label = self.grid()
        return {"kg_per_kwh": kg, "label": label, "verified": bool(g.get("verified", False)), "source": self.source("grid"),
                "alt": g.get("alt"), "boundary": "generation CO2 only: no upstream fuel, no T&D losses"}

    def diesel(self) -> dict:
        d = (self.raw.get("road") or {}).get("diesel_kg_per_l") or {"ttw": 2.675, "wtt": 0.8058, "label": "default: SFC India v1.0 diesel"}
        d = dict(d)
        d["wtw"] = d["ttw"] + d["wtt"]
        d["source"] = self.source("diesel")
        return d

    # --- distances and weights -----------------------------------------------------------------
    def km(self, store, a: str, b: str) -> tuple[float | None, str]:
        km = (self.raw.get("lanes_km") or {}).get(f"{a}-{b}")
        if km:
            return float(km), "profile lanes_km (approximate road distance)"
        lane = store.net.lane(a, b)
        if lane:
            return float(lane[0]) * KM_PER_TRANSIT_DAY, f"estimated: {lane[0]} transit days x {KM_PER_TRANSIT_DAY:.0f} km"
        return None, f"no lane {a}->{b}"

    def kg_per_case(self, store, sku: str) -> tuple[float, str]:
        kg = (self.raw.get("kg_per_case") or {}).get(sku)
        if kg:
            return float(kg), "profile kg_per_case"
        part = store.net.parts.get(sku)
        units = int(part[4]) if part else 12
        return round(units * 0.55 + 0.4, 2), f"estimated: {units} units x 0.55 kg + 0.4 kg case"

    def approver(self, profile: Profile, action: str, dc: str | None = None) -> str:
        role = (self.raw.get("approvers") or {}).get(action)
        if not role:
            return profile.approver_for(action, dc)
        who = profile.people.get(role, role)
        if isinstance(who, dict):
            who = who.get(dc or "", who.get("ANY", role))
        return f"{who} ({role})"


def load(profile: Profile) -> Factors:
    raw = profile.section("carbon")
    if not raw:
        return Factors({}, True, ["the profile has no carbon section: SFC India defaults, km estimated from transit days"])
    return Factors(raw, False)


def trucks_for(cases: float, truck_cases: int) -> int:
    return max(1, math.ceil(cases / truck_cases)) if cases > 0 else 0
