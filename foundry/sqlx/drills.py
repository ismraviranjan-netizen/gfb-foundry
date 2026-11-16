"""The SQL drill deck for Talk to Data: every hand-written exercise in the book, with its key.

One source for three readers. The book prints the exercise and, pages later, the key with its real output
(build_t2d.py runs the key at build time). The practice CLI prints the exercise and checks your answer
against the key's rows:

    python3 -m foundry.sqlx drill 3.2                     # the exercise
    python3 -m foundry.sqlx drill 3.2 --check mine.sql    # your rows against the key's rows
    python3 -m foundry.sqlx drill 3.2 --key               # the key and its output

A check compares result sets, not text: column names and column order do not matter, extra columns are
allowed, row order matters only where the drill says `ordered`. tests/test_sqlx_drills.py runs every key.
"""
from __future__ import annotations

D = []


def drill(id, kind, title, ask, sql, why, minutes=5, ordered=False, store="canonical", bad_sql=None, predict=None):
    D.append({"id": id, "kind": kind, "title": title, "ask": ask, "sql": sql.strip(), "why": why, "minutes": minutes,
              "ordered": ordered, "store": store, "bad_sql": bad_sql.strip() if bad_sql else None, "predict": predict})


# ===================================================================== chapter 1: tables, rows, the first SELECT
drill("1.1", "Write", "The SKU list",
      "List the ten SKUs with their name and ABC class, in SKU-code order.",
      """
SELECT sku, name, class
FROM dim_sku
ORDER BY sku
""", "`ORDER BY sku` sorts text codes alphabetically, so AJ-1L comes first. Without ORDER BY, SQL promises no "
     "order at all: the rows come back in whatever order the engine finds convenient, and that can change.",
      minutes=3, ordered=True)

drill("1.2", "Write", "The five biggest orders",
      "The five largest orders by cases requested at Chennai in week 12: order id, SKU and cases requested, largest first.",
      """
SELECT order_id, sku, requested
FROM orders
WHERE week = 12 AND dc = 'CHN'
ORDER BY requested DESC, order_id
LIMIT 5
""", "`DESC` reverses the sort; `LIMIT 5` keeps the top five. The second sort key (`order_id`) breaks ties, so the "
     "answer is the same every time you run it.", minutes=4, ordered=True)

drill("1.3", "Predict", "How many orders?",
      "Without running anything, predict how many rows `orders` holds. You know: 12 weeks, 4 DCs, 80 orders per DC per week. "
      "Then write the query that checks you.",
      """
SELECT COUNT(*) AS orders
FROM orders
""", "12 x 4 x 80 = 3,840. Predicting a row count before you run is the cheapest bug detector there is: when the "
     "count surprises you, the query or your picture of the data is wrong.", minutes=3,
      predict="rows in orders: ______")

drill("1.4", "Write", "The carriers",
      "Which carriers appear in `shipments`? One row each, alphabetical.",
      """
SELECT DISTINCT carrier
FROM shipments
ORDER BY carrier
""", "`DISTINCT` removes duplicate rows from the result: 683 trucks, three carriers.", minutes=3, ordered=True)

# ===================================================================== chapter 2: WHERE
drill("2.1", "Write", "The short orders",
      "Chennai's mango nectar 1 L (MN-1L) orders in week 12 that were not delivered in full: order id, cases requested, cases shipped.",
      """
SELECT order_id, requested, shipped
FROM orders
WHERE week = 12 AND dc = 'CHN' AND sku = 'MN-1L' AND in_full = 0
ORDER BY order_id
""", "Four of the twelve orders: one part-shipped (90 of 233) and three with nothing shipped. These four are the "
     "whole of Chennai's OTIF drop: 8 of 12 is 0.6667.", minutes=4)

drill("2.2", "Write", "Every late truck in week 12",
      "Every inbound truck in week 12 that arrived late at all: truck id, destination DC, SKU, cases and days late.",
      """
SELECT truck_id, lane_to AS dc, sku, cases, late_days
FROM shipments
WHERE week = 12 AND late_days > 0
ORDER BY late_days DESC, truck_id
""", "Five trucks. Two Sahyadri trucks into Chennai are 3 days late; three trucks are exactly 1.0 day late. "
     "Whether those three are 'late' is a definition, not a fact (Chapter 11).", minutes=4)

drill("2.3", "Fix", "AND before OR",
      "A planner wanted week-12 orders at Chennai and Gurgaon and got 1,040 rows. Explain the number, then fix the query.",
      """
SELECT COUNT(*) AS orders
FROM orders
WHERE dc IN ('CHN', 'GGN') AND week = 12
""", "AND binds tighter than OR, so the broken query reads `dc = 'CHN' OR (dc = 'GGN' AND week = 12)`: all 960 Chennai "
     "orders plus Gurgaon's 80 = 1,040. `IN (...)` (or brackets) says what was meant: 160.", minutes=5,
      bad_sql="""
SELECT COUNT(*) AS orders
FROM orders
WHERE dc = 'CHN' OR dc = 'GGN' AND week = 12
""")

drill("2.4", "Write", "The mango SKUs",
      "Which SKUs have 'mango' in their name, whatever the capitals? Code and name.",
      """
SELECT sku, name
FROM dim_sku
WHERE LOWER(name) LIKE '%mango%'
ORDER BY sku
""", "`%` matches any run of characters. SQLite's LIKE ignores case for plain letters anyway; LOWER() makes the intent "
     "explicit and keeps the query correct on engines where LIKE is case-sensitive (Postgres, Snowflake; ILIKE "
     "exists there). Three SKUs: this is exactly why 'mango nectar' needs a clarifying question.", minutes=3)

drill("2.5", "Write", "The long lanes",
      "Which lanes in `dim_lane` take more than four days? From, to, days and cost per case, longest first.",
      """
SELECT lane_from, lane_to, days, cost_per_case
FROM dim_lane
WHERE days > 4
ORDER BY days DESC, lane_from
""", "Both plants are five or more days from Chennai, and Baddi is five from Kolkata. Chennai holds four days of cover on "
     "a five-day lane: the structural reason one late truck empties it.", minutes=3, ordered=True)

