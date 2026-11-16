# The known lies: what each field usually gets wrong, by source system (Part 7)

The third question for every mapped field. Ask it before the integration, and write the answer in
the mapping sheet's notes column. The agent's data-stale gatherer and the mapping notes are the two
places these are caught.

| Table.field | Typical source | The usual lie | The check |
|---|---|---|---|
| inventory.on_hand | WMS stock snapshot | a scheduled job that ran yesterday, read today; blocked/quality stock counted as available | snapshot_age_h from the job's own timestamp; separate available from blocked |
| inventory.snapshot_age_h | integration layer | nobody records it | compute it from the file's write time at load; never trust a column called "last_updated" without asking what updates it |
| orders.on_time | TMS / ERP delivery | "promised date" is the customer's request, or the planner's confirmation, or the carrier's ETA, depending on who built the report | pick one definition, write it in the sheet, and re-derive OTIF yourself from the raw dates |
| orders.in_full | ERP | partial deliveries closed as complete when the balance is cancelled | compare shipped to requested, not to "open quantity" |
| orders.requested | ERP sales orders | sell-in to distributors, not sell-out to shops | mark which it is in the sheet; the spike gatherer needs to know |
| demand.actual | planning tool | the same sell-in / sell-out confusion; promo weeks already "cleaned" by a planner | ask for the raw series and the cleaned one; use the raw one for sensing |
| demand.forecast | planning tool | the number as of today, overwritten weekly; no history of what the forecast was at the time | ask for forecast snapshots by version; without them, the back-test is fiction |
| shipments.late_days | carrier events / TMS | rounded to whole days; a 20-hour slip reads as 0 or 1 depending on the midnight | keep hours where you can; set late_truck_days from the rounding you have |
| shipments.carrier | TMS | the contracted carrier, not the subcontractor who actually ran the truck | ask; the alternate-carrier playbook depends on it |
| dock.avg_dwell_h | yard / gate system | trucks that queued outside the gate are not in the gate log | compare gate time with the carrier's arrival event where both exist |
| receipts.planned | ERP stock transfer orders | re-planned after the miss, so the miss disappears | take the STO as it stood on the Friday before the week |
| receipts.received | WMS goods receipt | posted when counted, which can be days after arrival | use the arrival event for timing and the receipt for quantity |
| promos.uplift_pct | trade marketing sheet | planned uplift, never the realised one; weeks and SKUs drift after the sheet was made | learn the uplift from history; use the sheet only for which weeks and which SKUs |
