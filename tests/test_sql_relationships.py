import unittest

import pandas as pd

from app.core.errors import AppError, UnsafeSQLError
from app.services.relationships import detect_relationships, join_path
from app.services.semantic import SemanticIndex
from app.services.sql_engine import SQLiteEngine, validate_sql
from tests.support import Engine, sample_df


class SQLSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = Engine()

    def test_blocked_statements(self):
        attacks = ["DROP TABLE sales", "delete from sales", "UPDATE sales SET revenue=0", "INSERT INTO sales VALUES (1)",
                   "ALTER TABLE sales ADD x INT", "CREATE TABLE x(a)", "ATTACH DATABASE 'x.db' AS x", "COPY sales TO 'x'",
                   "INSTALL httpfs", "LOAD 'x'", "PRAGMA table_info(sales)", "SELECT 1; DROP TABLE sales",
                   "WITH a AS (SELECT 1) DELETE FROM sales", "select * from sales /* ; */ ; update sales set a=1", "", "   "]
        for sql in attacks:
            with self.assertRaises(UnsafeSQLError, msg=sql):
                self.e.ws.engine.execute(sql)

    def test_data_unchanged_after_attacks(self):
        for sql in ["DROP TABLE sales", "DELETE FROM sales"]:
            with self.assertRaises(AppError):
                self.e.ws.engine.execute(sql)
        self.assertEqual(int(self.e.ws.engine.execute("SELECT COUNT(*) AS n FROM sales").iloc[0, 0]), len(self.e.ws.tables["sales"]))

    def test_semicolon_inside_string_is_fine(self):
        self.assertEqual(self.e.ws.engine.execute("SELECT 'a;b' AS x").iloc[0, 0], "a;b")

    def test_cannot_read_system_tables(self):
        with self.assertRaises(AppError):
            self.e.ws.engine.execute("SELECT * FROM sqlite_master")

    def test_select_matches_pandas(self):
        got = self.e.ws.engine.execute("SELECT region, ROUND(SUM(revenue),2) AS r FROM sales GROUP BY region").set_index("region")["r"]
        exp = sample_df("sales").groupby("region").revenue.sum().round(2)
        pd.testing.assert_series_equal(got.sort_index(), exp.sort_index(), check_names=False)

    def test_timeout(self):
        eng = SQLiteEngine(timeout_s=0.3)
        eng.register({"t": pd.DataFrame({"a": range(3000)})})
        with self.assertRaises(AppError) as cm:
            eng.execute("SELECT COUNT(*) FROM t a, t b, t c")
        self.assertEqual(cm.exception.code, "timeout")

    def test_row_cap(self):
        df = self.e.ws.engine.execute("SELECT * FROM sales", max_rows=100)
        self.assertEqual(len(df), 100)
        self.assertTrue(df.attrs["truncated"])

    def test_validate_returns_clean_sql(self):
        self.assertEqual(validate_sql("SELECT 1;"), "SELECT 1")


class RelationshipTests(unittest.TestCase):
    def test_detects_joins(self):
        e = Engine()
        desc = {(r["child_table"], r["child_column"], r["parent_table"], r["parent_column"]) for r in e.ws.relationships}
        self.assertIn(("sales", "customer_id", "customers", "customer_id"), desc)
        self.assertIn(("sales", "product_id", "products", "product_id"), desc)
        path = join_path(e.ws.relationships, "customers", "products")
        self.assertEqual(len(path), 2)
        self.assertEqual(join_path(e.ws.relationships, "sales", "sales"), [])

    def test_no_false_relationship(self):
        t = {"a": pd.DataFrame({"x_id": ["1", "2", "3"], "v": [1, 2, 3]}), "b": pd.DataFrame({"y_id": ["9", "8", "7"], "w": [1, 2, 3]})}
        s = {k: [{"name": c, "role": "id" if c.endswith("id") else "measure"} for c in v.columns] for k, v in t.items()}
        self.assertEqual(detect_relationships(t, s), [])


class SemanticTests(unittest.TestCase):
    def test_business_terms(self):
        e = Engine()
        top = lambda q, roles=None: e.ws.semantic.search(q, 3, roles)[0]
        self.assertEqual(top("sales performance", {"measure"})["column"], "revenue")
        self.assertEqual(top("area")["column"], "region")
        self.assertEqual(top("client segment")["column"], "segment")
        self.assertEqual(top("price", {"measure"})["column"], "unit_price")
        self.assertEqual(top("units", {"measure"})["column"], "quantity")


if __name__ == "__main__":
    unittest.main()