# ===================================================================== chapter 3: aggregates and GROUP BY
drill("3.1", "Write", "The week in four rows",
      "For week 12, one row per DC: number of orders, cases requested, cases shipped.",
      """
SELECT dc, COUNT(*) AS orders, SUM(requested) AS requested, SUM(shipped) AS shipped
FROM orders
WHERE week = 12
GROUP BY dc
ORDER BY dc
""", "GROUP BY makes one output row per distinct dc; every other column must be an aggregate (COUNT, SUM) or in the "
     "GROUP BY. SQLite lets you break that rule silently; most engines refuse.", minutes=4)

drill("3.2", "Write", "OTIF by hand",
      "OTIF by DC for week 12: orders, orders both on time and in full, and OTIF to four decimals.",
      """
SELECT dc,
       COUNT(*) AS orders,
       SUM(CASE WHEN on_time = 1 AND in_full = 1 THEN 1 ELSE 0 END) AS otif_orders,
       ROUND(1.0 * SUM(CASE WHEN on_time = 1 AND in_full = 1 THEN 1 ELSE 0 END)
             / COUNT(*), 4) AS otif
FROM orders
WHERE week = 12
GROUP BY dc
ORDER BY dc
""", "The same shape as the semantic layer's otif metric, written by hand: Gurgaon 0.75 (60 of 80), Chennai 0.9 (72 of "
     "80). `1.0 *` turns the integer sum into a real before the division.", minutes=6)

drill("3.3", "Write", "Fill rate by DC",
      "Fill rate (cases shipped divided by cases requested) by DC for week 12, to four decimals.",
      """
SELECT dc, ROUND(1.0 * SUM(shipped) / SUM(requested), 4) AS fill_rate
FROM orders
WHERE week = 12
GROUP BY dc
ORDER BY fill_rate
""", "Chennai 0.8823 is the canonical number. Fill rate counts cases; OTIF counts orders. The same week can look "
     "different through each.", minutes=4)

drill("3.4", "Write", "HAVING: the pairs below 95%",
      "Which DC and SKU pairs had a fill rate below 0.95 in week 12? DC, SKU and fill rate.",
      """
SELECT dc, sku, ROUND(1.0 * SUM(shipped) / SUM(requested), 4) AS fill_rate
FROM orders
WHERE week = 12
GROUP BY dc, sku
HAVING 1.0 * SUM(shipped) / SUM(requested) < 0.95
ORDER BY fill_rate
""", "WHERE filters rows before grouping; HAVING filters groups after. A condition on an aggregate can only go in "
     "HAVING. Two pairs: Bhiwandi's pulp and Chennai's nectar.", minutes=5)

drill("3.5", "Fix", "The fill rate that is always zero",
      "This query returns 0 for Chennai's week-12 fill rate. Why? Fix it.",
      """
SELECT 1.0 * SUM(shipped) / SUM(requested) AS fill_rate
FROM orders
WHERE week = 12 AND dc = 'CHN'
""", "Integer divided by integer is an integer in SQLite (and Postgres, Redshift, Athena, SQL Server): 8,247 / 9,347 "
     "truncates to 0. Multiply by 1.0 (or CAST AS REAL) first. The semantic layer's forecast_error had exactly this "
     "bug once; a test now pins it.", minutes=4,
      bad_sql="""
SELECT SUM(shipped) / SUM(requested) AS fill_rate
FROM orders
WHERE week = 12 AND dc = 'CHN'
""")

drill("3.6", "Write", "Carriers over twelve weeks",
      "For each carrier over all twelve weeks: trucks, trucks more than one day late, and the share late to three decimals.",
      """
SELECT carrier,
       COUNT(*) AS trucks,
       SUM(CASE WHEN late_days > 1.0 THEN 1 ELSE 0 END) AS late_trucks,
       ROUND(1.0 * SUM(CASE WHEN late_days > 1.0 THEN 1 ELSE 0 END)
             / COUNT(*), 3) AS late_share
FROM shipments
GROUP BY carrier
ORDER BY late_share DESC
""", "Only Sahyadri has any truck more than a day late, and both are the week-12 Chennai trucks. Twelve weeks of "
     "history say Sahyadri is not a bad carrier; one week says it had a bad week. That is the difference between an "
     "incident and a trend.", minutes=5)

# ===================================================================== chapter 4: CASE, NULL and division
drill("4.1", "Write", "Full, part or nothing",
      "Classify each of Chennai's MN-1L orders in week 12 as 'full', 'part' or 'nothing', and count each class.",
      """
SELECT CASE WHEN shipped = requested THEN 'full'
            WHEN shipped > 0 THEN 'part'
            ELSE 'nothing' END AS outcome,
       COUNT(*) AS orders
FROM orders
WHERE week = 12 AND dc = 'CHN' AND sku = 'MN-1L'
GROUP BY outcome
ORDER BY orders DESC
""", "CASE tries its WHEN lines in order and stops at the first true one. SQLite lets GROUP BY use the alias "
     "`outcome`; on engines that do not, repeat the CASE or wrap it in a CTE.", minutes=5)

drill("4.2", "Write", "Three counts in one pass",
      "For week 12, one row per DC with three counts: orders on time, orders in full, orders both.",
      """
SELECT dc,
       SUM(on_time) AS on_time,
       SUM(in_full) AS in_full,
       SUM(CASE WHEN on_time = 1 AND in_full = 1 THEN 1 ELSE 0 END) AS both_ok
FROM orders
WHERE week = 12
GROUP BY dc
ORDER BY dc
""", "Conditional aggregation: one scan, many counts. Gurgaon's orders were all in full but twenty were late: a dock "
     "failure. Chennai lost four orders to lateness and four to short shipment, never the same order, so 72 passed both. "
     "Two different failures behind two OTIF drops.", minutes=5)

drill("4.3", "Write", "Weeks as columns",
      "Chennai MN-1L OTIF with one column each for weeks 10, 11 and 12 (a pivot), to four decimals.",
      """
SELECT ROUND(AVG(CASE WHEN week = 10 THEN on_time * in_full END), 4) AS w10,
       ROUND(AVG(CASE WHEN week = 11 THEN on_time * in_full END), 4) AS w11,
       ROUND(AVG(CASE WHEN week = 12 THEN on_time * in_full END), 4) AS w12
FROM orders
WHERE dc = 'CHN' AND sku = 'MN-1L' AND week BETWEEN 10 AND 12
""", "A CASE with no ELSE returns NULL, and AVG ignores NULLs, so each column averages only its own week. "
     "on_time * in_full is 1 only when both are 1.", minutes=6)

