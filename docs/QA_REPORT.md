# Manual QA report

Run against a live server at `http://127.0.0.1:5077` over HTTP (`scripts/manual_qa.py`). Expected values are computed independently with pandas from the CSVs.

| # | Test | Result |
|---|---|---|
| 1 | Upload sales.csv and ask highest-revenue region | **PASS** |
| 2 | Monthly sales trends -> line chart | **PASS** |
| 3 | Underperforming products ranking | **PASS** |
| 4 | Top five customers | **PASS** |
| 5 | Generate SQL for this analysis (read-only) | **PASS** |
| 6 | Detect anomalies (real statistics) | **PASS** |
| 7 | Forecast next 6 months | **PASS** |
| 8 | Two datasets + cross-dataset question (join) | **PASS** |
| 9 | Export report (MD / HTML / CSV / session) | **PASS** |
| 10 | Conversation context (+ server restart persistence) | **PASS** |

## TEST 1 – Upload sales.csv and ask highest-revenue region: PASS
- **Input:** upload sales.csv; “Which region generated the highest revenue?”
- **Expected:** North with 1,930,088.92 (pandas)
- **Actual:** North has the highest total revenue: ₹1,930,088.92, 34.2% of the overall total revenue.
- **Evidence:** table rows: [['North', 1930088.92, 34.18], ['South', 1484453.69, 26.29], ['East', 1297312.35, 22.97], ['West', 935091.82, 16.56]]; validation: Sum of groups (5,646,946.78) matches the overall total (5,646,946.78).

## TEST 2 – Monthly sales trends -> line chart: PASS
- **Input:** Show monthly sales trends.
- **Expected:** line chart, 36 monthly points summing to 5,646,946.78
- **Actual:** Total revenue over 36 months (2023-01 to 2025-12): total ₹5,646,946.78, peaking in 2025-12 at ₹268,944.78.
- **Evidence:** chart type=line, points=36, sum=5,646,946.78; insights: Linear trend: upward (slope ₹3,013.59 per month, R²=0.53, p=4.18e-07).

## TEST 3 – Underperforming products ranking: PASS
- **Input:** Which products are underperforming?
- **Expected:** 8 products at/below the 25th percentile, weakest first (P018)
- **Actual:** 8 of 30 product ids are underperforming: their total revenue is at or below the 25th percentile (₹23,727.25). The weakest is P018 with ₹1,197.75.
- **Evidence:** flagged=['P015', 'P017', 'P018', 'P019', 'P020', 'P021']...; assumptions: ["No descriptive name column was found, so results are grouped by the identifier 'product_id'. Upload the matching lookup file (e.g. products.csv) to see names.", 'Used metric from the previous question.']

## TEST 4 – Top five customers: PASS
- **Input:** What are the top five customers?
- **Expected:** ['C0070', 'C0288', 'C0024', 'C0282', 'C0154'] (grouped by customer_id: customers.csv not uploaded yet)
- **Actual:** The top 5 customer ids by total revenue: 1. C0070 (₹2,287,711.46); 2. C0288 (₹223,045.50); 3. C0024 (₹156,880.12); 4. C0282 (₹115,557.30); 5. C0154 (₹107,218.40).
- **Evidence:** rows: [['C0070', 2287711.46, 40.51], ['C0288', 223045.5, 3.95], ['C0024', 156880.12, 2.78], ['C0282', 115557.3, 2.05], ['C0154', 107218.4, 1.9]]

## TEST 5 – Generate SQL for this analysis (read-only): PASS
- **Input:** Generate SQL for this analysis.
- **Expected:** the previous analysis' SELECT, re-executed read-only, 5 rows
- **Actual:** Here is the SQL behind the previous analysis (“What are the top five customers?”). It is read-only and was just re-validated and executed (1 statement(s)); the last returned 5 row(s).
- **Evidence:** SELECT "sales"."customer_id" AS "customer_id", ⏎        ROUND(SUM("sales"."revenue"), 2) AS total_revenue ⏎ FROM "sales" ⏎ WHERE "sales"."customer_id" IS NOT NULL ⏎ GROUP BY 1 ⏎ ORDER BY total_revenue DESC ⏎ LIMIT 5

