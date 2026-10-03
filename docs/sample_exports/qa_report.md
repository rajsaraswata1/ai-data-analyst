# AI Data Analyst Report
_Generated 2026-10-03 02:55 UTC_

## 1. Which customer segment generated the highest revenue?

**Datasets used:** customers.csv, sales.csv

### Key findings

Corporate has the highest total revenue: ₹3,265,060.16, 57.8% of the overall total revenue.

- Compared across 4 segments.
- Corporate is 108.9% ahead of Consumer (₹1,563,184.94).
- The best segment is 12.9x the weakest (Enterprise).

**Chart: Total revenue by segment** (hbar)

| Segment | total revenue |
|---|---|
| Corporate | 3265060.16 |
| Consumer | 1563184.94 |
| Small Business | 553406.67 |
| Enterprise | 252825.24 |

### SQL

```sql
SELECT "customers"."segment" AS "segment",
       ROUND(SUM("sales"."revenue"), 2) AS total_revenue
FROM "sales"
LEFT JOIN "customers" ON "sales"."customer_id" = "customers"."customer_id"
WHERE "customers"."segment" IS NOT NULL
GROUP BY 1
ORDER BY total_revenue DESC
```

### Equivalent pandas

```python
import pandas as pd

customers = pd.read_csv('customers.csv', parse_dates=['signup_date'])
customers.columns = customers.columns.str.strip().str.lower().str.replace(r'[^0-9a-z]+', '_', regex=True).str.strip('_')
sales = pd.read_csv('sales.csv', parse_dates=['order_date'])
sales.columns = sales.columns.str.strip().str.lower().str.replace(r'[^0-9a-z]+', '_', regex=True).str.strip('_')

df = sales
df = df.merge(customers, left_on='customer_id', right_on='customer_id', how='left', suffixes=('', '_customers'))
df = df.dropna(subset=['segment'])
result = (df.groupby(['segment'])['revenue'].sum().round(2).rename('total_revenue').reset_index())
result = result.sort_values('total_revenue', ascending=False)
print(result)
```

### Analysis method

1. Grouped sales joined with customers by segment.
2. Calculated the sum of revenue per group.
3. Sorted descending.
4. Reconciled the grouped totals against an independent overall total query.

### Validation

- Groups sum to 5,634,477.01; 12,469.77 (0.22% of the overall total 5,646,946.78) has no segment (missing or unmatched key values) and is excluded from the groups.
- 4 group(s) returned; no NaN/inf values in results.

### Assumptions

- No special assumptions: all columns and filters were named in the question; standard definitions were applied (see Analysis method).