drill("4.4", "Predict", "What NULL does",
      "Predict each value before you run: (a) `1 / 0`; (b) `1.0 * NULL`; (c) COUNT(*) and COUNT(x) over the two rows "
      "x = NULL and x = 4; (d) AVG(x) over the same rows.",
      """
WITH t AS (SELECT NULL AS x UNION ALL SELECT 4)
SELECT (SELECT 1 / 0) AS a, (SELECT 1.0 * NULL) AS b,
       COUNT(*) AS c_star, COUNT(x) AS c_x, AVG(x) AS d
FROM t
""", "SQLite returns NULL for a division by zero; Postgres, Snowflake, BigQuery and SQL Server raise an error. "
     "Anything combined with NULL is NULL. COUNT(*) counts rows (2); COUNT(x) counts non-NULL values (1); AVG ignores "
     "the NULL, so it is 4, not 2.", minutes=4, predict="(a) ____ (b) ____ (c) ____ and ____ (d) ____")

# ===================================================================== chapter 5: JOINs
drill("5.1", "Write", "Names on the orders",
      "Week-12 Chennai orders for class A SKUs only: SKU, its name and the number of orders. Use dim_sku.",
      """
SELECT o.sku, s.name, COUNT(*) AS orders
FROM orders o
JOIN dim_sku s ON s.sku = o.sku
WHERE o.week = 12 AND o.dc = 'CHN' AND s.class = 'A'
GROUP BY o.sku, s.name
ORDER BY o.sku
""", "An inner JOIN keeps rows that match on both sides. The class lives in the dimension table, so the filter needs "
     "the join. Short aliases (o, s) keep long queries readable.", minutes=5)

drill("5.2", "Write", "Days of supply at Chennai",
      "Days of supply for every SKU at Chennai in week 12 (on hand divided by daily demand from dim_dc_sku), lowest first.",
      """
SELECT i.sku, i.on_hand, d.daily_demand,
       ROUND(i.on_hand / d.daily_demand, 1) AS days_of_supply
FROM inventory i
JOIN dim_dc_sku d ON d.dc = i.dc AND d.sku = i.sku
WHERE i.week = 12 AND i.dc = 'CHN'
ORDER BY days_of_supply, i.sku
""", "A two-column join key: a DC-SKU pair, not a SKU alone. Join on sku only and every inventory row would meet four "
     "dim_dc_sku rows, one per DC. MN-1L sits at 0.0 days.", minutes=5, ordered=True)

drill("5.3", "Write", "Lanes and their trucks",
      "For week 12, each inbound lane that had trucks: planned transit days, trucks, and trucks more than a day late.",
      """
SELECT s.lane_from, s.lane_to, l.days,
       COUNT(*) AS trucks,
       SUM(CASE WHEN s.late_days > 1.0 THEN 1 ELSE 0 END) AS late_trucks
FROM shipments s
JOIN dim_lane l ON l.lane_from = s.lane_from AND l.lane_to = s.lane_to
WHERE s.week = 12
GROUP BY s.lane_from, s.lane_to, l.days
ORDER BY late_trucks DESC, l.days DESC
""", "Eight plant-to-DC lanes carried trucks. The only lane with late trucks is Nashik to Chennai, five days long.",
      minutes=5)

drill("5.4", "Write", "The lanes nobody drove",
      "Which lanes in dim_lane had no truck at all in week 12? Use a LEFT JOIN.",
      """
SELECT l.lane_from, l.lane_to, l.days
FROM dim_lane l
LEFT JOIN shipments s
  ON s.lane_from = l.lane_from AND s.lane_to = l.lane_to AND s.week = 12
WHERE s.truck_id IS NULL
ORDER BY l.lane_from, l.lane_to
""", "A LEFT JOIN keeps every lane; where no truck matched, the shipment columns are NULL, and `IS NULL` keeps exactly "
     "those. The week filter sits in ON, not WHERE: in WHERE it would delete the NULL rows and turn the LEFT JOIN back "
     "into an inner one. The four empty lanes are the inter-DC lanes: transfers are proposals, not trucks, until someone signs.",
      minutes=6)

drill("5.5", "Fix", "The join that multiplied the cases",
      "This query claims Chennai shipped 6,822 cases of MN-1L in week 12, on 36 trucks. The orders say 2,274 cases and "
      "the shipments table three trucks. Find the fan-out and fix it.",
      """
WITH o AS (SELECT dc, sku, SUM(shipped) AS shipped
           FROM orders WHERE week = 12 GROUP BY dc, sku),
     t AS (SELECT lane_to AS dc, sku, COUNT(*) AS trucks
           FROM shipments WHERE week = 12 GROUP BY lane_to, sku)
SELECT o.dc, o.sku, o.shipped, t.trucks
FROM o JOIN t ON t.dc = o.dc AND t.sku = o.sku
WHERE o.dc = 'CHN' AND o.sku = 'MN-1L'
""", "Twelve order rows met three truck rows on the same key, so every order was counted three times (and the trucks "
     "twelve times). Aggregate each table to the grain you want first, then join one row to one row.", minutes=7,
      bad_sql="""
SELECT o.dc, o.sku, SUM(o.shipped) AS shipped, COUNT(*) AS trucks
FROM orders o
JOIN shipments s ON s.week = o.week AND s.lane_to = o.dc AND s.sku = o.sku
WHERE o.week = 12 AND o.dc = 'CHN' AND o.sku = 'MN-1L'
GROUP BY o.dc, o.sku
""")

drill("5.6", "Write", "Stock value at cost",
      "Stock value at cost by DC for week 12 (on hand times cost per case), largest first.",
      """
SELECT i.dc, ROUND(SUM(i.on_hand * s.cost_per_case)) AS stock_value
FROM inventory i
JOIN dim_sku s ON s.sku = i.sku
WHERE i.week = 12
GROUP BY i.dc
ORDER BY stock_value DESC
""", "Kolkata's number is low partly because of the stale 400-case snapshot: a money KPI is only as true as the "
     "snapshot under it.", minutes=4)