## TEST 6 – Detect anomalies (real statistics): PASS
- **Input:** Detect anomalies in the dataset.
- **Expected:** IQR(k=3) upper bound 5,401.98; ≥23 rows above it; most extreme = 19,786.56
- **Actual:** 37 anomalous rows were flagged in revenue out of 5,972 scanned. The most extreme is O100238 with ₹19,786.56 (expected ₹0 – ₹5,401.98).
- **Evidence:** top: id=O100238 actual=19786.56 expected≤5401.98; reason: revenue of 19,786.56 is unusually high: outside the expected range [0.00, 5,401.98] (IQR rule (k=3.0; Q1=96.14, Q3=1,422.60) chosen because |skew|=1.69 > 1.0). …

## TEST 7 – Forecast next 6 months: PASS
- **Input:** Forecast sales for the next 6 months.
- **Expected:** 6 monthly estimates from 2026-01 with lower ≤ point ≤ upper, labelled as estimates
- **Actual:** Estimated total revenue for the next 6 months (2026-01 to 2026-06): about ₹1,206,238.60 in total (rough 95% range ₹818,168.22 to ₹1,594,308.97). This is a statistical estimate, not a guarantee.
- **Evidence:** model=seasonal_multiplicative; point=[201235.79, 183693.63, 225620.8, 206606.08, 180976.13, 208106.17]; backtest={'mae': 26461.8, 'mape_pct': 14.18}

## TEST 8 – Two datasets + cross-dataset question (join): PASS
- **Input:** upload customers.csv (now 2 datasets); “Which customer segment generated the highest revenue?”
- **Expected:** relationship sales.customer_id→customers.customer_id; Corporate = 3,265,060.16
- **Actual:** Corporate has the highest total revenue: ₹3,265,060.16, 57.8% of the overall total revenue.
- **Evidence:** detected: sales.customer_id → customers.customer_id (100.0% of sales values match); SQL: FROM "sales" / LEFT JOIN "customers" ON "sales"."customer_id" = "customers"."customer_id"; Groups sum to 5,634,477.01; 12,469.77 (0.22% of the overall total 5,646,946.78) has no segment (missing or unmatched key values) and is excluded from the groups.

## TEST 9 – Export report (MD / HTML / CSV / session): PASS
- **Input:** export the segment analysis
- **Expected:** 200 responses; MD has findings, SQL, method, assumptions, sources; HTML embeds chart PNG
- **Actual:** statuses/sizes: {'md': (200, 2265), 'html': (200, 32411), 'csv': (200, 145)}; session report 22939 bytes
- **Evidence:** files written to docs/sample_exports/; markdown sections present: ['Key findings', '### SQL', 'Analysis method', 'Assumptions', 'Datasets used', 'sales.csv', 'customers.csv']

## TEST 10 – Conversation context (+ server restart persistence): PASS
- **Input:** “Which region has the highest revenue?” → “Show me its monthly trend” → [restart server] → “what about South”
- **Expected:** ‘its’ resolves to North: trend total = 1,930,088.92; after restart datasets (2) and context persist and ‘South’ replaces the filter
- **Actual:** Total revenue over 36 months (2023-01 to 2025-12): total ₹1,930,088.92, peaking in 2025-09 at ₹104,121.46. (filtered: region = North) | after restart: Total revenue over 36 months (2023-01 to 2025-12): total ₹1,484,453.69, peaking in 2024-11 at ₹85,397.70. (filtered: region = South)
- **Evidence:** assumptions: ['Used metric from the previous question.', 'Used region=North from the previous question.']; restarted=True; follow-up after restart used context: True
