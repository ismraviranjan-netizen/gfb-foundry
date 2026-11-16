"""The inventory rebalancing agent: which DC is short, which is long for the same SKU, how many
cases to move on which lane, and whether the move pays. Propose-only; the receiving DC manager signs,
the sending DC manager co-signs below the reorder point, the logistics lead books the truck."""