# ===================================================================== chapter 6: subqueries and CTEs
drill("6.1", "Write", "Above its own average",
      "Which SKUs at Bhiwandi sold more in week 12 than their own twelve-week average? SKU, week-12 sell-out and the average.",
      """
WITH avg12 AS (SELECT sku, AVG(actual) AS avg_actual
               FROM demand WHERE dc = 'BHW' GROUP BY sku)
SELECT d.sku, d.actual, ROUND(a.avg_actual, 1) AS avg_actual
FROM demand d
JOIN avg12 a ON a.sku = d.sku
WHERE d.dc = 'BHW' AND d.week = 12 AND d.actual > a.avg_actual
ORDER BY d.actual - a.avg_actual DESC
""", "A CTE names a step so the next step can use it. Chakli tops the list: the Diwali promotion doubled it.",
      minutes=6)

drill("6.2", "Write", "The most late trucks, ties included",
      "Which DC had the most trucks more than one day late in week 12? If two DCs tie, show both.",
      """
WITH t AS (SELECT lane_to AS dc, COUNT(*) AS late_trucks
           FROM shipments
           WHERE week = 12 AND late_days > 1.0
           GROUP BY lane_to)
SELECT dc, late_trucks
FROM t
WHERE late_trucks = (SELECT MAX(late_trucks) FROM t)
""", "`ORDER BY ... LIMIT 1` drops a tie silently; comparing with MAX keeps it. The eval's gold for F01 is written "
     "this way for that reason.", minutes=5)

drill("6.3", "Write", "Trucks that met a slow dock",
      "Week-12 trucks into any DC whose average dock dwell that week was above 5 hours: DC and truck count. Use EXISTS.",
      """
SELECT s.lane_to AS dc, COUNT(*) AS trucks
FROM shipments s
WHERE s.week = 12
  AND EXISTS (SELECT 1 FROM dock k
              WHERE k.dc = s.lane_to AND k.week = s.week AND k.avg_dwell_h > 5)
GROUP BY s.lane_to
""", "EXISTS asks a yes-or-no question per row and never multiplies rows, so it cannot fan out the way a join can. "
     "Only Gurgaon qualifies.", minutes=5)

drill("6.4", "Rewrite", "The query that never finished",
      "This correlated subquery sums each receipt row's shipped cases. Predict whether it finishes inside the warehouse's "
      "step budget, run it, then rewrite it so it does.",
      """
WITH s AS (SELECT week, dc, sku, SUM(shipped) AS shipped
           FROM orders GROUP BY week, dc, sku)
SELECT COUNT(*) AS rows_checked,
       SUM(CASE WHEN r.received >= 0.8 * COALESCE(s.shipped, 0)
                THEN 1 ELSE 0 END) AS covered
FROM receipts r
LEFT JOIN s ON s.week = r.week AND s.dc = r.dc AND s.sku = r.sku
""", "The correlated version re-reads all 3,840 orders for each of 480 receipt rows: 1.8 million row visits, past the "
     "budget, and the guard stops it. Sum once in a CTE, then join: one pass over each table. This is the exact "
     "rewrite the impossible_drop quality check needed.", minutes=8,
      bad_sql="""
SELECT COUNT(*) AS rows_checked,
       SUM(CASE WHEN r.received >= 0.8 * (SELECT COALESCE(SUM(o.shipped), 0)
                                          FROM orders o
                                          WHERE o.week = r.week AND o.dc = r.dc
                                            AND o.sku = r.sku)
                THEN 1 ELSE 0 END) AS covered
FROM receipts r
""", predict="finishes inside the budget? yes / no")

# ===================================================================== chapter 7: windows I
drill("7.1", "Write", "Cases shipped, running",
      "Chennai MN-1L: cases shipped each week and the running total, weeks 1 to 12.",
      """
SELECT week, SUM(shipped) AS shipped,
       SUM(SUM(shipped)) OVER (ORDER BY week) AS running_shipped
FROM orders
WHERE dc = 'CHN' AND sku = 'MN-1L'
GROUP BY week
ORDER BY week
""", "The window runs after GROUP BY, so `SUM(SUM(shipped)) OVER (...)` sums the weekly sums. ORDER BY inside OVER "
     "makes the frame 'from the first week to this one'.", minutes=5, ordered=True)

drill("7.2", "Write", "The book balance",
      "Rebuild Chennai MN-1L's stock from its flows: week 1's snapshot, then plus received and minus shipped, week by week. "
      "Show the snapshot beside your book balance.",
      """
WITH f AS (
  SELECT i.week, i.on_hand, r.received, o.shipped
  FROM inventory i
  JOIN receipts r ON r.week = i.week AND r.dc = i.dc AND r.sku = i.sku
  JOIN (SELECT week, dc, sku, SUM(shipped) AS shipped FROM orders GROUP BY week, dc, sku) o
    ON o.week = i.week AND o.dc = i.dc AND o.sku = i.sku
  WHERE i.dc = 'CHN' AND i.sku = 'MN-1L')
SELECT week, on_hand,
       FIRST_VALUE(on_hand) OVER w
         + SUM(received - shipped) OVER w
         - (FIRST_VALUE(received) OVER w - FIRST_VALUE(shipped) OVER w) AS book_balance
FROM f
WINDOW w AS (ORDER BY week ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
ORDER BY week
""", "Week 1's snapshot already includes week 1's flows, so the running sum must start from week 2: that is the "
     "subtracted term. The two columns agree every week, down to week 12's zero. A named WINDOW saves repeating "
     "the frame.", minutes=10, ordered=True)

drill("7.3", "Write", "The lie finder",
      "Run the book balance for every DC and SKU, and return only the rows where the snapshot disagrees with the book.",
      """
WITH f AS (
  SELECT i.week, i.dc, i.sku, i.on_hand, r.received, COALESCE(o.shipped, 0) AS shipped
  FROM inventory i
  JOIN receipts r ON r.week = i.week AND r.dc = i.dc AND r.sku = i.sku
  LEFT JOIN (SELECT week, dc, sku, SUM(shipped) AS shipped
             FROM orders GROUP BY week, dc, sku) o
    ON o.week = i.week AND o.dc = i.dc AND o.sku = i.sku),
b AS (
  SELECT week, dc, sku, on_hand,
         FIRST_VALUE(on_hand) OVER w + SUM(received - shipped) OVER w
           - (FIRST_VALUE(received) OVER w - FIRST_VALUE(shipped) OVER w) AS book
  FROM f
  WINDOW w AS (PARTITION BY dc, sku ORDER BY week ROWS UNBOUNDED PRECEDING))
SELECT week, dc, sku, on_hand, book, on_hand - book AS gap
FROM b
WHERE on_hand <> book
""", "480 rows checked, one disagrees: Kolkata MN-1L in week 12 claims 400 cases where the flows say 9,322. "
     "PARTITION BY restarts the running sum for each DC-SKU pair. This is a data-quality check written as a window.",
      minutes=10)

