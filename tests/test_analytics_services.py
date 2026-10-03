import unittest

import numpy as np
import pandas as pd

from app.core.cache import TTLCache, make_key
from app.core.errors import AppError
from app.services.anomaly import detect_anomalies
from app.services.forecasting import forecast_monthly
from app.tools.viz_tool import select_chart_type
from tests.support import Engine, sample_df
from app.tools.base import ToolContext


class AnomalyTests(unittest.TestCase):
    def test_finds_injected_outliers(self):
        rng = np.random.default_rng(0)
        df = pd.DataFrame({"v": rng.normal(100, 5, 500), "w": rng.normal(10, 1, 500)})
        df.loc[[10, 20, 30], "v"] = [900, 1000, -500]
        r = detect_anomalies(df, "v", ["v", "w"])
        flagged = {a["row"] for a in r["anomalies"]}
        self.assertTrue({10, 20, 30} <= flagged)
        self.assertLess(len(flagged), 15)
        a = next(x for x in r["anomalies"] if x["row"] == 20)
        self.assertEqual(a["actual"], 1000.0)
        self.assertLess(a["expected_low"], 100)
        self.assertGreater(a["expected_high"], 100)
        self.assertIn("1,000.00", a["reason"])             # reason cites the real value
        self.assertEqual(r["method"], "zscore")            # symmetric data -> z-score chosen automatically

    def test_skewed_data_selects_iqr(self):
        df = pd.DataFrame({"v": np.random.default_rng(1).lognormal(3, 1.2, 600)})
        self.assertEqual(detect_anomalies(df, "v", ["v"])["method"], "iqr")

    def test_clean_uniform_data_has_no_anomalies(self):
        df = pd.DataFrame({"v": np.linspace(1, 100, 300)})
        self.assertEqual(detect_anomalies(df, "v", ["v"])["total"], 0)

    def test_sample_sales_detects_bulk_orders(self):
        df = sample_df("sales")
        df["order_date"] = pd.to_datetime(df["order_date"])
        r = detect_anomalies(df, "quantity", ["quantity", "revenue"], "order_id")
        big = set(df.loc[df.quantity >= 150, "order_id"])
        self.assertEqual(len(big), 8)
        self.assertTrue(big <= {a["id"] for a in r["anomalies"]})


class ForecastTests(unittest.TestCase):
    def series(self, n, slope=10.0, base=100.0, seasonal=0.0):
        idx = [str(p) for p in pd.period_range("2022-01", periods=n, freq="M")]
        vals = [base + slope * i + seasonal * np.sin(2 * np.pi * i / 12) for i in range(n)]
        return pd.Series(vals, index=idx)

    def test_linear_series_forecast_is_close(self):
        r = forecast_monthly(self.series(30), 6)
        exp = [100 + 10 * i for i in range(30, 36)]
        for got, e in zip(r["forecast"]["point"], exp):
            self.assertAlmostEqual(got, e, delta=15)
        for lo, p, hi in zip(r["forecast"]["lower"], r["forecast"]["point"], r["forecast"]["upper"]):
            self.assertLessEqual(lo, p)
            self.assertLessEqual(p, hi)
        self.assertEqual(r["forecast"]["periods"][0], "2024-07")
        self.assertIn("estimates", r["disclaimer"])

    def test_seasonal_model_chosen_for_seasonal_data(self):
        r = forecast_monthly(self.series(48, seasonal=40), 6)
        self.assertTrue(r["model"].startswith("seasonal"), r["model"])

    def test_insufficient_data(self):
        with self.assertRaises(AppError) as cm:
            forecast_monthly(self.series(4), 3)
        self.assertEqual(cm.exception.code, "insufficient_data")

    def test_partial_last_month_excluded(self):
        r = forecast_monthly(self.series(14), 3, pd.Timestamp("2023-02-05"))
        self.assertEqual(r["history"]["periods"][-1], "2023-01")
        self.assertTrue(r["notes"])


class VizAndCacheTests(unittest.TestCase):
    def test_chart_selection_rules(self):
        self.assertEqual(select_chart_type("trend", 12), "line")
        self.assertEqual(select_chart_type("rank", 5), "hbar")
        self.assertEqual(select_chart_type("aggregate", 4, share_question=True), "pie")
        self.assertEqual(select_chart_type("aggregate", 20, share_question=True), "bar")
        self.assertEqual(select_chart_type("distribution", 0), "histogram")
        self.assertEqual(select_chart_type("scatter", 0), "scatter")
        self.assertEqual(select_chart_type("aggregate", 4, override="pie"), "pie")

    def test_chart_data_comes_from_frame(self):
        e = Engine()
        ctx = ToolContext(e.ws, e.cache, e.cfg, "u1", "t")
        frame = pd.DataFrame({"k": ["a", "b"], "v": [1.5, 2.5]})
        ch = e.router.call("create_chart", {"chart_type": "bar", "frame": frame, "x": "k", "y": "v"}, ctx).data["chart"]
        self.assertEqual(ch["labels"], ["a", "b"])
        self.assertEqual(ch["series"][0]["values"], [1.5, 2.5])
        h = e.router.call("create_chart", {"chart_type": "histogram", "frame": pd.DataFrame({"x": range(100)}), "x": "x"}, ctx).data["chart"]
        self.assertEqual(sum(b["count"] for b in h["bins"]), 100)

    def test_cache_hit_miss_ttl_and_disabled(self):
        c = TTLCache(ttl=1)
        calls = []
        k = make_key("q", {"a": 1})
        self.assertEqual(k, make_key("q", {"a": 1}))
        self.assertNotEqual(k, make_key("q", {"a": 2}))
        for _ in range(3):
            c.get_or_compute(k, lambda: calls.append(1) or 7)
        self.assertEqual(len(calls), 1)
        self.assertEqual(c.stats()["hits"], 2)
        import time
        time.sleep(1.1)
        c.get_or_compute(k, lambda: calls.append(1) or 7)
        self.assertEqual(len(calls), 2)
        off = TTLCache(enabled=False)
        off.get_or_compute(k, lambda: calls.append(1))
        off.get_or_compute(k, lambda: calls.append(1))
        self.assertEqual(len(calls), 4)

    def test_sql_results_cached_across_calls(self):
        e = Engine()
        ctx = ToolContext(e.ws, e.cache, e.cfg, "u1", "t")
        a = e.router.call("run_sql", {"sql": "SELECT COUNT(*) AS n FROM sales"}, ctx)
        b = e.router.call("run_sql", {"sql": "SELECT  COUNT(*)  AS n FROM sales"}, ctx)   # whitespace-normalised key
        self.assertFalse(a.cached)
        self.assertTrue(b.cached)

    def test_tool_argument_validation(self):
        e = Engine()
        ctx = ToolContext(e.ws, e.cache, e.cfg, "u1", "t")
        for name, args in [("run_sql", {}), ("run_sql", {"sql": 5}), ("run_sql", {"sql": "SELECT 1", "x": 1}), ("create_chart", {"chart_type": "3d", "frame": pd.DataFrame()}),
                           ("nope", {})]:
            with self.assertRaises(AppError):
                e.router.call(name, args, ctx)


if __name__ == "__main__":
    unittest.main()
