import unittest

import pandas as pd

from app.core.errors import ValidationError
from app.services.ingestion import parse_csv, table_name_from_filename, validate_upload
from app.services.profiling import profile_dataframe
from app.services.quality import assess_quality


def load(raw: bytes, name="t.csv"):
    df, rep = parse_csv(raw, name, 1_000_000)
    return df, rep, profile_dataframe(df, rep.kinds, rep.invalid_counts)


class IngestionTests(unittest.TestCase):
    def test_rejects_bad_uploads(self):
        for name, raw in [("a.txt", b"a,b\n1,2"), ("a.csv", b""), ("a.csv", b"   \n"), ("a.csv", b"\x00\x01binary")]:
            with self.assertRaises(ValidationError):
                validate_upload(name, raw, 50)
        with self.assertRaises(ValidationError):
            validate_upload("big.csv", b"a,b\n" + b"1,2\n" * 400_000, 1)

    def test_header_only_and_ragged(self):
        with self.assertRaises(ValidationError):
            parse_csv(b"a,b\n", "h.csv", 100)
        df, rep, _ = load(b"a,b\n1,2\n3,4,5\n6,7\n")
        self.assertEqual(len(df), 2)
        self.assertEqual(rep.skipped_rows, 1)

    def test_encoding_fallback_and_delimiter(self):
        df, rep, _ = load("name;city\nJosé;Zürich\nAna;Köln\n".encode("cp1252"))
        self.assertEqual(rep.encoding, "cp1252")
        self.assertEqual(rep.delimiter, ";")
        self.assertEqual(list(df.columns), ["name", "city"])
        self.assertIn("José", df["name"].tolist())

    def test_duplicate_and_missing_headers(self):
        df, rep, _ = load(b"Amount,Amount,,Order Date\n1,2,3,2024-01-01\n4,5,6,2024-01-02\n")
        self.assertEqual(list(df.columns), ["amount", "amount_2", "unnamed_3", "order_date"])
        self.assertTrue(any("Duplicate" in w for w in rep.warnings))

    def test_headerless(self):
        df, rep, _ = load(b"1,2,3\n4,5,6\n")
        self.assertEqual(list(df.columns), ["col_1", "col_2", "col_3"])
        self.assertEqual(len(df), 2)

    def test_type_inference(self):
        rows = "\n".join('%d,"₹%s",2024-01-%02d,yes,%s' % (i, format(i * 1000, ","), i % 28 + 1, "0" + str(i).zfill(4)) for i in range(1, 41))
        df, rep, _ = load(("id,price,day,flag,zip\n" + rows + "\n").encode())
        self.assertEqual(rep.kinds["price"], "integer")
        self.assertEqual(df["price"].iloc[2], 3000)                       # currency/commas stripped
        self.assertEqual(rep.kinds["day"], "datetime")
        self.assertEqual(rep.kinds["flag"], "boolean")
        self.assertEqual(rep.kinds["zip"], "text")                        # leading zeros preserved as text

    def test_invalid_values_are_counted_not_hidden(self):
        vals = [str(i) for i in range(1, 50)] + ["oops"]
        df, rep, schema = load(("v,d\n" + "\n".join(f"{v},2024-01-01" for v in vals) + "\n").encode())
        self.assertEqual(rep.kinds["v"], "integer")
        self.assertEqual(rep.invalid_counts["v"], 1)
        self.assertEqual(int(df["v"].isna().sum()), 1)

    def test_table_names_unique(self):
        self.assertEqual(table_name_from_filename("My Sales (2024).csv", set()), "my_sales_2024")
        self.assertEqual(table_name_from_filename("a.csv", {"a"}), "a_2")


class QualityTests(unittest.TestCase):
    def test_perfect_data_scores_100(self):
        df, rep, sch = load(("id,v\n" + "\n".join(f"{i},{i * 2}" for i in range(50)) + "\n").encode())
        q = assess_quality(df, sch, rep.invalid_counts)
        self.assertEqual(q["score"], 100.0)

    def test_detects_issues_with_exact_numbers(self):
        body = [f"{i},{i % 3},A,x" for i in range(100)] + ["5,5,A,x", "7,7,A,x"]   # 2 duplicates (rows 5 and 7 repeat)
        body = [b if i not in range(10) else f"{b.split(',')[0]},,A,x" for i, b in enumerate(body)]  # 10 missing in col2
        df, rep, sch = load(("id,m,c,const\n" + "\n".join(body) + "\n").encode())
        q = assess_quality(df, sch, rep.invalid_counts)
        types = {(i["type"], tuple(i["columns"])): i for i in q["issues"]}
        miss = types[("missing_values", ("m",))]
        self.assertEqual(miss["affected_rows"], int(df["m"].isna().sum()))
        self.assertIn("missing values", miss["message"])
        self.assertIn(("constant_column", ("const",)), types)
        self.assertEqual(q["summary"]["duplicate_rows"], int(df.duplicated().sum()))
        self.assertLess(q["score"], 100)
        self.assertGreater(q["score"], 80)

    def test_sample_sales_quality(self):
        from tests.support import sample_bytes
        df, rep, sch = load(sample_bytes("sales"), "sales.csv")
        q = assess_quality(df, sch, rep.invalid_counts)
        self.assertEqual(q["summary"]["duplicate_rows"], 6)
        self.assertTrue(any(i["type"] == "missing_values" and i["columns"] == ["discount"] for i in q["issues"]))
        self.assertTrue(any(i["type"] == "numeric_outliers" for i in q["issues"]))


if __name__ == "__main__":
    unittest.main()