drill("7.4", "Write", "Three ways to rank a tie",
      "Rank the DCs by week-12 fill rate, best first, with RANK, DENSE_RANK and ROW_NUMBER side by side.",
      """
WITH f AS (SELECT dc, ROUND(1.0 * SUM(shipped) / SUM(requested), 4) AS fill_rate
           FROM orders WHERE week = 12 GROUP BY dc)
SELECT dc, fill_rate,
       RANK()       OVER (ORDER BY fill_rate DESC) AS rnk,
       DENSE_RANK() OVER (ORDER BY fill_rate DESC) AS dense,
       ROW_NUMBER() OVER (ORDER BY fill_rate DESC, dc) AS row_no
FROM f
ORDER BY fill_rate DESC, dc
""", "Gurgaon and Kolkata tie at 1.0. RANK gives 1, 1, 3, 4 (it skips); DENSE_RANK gives 1, 1, 2, 3; ROW_NUMBER "
     "forces 1, 2, 3, 4 and needs a tie-breaker (dc) to be repeatable. Say which one you mean.", minutes=6, ordered=True)

drill("7.5", "Write", "The best week of each SKU",
      "For each SKU at Bhiwandi, the week with the highest sell-out and that sell-out. One row per SKU; if two weeks tie, "
      "take the later week.",
      """
WITH r AS (SELECT sku, week, actual,
                  ROW_NUMBER() OVER (PARTITION BY sku
                                     ORDER BY actual DESC, week DESC) AS rn
           FROM demand WHERE dc = 'BHW')
SELECT sku, week, actual
FROM r
WHERE rn = 1
ORDER BY sku
""", "Top-1 per group: number the rows inside each partition, keep number 1. Chakli's 4,426 happened in week 11 and "
     "again in week 12; the tie rule picks 12. Without the rule the answer can change between runs.", minutes=6)

# ===================================================================== chapter 8: windows II
drill("8.1", "Write", "OTIF, week on week",
      "Gurgaon's OTIF by week with the change from the week before, to four decimals.",
      """
WITH w AS (SELECT week, AVG(on_time * in_full) AS otif
           FROM orders WHERE dc = 'GGN' GROUP BY week)
SELECT week, ROUND(otif, 4) AS otif,
       ROUND(otif - LAG(otif) OVER (ORDER BY week), 4) AS change
FROM w
ORDER BY week
""", "LAG reads the previous row in the window's order; the first week has none, so its change is NULL. Week 12: "
     "down 0.2375 in one week.", minutes=5, ordered=True)

drill("8.2", "Write", "A four-week moving average",
      "Chennai MN-1L weekly sell-out with its four-week moving average (this week and the three before).",
      """
SELECT week, actual,
       ROUND(AVG(actual) OVER (ORDER BY week
                               ROWS BETWEEN 3 PRECEDING AND CURRENT ROW), 1) AS ma4
FROM demand
WHERE dc = 'CHN' AND sku = 'MN-1L'
ORDER BY week
""", "ROWS BETWEEN 3 PRECEDING AND CURRENT ROW is the frame. The first three weeks average fewer than four values; say "
     "so in a chart, or start at week 4.", minutes=5, ordered=True)

drill("8.3", "Write", "The monitor's z-score, in SQL",
      "Reproduce the monitor: Chennai MN-1L OTIF for weeks 9 to 12 with the mean and sample standard deviation of the "
      "eight weeks before each, and the z-score to one decimal.",
      """
WITH w AS (SELECT week, ROUND(AVG(on_time * in_full), 4) AS otif
           FROM orders WHERE dc = 'CHN' AND sku = 'MN-1L' GROUP BY week),
b AS (SELECT week, otif,
             AVG(otif) OVER p AS mean8,
             SQRT((SUM(otif * otif) OVER p
                   - COUNT(*) OVER p * AVG(otif) OVER p * AVG(otif) OVER p)
                  / (COUNT(*) OVER p - 1)) AS sd8
      FROM w
      WINDOW p AS (ORDER BY week ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING))
SELECT week, otif, ROUND(mean8, 4) AS mean8, ROUND(sd8, 4) AS sd8,
       ROUND((otif - mean8) / sd8, 1) AS z
FROM b
WHERE week >= 9
ORDER BY week
""", "The frame `8 PRECEDING AND 1 PRECEDING` is the baseline, excluding this week. SQLite has no STDDEV, so the sample "
     "standard deviation is built from sums: (sum of squares minus n times the mean squared) over n minus 1. Week 12: "
     "mean 0.9792, sd 0.0386, z -8.1, the monitor's number exactly.", minutes=12, ordered=True)

drill("8.4", "Write", "Dwell against its own history",
      "For week 12, each DC's dock dwell, its average over the eight weeks before, and the ratio of the two.",
      """
WITH b AS (SELECT week, dc, avg_dwell_h,
                  AVG(avg_dwell_h) OVER (PARTITION BY dc ORDER BY week
                                         ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING) AS base
           FROM dock)
SELECT dc, avg_dwell_h, ROUND(base, 2) AS base_8wk, ROUND(avg_dwell_h / base, 2) AS factor
FROM b
WHERE week = 12
ORDER BY factor DESC
""", "Gurgaon's 9.5 hours is 3.14 times its own baseline of about 3.0; the profile calls 2.0 congestion. The filter on "
     "week 12 sits outside the CTE: filter inside and the window would see no history.", minutes=7)

