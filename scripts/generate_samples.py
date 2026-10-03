"""Generate deterministic synthetic sample datasets (no real/copyrighted data).

Designed to exercise: trends + seasonality, rankings, joins, anomalies, forecasting, data-quality issues.
Run:  python scripts/generate_samples.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "data" / "samples"
RNG = np.random.default_rng(42)


def make_products() -> pd.DataFrame:
    cats = {"Electronics": (["Laptop", "Monitor", "Tablet", "Headphones", "Webcam", "Router", "Speaker", "Printer"], (180, 900)),
            "Furniture": (["Desk", "Chair", "Bookshelf", "Cabinet", "Lamp", "Table"], (60, 450)),
            "Office Supplies": (["Notebook", "Pen Set", "Stapler", "Folder", "Planner", "Whiteboard", "Markers", "Paper Ream"], (3, 40)),
            "Accessories": (["Mouse", "Keyboard", "USB Hub", "Cable Pack", "Laptop Stand", "Mouse Pad", "Charger", "Phone Case"], (8, 120))}
    rows, n = [], 1
    for cat, (names, (lo, hi)) in cats.items():
        for nm in names:
            price = round(float(RNG.uniform(lo, hi)), 2)
            rows.append({"product_id": f"P{n:03d}", "product_name": nm, "category": cat,
                         "unit_price": price, "unit_cost": round(price * float(RNG.uniform(0.55, 0.8)), 2)})
            n += 1
    return pd.DataFrame(rows)


def make_customers(n: int = 300) -> pd.DataFrame:
    first = ["Aarav", "Priya", "Rohan", "Sneha", "Vikram", "Anita", "Karan", "Meera", "Arjun", "Divya", "Rahul", "Neha",
             "Amit", "Pooja", "Suresh", "Kavya", "Manish", "Ritu", "Nikhil", "Isha"]
    last = ["Sharma", "Verma", "Patel", "Gupta", "Singh", "Reddy", "Nair", "Joshi", "Mehta", "Iyer", "Kapoor", "Das"]
    seg = RNG.choice(["Consumer", "Corporate", "Small Business", "Enterprise"], n, p=[0.45, 0.25, 0.22, 0.08])
    cities = RNG.choice(["Delhi", "Mumbai", "Bengaluru", "Chennai", "Kolkata", "Pune", "Lucknow", "Jaipur"], n)
    signup = pd.to_datetime("2022-01-01") + pd.to_timedelta(RNG.integers(0, 1000, n), unit="D")
    names = [f"{first[i % len(first)]} {last[(i * 7) % len(last)]} {i // 20 + 1}" for i in range(n)]
    return pd.DataFrame({"customer_id": [f"C{i + 1:04d}" for i in range(n)], "customer_name": names,
                         "segment": seg, "city": cities, "signup_date": signup.strftime("%Y-%m-%d")})


def make_sales(products: pd.DataFrame, customers: pd.DataFrame, n: int = 6000) -> pd.DataFrame:
    months = pd.period_range("2023-01", "2025-12", freq="M")
    # growth trend + Q4 seasonality
    w = np.array([(1 + 0.025 * i) * (1.35 if p.month in (11, 12) else 1.0) * (0.9 if p.month in (1, 2) else 1.0)
                  for i, p in enumerate(months)])
    month_idx = RNG.choice(len(months), n, p=w / w.sum())
    day = RNG.integers(0, 28, n)
    dates = [months[m].to_timestamp() + pd.Timedelta(days=int(d)) for m, d in zip(month_idx, day)]
    region = RNG.choice(["North", "South", "East", "West"], n, p=[0.34, 0.26, 0.22, 0.18])
    # a March-2025 dip in the North (supports 'why did revenue fall' questions)
    keep = ~((region == "North") & np.array([(d.year == 2025 and d.month == 3) for d in dates]) & (RNG.random(n) < 0.6))
    # product popularity: a few clear laggards
    pw = RNG.uniform(0.5, 2.0, len(products))
    pw[[5, 11, 17, 23, 29]] *= 0.12
    pidx = RNG.choice(len(products), n, p=pw / pw.sum())
    cw = RNG.pareto(1.6, len(customers)) + 0.3
    cidx = RNG.choice(len(customers), n, p=cw / cw.sum())
    qty = RNG.integers(1, 8, n)
    price = products.unit_price.values[pidx]
    disc = RNG.choice([0, 0, 0, 0.05, 0.1, 0.15], n)
    df = pd.DataFrame({"order_id": [f"O{100000 + i}" for i in range(n)], "order_date": pd.to_datetime(dates),
                       "customer_id": customers.customer_id.values[cidx], "product_id": products.product_id.values[pidx],
                       "region": region, "quantity": qty, "unit_price": price, "discount": disc})
    df = df[keep].reset_index(drop=True)
    # injected anomalies: bulk orders with extreme quantity
    bad = RNG.choice(len(df), 8, replace=False)
    df.loc[bad, "quantity"] = RNG.integers(150, 400, 8)
    df["revenue"] = (df.quantity * df.unit_price * (1 - df.discount)).round(2)
    df = df.sort_values("order_date").reset_index(drop=True)
    df["order_id"] = [f"O{100000 + i}" for i in range(len(df))]
    # realistic data-quality issues
    df.loc[RNG.choice(len(df), int(len(df) * 0.03), replace=False), "discount"] = np.nan
    df.loc[RNG.choice(len(df), 12, replace=False), "customer_id"] = np.nan
    df = pd.concat([df, df.sample(6, random_state=1)], ignore_index=True)  # 6 exact duplicate rows
    df["order_date"] = df.order_date.dt.strftime("%Y-%m-%d")
    return df


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    products, customers = make_products(), make_customers()
    sales = make_sales(products, customers)
    sales.to_csv(OUT / "sales.csv", index=False)
    customers.to_csv(OUT / "customers.csv", index=False)
    products.drop(columns=["unit_price"]).to_csv(OUT / "products.csv", index=False)
    for f in sorted(OUT.glob("*.csv")):
        print(f.name, len(pd.read_csv(f)), "rows")


if __name__ == "__main__":
    main()