# ===================================================================== chapter 9: putting it together
drill("9.1", "Write", "The Monday table",
      "One row per DC for week 12: OTIF, fill rate, trucks more than a day late, and dock dwell. Build it from CTEs.",
      """
WITH o AS (SELECT dc, ROUND(AVG(on_time * in_full), 4) AS otif,
                  ROUND(1.0 * SUM(shipped) / SUM(requested), 4) AS fill_rate
           FROM orders WHERE week = 12 GROUP BY dc),
     t AS (SELECT lane_to AS dc,
                  SUM(CASE WHEN late_days > 1.0 THEN 1 ELSE 0 END) AS late_trucks
           FROM shipments WHERE week = 12 GROUP BY lane_to),
     k AS (SELECT dc, avg_dwell_h FROM dock WHERE week = 12)
SELECT o.dc, o.otif, o.fill_rate, t.late_trucks, k.avg_dwell_h
FROM o JOIN t ON t.dc = o.dc JOIN k ON k.dc = o.dc
ORDER BY o.otif
""", "Each CTE is aggregated to one row per DC before the join, so nothing fans out. This is the first screen of a "
     "control tower, in one statement.", minutes=10)

drill("9.2", "Write", "Short cases, in rupees",
      "Cases short (requested minus shipped) by DC and SKU for week 12, with the margin lost (cases short times "
      "contribution per case), largest first. Only pairs that were short.",
      """
SELECT o.dc, o.sku, SUM(o.requested - o.shipped) AS cases_short,
       ROUND(SUM(o.requested - o.shipped) * s.contribution_per_case) AS margin_lost_inr
FROM orders o
JOIN dim_sku s ON s.sku = o.sku
WHERE o.week = 12
GROUP BY o.dc, o.sku, s.contribution_per_case
HAVING SUM(o.requested - o.shipped) > 0
ORDER BY margin_lost_inr DESC
""", "1,100 nectar cases at Chennai and 132 pulp cases at Bhiwandi. This is margin on the cases already missed, not "
     "the triage's money at stake, which also sizes the weeks ahead.", minutes=7)

drill("9.3", "Write", "Chennai's week in one row",
      "For Chennai MN-1L in week 12, in one row: OTIF, cases short, trucks in, trucks more than a day late, cases on those "
      "trucks, and days of supply.",
      """
WITH o AS (SELECT ROUND(AVG(on_time * in_full), 4) AS otif,
                  SUM(requested - shipped) AS cases_short
           FROM orders WHERE week = 12 AND dc = 'CHN' AND sku = 'MN-1L'),
     t AS (SELECT COUNT(*) AS trucks,
                  SUM(CASE WHEN late_days > 1.0 THEN 1 ELSE 0 END) AS late_trucks,
                  SUM(CASE WHEN late_days > 1.0 THEN cases ELSE 0 END) AS late_cases
           FROM shipments WHERE week = 12 AND lane_to = 'CHN' AND sku = 'MN-1L'),
     i AS (SELECT ROUND(i.on_hand / d.daily_demand, 1) AS dos
           FROM inventory i JOIN dim_dc_sku d ON d.dc = i.dc AND d.sku = i.sku
           WHERE i.week = 12 AND i.dc = 'CHN' AND i.sku = 'MN-1L')
SELECT * FROM o, t, i
""", "Three one-row CTEs joined with commas (a cross join of one row by one row by one row) make one row. "
     "Every number in the root-cause agent's evidence for Chennai is in it.", minutes=10)

drill("9.4", "Fix", "Three bugs, one query",
      "This query should list, for week 12, each DC's fill rate and its late-truck count, including DCs with no late "
      "trucks. It has three bugs: one gives wrong fill rates, one multiplies rows, one drops DCs. Fix all three.",
      """
WITH f AS (SELECT dc, ROUND(1.0 * SUM(shipped) / SUM(requested), 4) AS fill_rate
           FROM orders WHERE week = 12 GROUP BY dc),
     t AS (SELECT lane_to AS dc, COUNT(*) AS late_trucks
           FROM shipments WHERE week = 12 AND late_days > 1.0 GROUP BY lane_to)
SELECT f.dc, f.fill_rate, COALESCE(t.late_trucks, 0) AS late_trucks
FROM f LEFT JOIN t ON t.dc = f.dc
ORDER BY f.dc
""", "(1) `SUM(shipped) / SUM(requested)` is integer division: every fill rate below 1 becomes 0. (2) Joining raw orders "
     "to raw shipments multiplies rows. (3) The late filter in WHERE removes every DC without a late truck, which "
     "turns the LEFT JOIN into an inner join. Pre-aggregate, divide as reals, and COALESCE the missing count to 0.",
      minutes=12, bad_sql="""
SELECT o.dc, SUM(o.shipped) / SUM(o.requested) AS fill_rate,
       COUNT(s.truck_id) AS late_trucks
FROM orders o
LEFT JOIN shipments s ON s.lane_to = o.dc AND s.week = o.week
WHERE o.week = 12 AND s.late_days > 1.0
GROUP BY o.dc
ORDER BY o.dc
""")

# ===================================================================== Part II: KPIs as SQL
drill("10.1", "Write", "OTIF three ways",
      "For Chennai MN-1L in week 12, compute OTIF by order, on-time share alone, and OTIF weighted by cases (cases on "
      "orders that were on time and in full, over all cases requested).",
      """
SELECT ROUND(AVG(on_time * in_full), 4) AS otif_by_order,
       ROUND(AVG(on_time), 4) AS on_time_only,
       ROUND(1.0 * SUM(CASE WHEN on_time = 1 AND in_full = 1 THEN requested ELSE 0 END)
             / SUM(requested), 4) AS otif_by_case
FROM orders
WHERE week = 12 AND dc = 'CHN' AND sku = 'MN-1L'
""", "Three honest-looking formulas, three numbers: 0.6667, 1.0 and 0.6473. On-time alone calls the worst week of the "
     "quarter perfect, because every one of the twelve orders left on time; four left short. A semantic layer exists "
     "so that only one of these is called OTIF.", minutes=6)

drill("10.2", "Write", "The two lates, side by side",
      "For week 12, by destination DC: trucks, trucks late by 1.0 day or more, and trucks late by more than 1.0 day.",
      """
SELECT lane_to AS dc, COUNT(*) AS trucks,
       SUM(CASE WHEN late_days >= 1.0 THEN 1 ELSE 0 END) AS late_ge_1,
       SUM(CASE WHEN late_days > 1.0 THEN 1 ELSE 0 END) AS late_gt_1
FROM shipments
WHERE week = 12
GROUP BY lane_to
ORDER BY lane_to
""", "Chennai 3 against 2, Gurgaon 1 against 0, Kolkata 1 against 0. The diagnosis tree uses the first reading, the "
     "semantic layer the second. Neither is wrong; having both is.", minutes=5)

drill("10.3", "Write", "Where the two days of supply disagree",
      "Days of supply two ways for every inventory row: from dim_dc_sku's daily demand (kept to 2 decimals) and from "
      "the weekly mean divided by 7. Return the rows where the two, rounded to one decimal, differ.",
      """
SELECT i.week, i.dc, i.sku, i.on_hand,
       ROUND(i.on_hand / d.daily_demand, 1) AS dos_2dp,
       ROUND(i.on_hand * 7.0 / d.weekly_mean, 1) AS dos_exact
FROM inventory i
JOIN dim_dc_sku d ON d.dc = i.dc AND d.sku = i.sku
WHERE ROUND(i.on_hand / d.daily_demand, 1) <> ROUND(i.on_hand * 7.0 / d.weekly_mean, 1)
ORDER BY i.week, i.dc, i.sku
""", "Three of 480 rows. Rounding the denominator to two decimals moves a result across a rounding boundary. A "
     "tenth of a day matters only next to a threshold, but a test that expects exact equality fails on exactly these "
     "rows.", minutes=7)

drill("10.4", "Write", "Receipts below plan",
      "Week-12 receipts that arrived below 98% of plan: DC, SKU, planned, received and receipts fill.",
      """
SELECT dc, sku, planned, received, ROUND(1.0 * received / planned, 4) AS receipts_fill
FROM receipts
WHERE week = 12 AND planned > 0 AND 1.0 * received / planned < 0.98
ORDER BY receipts_fill
""", "Two rows: Bhiwandi's pulp (0 of 602, the plant run that did not happen) and Chennai's nectar (881 of 2,644, "
     "the two late trucks). The 98% line ignores the one-case truck rounding.", minutes=5)

drill("13.1", "Write", "MAPE against WMAPE",
      "For GC-200 at Bhiwandi over weeks 9 to 12, compute MAPE (the mean of each week's absolute percentage error) and "
      "WMAPE (total absolute error over total sell-out).",
      """
SELECT ROUND(AVG(1.0 * ABS(actual - forecast) / actual), 4) AS mape,
       ROUND(1.0 * SUM(ABS(actual - forecast)) / SUM(actual), 4) AS wmape
FROM demand
WHERE dc = 'BHW' AND sku = 'GC-200' AND week BETWEEN 9 AND 12
""", "MAPE 0.3945, WMAPE 0.4356. MAPE weights every week equally; WMAPE weights by volume, and the two big misses came "
     "in the two big promo weeks. Planners usually mean WMAPE; ask.", minutes=6)

drill("13.2", "Write", "Bias by DC",
      "Forecast bias by DC over all twelve weeks: total sell-out minus total forecast, over total sell-out. Positive "
      "means the forecast was too low.",
      """
SELECT dc, SUM(actual) AS actual, SUM(forecast) AS forecast,
       ROUND(1.0 * SUM(actual - forecast) / SUM(actual), 4) AS bias
FROM demand
GROUP BY dc
ORDER BY bias DESC
""", "A forecast can have a small error and a large bias, or the reverse. Bias is the one that fills or empties a "
     "warehouse, week after week, in the same direction.", minutes=5)

# ===================================================================== chapter 26 (Part VI): the screens
drill("26.1", "Screen", "Top three per group",
      "The top three SKUs by cases shipped at each DC in week 12.",
      """
WITH r AS (SELECT dc, sku, SUM(shipped) AS shipped,
                  ROW_NUMBER() OVER (PARTITION BY dc ORDER BY SUM(shipped) DESC, sku) AS rn
           FROM orders WHERE week = 12 GROUP BY dc, sku)
SELECT dc, sku, shipped, rn
FROM r
WHERE rn <= 3
ORDER BY dc, rn
""", "The most common screen of all. Say aloud why ROW_NUMBER and not RANK (exactly three rows per DC), and what the "
     "tie-breaker is for.", minutes=6, ordered=True)

drill("26.2", "Screen", "The second-highest week",
      "The second-highest weekly sell-out of MN-1L at Chennai, and its week. If several weeks share the top value, the "
      "second-highest is the next distinct value.",
      """
WITH r AS (SELECT week, actual, DENSE_RANK() OVER (ORDER BY actual DESC) AS dr
           FROM demand WHERE dc = 'CHN' AND sku = 'MN-1L')
SELECT week, actual
FROM r
WHERE dr = 2
""", "DENSE_RANK answers 'the next distinct value'. `LIMIT 1 OFFSET 1` answers 'the second row', which is wrong "
     "when the top value repeats. Say which question you are answering.", minutes=5)

drill("26.3", "Screen", "Share of the total",
      "Each DC's share of all cases shipped in week 12, to four decimals.",
      """
SELECT dc, SUM(shipped) AS shipped,
       ROUND(1.0 * SUM(shipped) / SUM(SUM(shipped)) OVER (), 4) AS share
FROM orders
WHERE week = 12
GROUP BY dc
ORDER BY share DESC
""", "`OVER ()` is a window over the whole result: the grand total on every row, no second query, no join.",
      minutes=4)

drill("26.4", "Screen", "Gaps and islands",
      "For each DC, the longest run of consecutive weeks with OTIF of exactly 1.0, and the weeks it ran.",
      """
WITH w AS (SELECT dc, week, AVG(on_time * in_full) AS otif FROM orders GROUP BY dc, week),
     p AS (SELECT dc, week, week - ROW_NUMBER() OVER (PARTITION BY dc ORDER BY week) AS grp
           FROM w WHERE otif = 1.0),
     runs AS (SELECT dc, MIN(week) AS first_week, MAX(week) AS last_week, COUNT(*) AS weeks
              FROM p GROUP BY dc, grp),
     best AS (SELECT dc, first_week, last_week, weeks,
                     ROW_NUMBER() OVER (PARTITION BY dc
                                        ORDER BY weeks DESC, first_week) AS rn
              FROM runs)
SELECT dc, first_week, last_week, weeks
FROM best
WHERE rn = 1
ORDER BY weeks DESC, dc
""", "The island trick: within a run of consecutive weeks, week minus row number is constant, so it labels the run. "
     "Group by it, measure each run, keep the longest.", minutes=12)

drill("26.5", "Screen", "Deduplicate a double load",
      "A night job loaded week 12's inventory twice. Simulate it with a UNION ALL, then return one row per week, DC and "
      "SKU, and prove the count: 480.",
      """
WITH loaded AS (SELECT * FROM inventory
                UNION ALL
                SELECT * FROM inventory WHERE week = 12),
     r AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY week, dc, sku
                                        ORDER BY snapshot_age_h) AS rn
           FROM loaded)
SELECT COUNT(*) AS rows_loaded, SUM(CASE WHEN rn = 1 THEN 1 ELSE 0 END) AS rows_kept
FROM r
""", "520 rows loaded, 480 kept. ROW_NUMBER over the natural key keeps one copy; ordering by snapshot age keeps the "
     "freshest when copies differ.", minutes=7)

drill("26.6", "Screen", "Never late",
      "Which DC and SKU pairs had no inbound truck more than a day late in any week? Count them, out of 40.",
      """
SELECT COUNT(*) AS never_late
FROM dim_dc_sku d
WHERE NOT EXISTS (SELECT 1 FROM shipments s
                  WHERE s.lane_to = d.dc AND s.sku = d.sku AND s.late_days > 1.0)
""", "The 'customers with no orders' screen in supply chain clothes: an anti-join with NOT EXISTS. Beware NOT IN when "
     "the subquery can return a NULL: then NOT IN returns no rows at all.", minutes=5)

drill("26.7", "Screen", "Pivot by class",
      "Week-12 cases shipped by DC, with one column per ABC class.",
      """
SELECT o.dc,
       SUM(CASE WHEN s.class = 'A' THEN o.shipped ELSE 0 END) AS class_a,
       SUM(CASE WHEN s.class = 'B' THEN o.shipped ELSE 0 END) AS class_b,
       SUM(CASE WHEN s.class = 'C' THEN o.shipped ELSE 0 END) AS class_c
FROM orders o
JOIN dim_sku s ON s.sku = o.sku
WHERE o.week = 12
GROUP BY o.dc
ORDER BY o.dc
""", "Conditional aggregation is the portable pivot; PIVOT syntax exists in Snowflake, Databricks, BigQuery and SQL "
     "Server, and differs in each.", minutes=5)

drill("26.8", "Screen", "Growth, week on week",
      "GC-200 sell-out at Bhiwandi by week with the percentage change from the week before, to one decimal.",
      """
SELECT week, actual,
       ROUND(100.0 * (actual - LAG(actual) OVER (ORDER BY week))
             / LAG(actual) OVER (ORDER BY week), 1) AS pct_change
FROM demand
WHERE dc = 'BHW' AND sku = 'GC-200'
ORDER BY week
""", "The Diwali promotion shows as +187.6% in week 11 and 0.0% in week 12. A percentage change against a small "
     "base looks dramatic; quote the cases too.", minutes=5, ordered=True)

drill("26.9", "Screen", "When the running total crossed a line",
      "The first week in which cumulative MN-1L sell-out at Chennai passed 20,000 cases, with the running total that week.",
      """
WITH c AS (SELECT week, SUM(actual) OVER (ORDER BY week) AS running
           FROM demand WHERE dc = 'CHN' AND sku = 'MN-1L')
SELECT week, running
FROM c
WHERE running > 20000
ORDER BY week
LIMIT 1
""", "Running total first, filter second: a WHERE cannot see a window's result in the same SELECT, so the window goes "
     "in a CTE. QUALIFY does this in one step on the engines that have it.", minutes=5)

drill("26.10", "Screen", "Long here, short there",
      "Self-join: in week 11, pairs of DCs where one held more than 21 days of supply of a SKU and the other less than "
      "7 days of the same SKU. SKU, long DC, its days, short DC, its days.",
      """
WITH d AS (SELECT i.dc, i.sku, ROUND(i.on_hand / x.daily_demand, 1) AS dos
           FROM inventory i JOIN dim_dc_sku x ON x.dc = i.dc AND x.sku = i.sku
           WHERE i.week = 11)
SELECT a.sku, a.dc AS long_dc, a.dos AS long_dos, b.dc AS short_dc, b.dos AS short_dos
FROM d a
JOIN d b ON b.sku = a.sku AND b.dc <> a.dc
WHERE a.dos > 21 AND b.dos < 7
ORDER BY a.dos - b.dos DESC
""", "Kolkata's 60.2 days of mango nectar against Chennai's 3.8: the F.11 transfer, found by a self-join on the last good "
     "week. Run it on week 12 and the pair disappears: the stale 400-case snapshot hides the stock that saves Chennai.",
      minutes=8)

drill("26.11", "Screen", "A median without MEDIAN",
      "The median cases requested per order at Chennai in week 12, without a MEDIAN function.",
      """
WITH r AS (SELECT requested,
                  ROW_NUMBER() OVER (ORDER BY requested) AS rn,
                  COUNT(*) OVER () AS n
           FROM orders WHERE week = 12 AND dc = 'CHN')
SELECT AVG(requested) AS median_requested
FROM r
WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
""", "With n = 80, integer division picks rows 40 and 41 and averages them; with an odd n both expressions pick the "
     "middle row. Snowflake, Databricks and BigQuery have MEDIAN or PERCENTILE_CONT; SQLite does not.", minutes=8)

drill("26.12", "Explain", "Read it before you run it",
      "Say in one sentence what this query returns, predict its first row, then run it.",
      """
SELECT dc, week, avg_dwell_h,
       MAX(avg_dwell_h) OVER (PARTITION BY dc) AS worst_dwell
FROM dock
WHERE week >= 11
ORDER BY avg_dwell_h DESC
LIMIT 3
""", "Each DC's dwell in weeks 11 and 12 next to that DC's worst dwell in those two weeks, worst rows first. The "
     "window sees only the rows the WHERE kept, which is why worst_dwell is the two-week worst, not the all-time worst.",
      minutes=4, ordered=True, predict="first row: ______")

DRILLS = {d["id"]: d for d in D}


def chapter(ch: int | str) -> list[dict]:
    return [d for d in D if d["id"].split(".")[0] == str(ch)]
