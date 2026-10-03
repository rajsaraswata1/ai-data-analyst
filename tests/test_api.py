import io
import json
import unittest

from tests.support import H, new_client, register, upload


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.app, self.c = new_client()

    def test_protected_routes_require_login(self):
        for path in ["/api/datasets", "/api/dashboard", "/api/conversations", "/api/metrics", "/api/quick/anomalies"]:
            self.assertEqual(self.c.get(path).status_code, 401, path)
        self.assertEqual(self.c.get("/api/health").status_code, 200)       # public
        self.assertEqual(self.c.get("/").status_code, 200)                  # SPA shell is static

    def test_csrf_header_required(self):
        r = self.c.post("/api/auth/register", json={"username": "bob12", "password": "password123"})
        self.assertEqual(r.status_code, 403)

    def test_register_login_logout_flow(self):
        self.assertEqual(register(self.c).status_code, 201)
        self.assertEqual(self.c.get("/api/auth/me").json["user"]["username"], "alice")
        self.assertEqual(self.c.post("/api/auth/logout", headers=H).status_code, 200)
        self.assertIsNone(self.c.get("/api/auth/me").json["user"])
        self.assertEqual(self.c.get("/api/datasets").status_code, 401)
        self.assertEqual(self.c.post("/api/auth/login", json={"username": "alice", "password": "password123"}, headers=H).status_code, 200)
        self.assertEqual(self.c.get("/api/datasets").status_code, 200)

    def test_validation_and_duplicates(self):
        for u, p in [("ab", "password123"), ("alice", "short"), ("bad name!", "password123")]:
            self.assertEqual(self.c.post("/api/auth/register", json={"username": u, "password": p}, headers=H).status_code, 400)
        register(self.c)
        self.assertEqual(register(self.c).status_code, 409)

    def test_passwords_are_hashed(self):
        register(self.c, pw="password123")
        row = self.app.extensions["svc"].db.get_user_by_name("alice")
        self.assertNotIn("password123", row["password_hash"])
        self.assertTrue(row["password_hash"].startswith(("scrypt:", "pbkdf2:")))

    def test_bad_login_and_throttle(self):
        register(self.c)
        self.c.post("/api/auth/logout", headers=H)
        codes = [self.c.post("/api/auth/login", json={"username": "alice", "password": "wrongpass1"}, headers=H).status_code for _ in range(6)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5], 429)
        # even the right password is refused while locked
        self.assertEqual(self.c.post("/api/auth/login", json={"username": "alice", "password": "password123"}, headers=H).status_code, 429)

    def test_security_headers_and_cookie_flags(self):
        r = self.c.get("/")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        reg = register(self.c)
        cookie = reg.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)


class DataAndAnalysisApiTests(unittest.TestCase):
    def setUp(self):
        self.app, self.c = new_client()
        register(self.c)

    def test_upload_valid_multiple_and_invalid(self):
        r = upload(self.c)
        self.assertEqual(r.status_code, 200)
        self.assertEqual({d["name"] for d in r.json["uploaded"]}, {"sales", "customers", "products"})
        bad = self.c.post("/api/datasets", data={"files": [(io.BytesIO(b""), "empty.csv"), (io.BytesIO(b"x"), "notes.txt")]}, headers=H, content_type="multipart/form-data")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(len(bad.json["failed"]), 2)
        mixed = self.c.post("/api/datasets", data={"files": [(io.BytesIO(b"a,b\n1,2\n3,4\n"), "ok.csv"), (io.BytesIO(b""), "bad.csv")]}, headers=H, content_type="multipart/form-data")
        self.assertEqual(mixed.status_code, 200)
        self.assertEqual(len(mixed.json["uploaded"]), 1)
        self.assertEqual(len(mixed.json["failed"]), 1)
        self.assertEqual(self.c.post("/api/datasets", data={}, headers=H, content_type="multipart/form-data").status_code, 400)

    def test_file_size_limit(self):
        _, c = new_client(max_upload_mb=1)
        register(c)
        big = b"a,b\n" + b"1,2\n" * 400_000
        r = c.post("/api/datasets", data={"files": [(io.BytesIO(big), "big.csv")]}, headers=H, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertIn("limit", r.json["failed"][0]["error"])

    def test_samples_dashboard_and_detail(self):
        self.assertEqual(self.c.get("/api/dashboard").json, {"empty": True})
        self.assertEqual(len(self.c.post("/api/datasets/samples", headers=H).json["loaded"]), 3)
        self.assertEqual(self.c.post("/api/datasets/samples", headers=H).json["loaded"], [])   # idempotent
        d = self.c.get("/api/dashboard").json
        labels = {k["label"] for k in d["kpis"]}
        self.assertTrue({"Total Revenue", "Total Orders", "Avg Order Value", "Unique Customers", "Data Quality Score"} <= labels)
        self.assertGreaterEqual(len(d["charts"]), 3)
        ds = self.c.get("/api/datasets").json
        sales = next(x for x in ds["datasets"] if x["name"] == "sales")
        detail = self.c.get(f"/api/datasets/{sales['dataset_id']}").json
        self.assertEqual(len(detail["preview"]["data"]), 10)
        self.assertIn("issues", detail["quality"])
        self.assertEqual(len(ds["relationships"]), 2)

    def test_dashboard_updates_after_upload(self):
        upload(self.c, ("sales",))
        before = self.c.get("/api/dashboard").json
        self.assertNotIn("category", json.dumps(before["charts"]).lower())
        upload(self.c, ("products",))
        after = self.c.get("/api/dashboard").json
        self.assertIn("category", json.dumps(after["charts"]).lower())

    def test_delete_dataset(self):
        upload(self.c)
        ds = self.c.get("/api/datasets").json["datasets"]
        pid = next(d["dataset_id"] for d in ds if d["name"] == "products")
        self.assertEqual(self.c.delete(f"/api/datasets/{pid}", headers=H).status_code, 200)
        self.assertEqual(len(self.c.get("/api/datasets").json["datasets"]), 2)
        self.assertEqual(self.c.delete(f"/api/datasets/{pid}", headers=H).status_code, 404)

    def ask(self, cid, q):
        return self.c.post(f"/api/conversations/{cid}/ask", json={"question": q}, headers=H)

    def test_ask_persist_context_and_reports(self):
        upload(self.c)
        cid = self.c.post("/api/conversations", headers=H).json["id"]
        r1 = self.ask(cid, "Which region generated the highest revenue?")
        self.assertEqual(r1.status_code, 200)
        r2 = self.ask(cid, "Show me its monthly trend")
        self.assertEqual(r2.json["result"]["mode"], "trend")
        msgs = self.c.get(f"/api/conversations/{cid}/messages").json["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user", "assistant"])
        mid = r1.json["message_id"]
        md = self.c.get(f"/api/reports/message/{mid}.md")
        self.assertEqual(md.status_code, 200)
        text = md.get_data(as_text=True)
        for needle in ("Key findings", "### SQL", "Analysis method", "Assumptions", "Datasets used"):
            self.assertIn(needle, text)
        html = self.c.get(f"/api/reports/message/{mid}.html").get_data(as_text=True)
        self.assertIn("data:image/png;base64,", html)
        csv = self.c.get(f"/api/reports/message/{mid}.csv").get_data(as_text=True)
        self.assertTrue(csv.startswith("region,total_revenue"))
        session_html = self.c.get(f"/api/reports/conversation/{cid}.html")
        self.assertEqual(session_html.status_code, 200)
        self.assertEqual(self.c.get(f"/api/reports/message/{mid}.pdf").status_code, 404)

    def test_html_report_escapes_user_text(self):
        upload(self.c)
        cid = self.c.post("/api/conversations", headers=H).json["id"]
        mid = self.ask(cid, "<script>alert(1)</script> show revenue by region").json["message_id"]
        html = self.c.get(f"/api/reports/message/{mid}.html").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_csv_export_neutralises_formulas(self):
        from app.services.reporting import to_csv
        out = to_csv({"table": {"columns": ["a"], "rows": [["=HYPERLINK(\"x\")"], ["+cmd"], ["-5"], ["ok"]]}})
        self.assertIn("'=HYPERLINK", out)
        self.assertIn("'+cmd", out)
        self.assertIn("-5", out.replace("'", ""))

    def test_stream_endpoint_emits_real_events_and_persists(self):
        upload(self.c)
        cid = self.c.post("/api/conversations", headers=H).json["id"]
        body = self.c.post(f"/api/conversations/{cid}/ask/stream", json={"question": "What are the top five customers?"}, headers=H).get_data(as_text=True)
        events = [l.split(": ", 1)[1] for l in body.splitlines() if l.startswith("event: ")]
        self.assertEqual(events[-1], "result")
        self.assertGreaterEqual(events.count("status"), 4)
        self.assertIn("Running SQL", body)
        self.assertEqual(len(self.c.get(f"/api/conversations/{cid}/messages").json["messages"]), 2)

    def test_input_validation_and_error_hygiene(self):
        upload(self.c)
        cid = self.c.post("/api/conversations", headers=H).json["id"]
        self.assertEqual(self.ask(cid, "").status_code, 400)
        self.assertEqual(self.ask(cid, "x" * 1001).status_code, 400)
        self.assertEqual(self.c.post("/api/conversations/nope/ask", json={"question": "hi"}, headers=H).status_code, 404)
        r = self.ask(cid, "zzzz qqqq")
        self.assertNotIn("Traceback", r.get_data(as_text=True))
        self.assertEqual(self.c.get("/api/nothing-here").status_code, 404)
        self.assertEqual(self.c.get("/api/quick/unknown").status_code, 404)

    def test_quick_endpoints(self):
        upload(self.c)
        self.assertGreater(self.c.get("/api/quick/anomalies").json["anomalies"][0]["actual"], 0)
        f = self.c.get("/api/quick/forecast?horizon=3").json
        self.assertEqual(f["forecast"]["horizon"], 3)
        self.assertEqual(self.c.get("/api/quick/quality").json["intent"], "DATA_QUALITY")

    def test_users_are_isolated(self):
        upload(self.c)
        cid = self.c.post("/api/conversations", headers=H).json["id"]
        mid = self.ask(cid, "Which region has the highest revenue?").json["message_id"]
        c2 = self.app.test_client()
        register(c2, "mallory")
        self.assertEqual(c2.get("/api/datasets").json["datasets"], [])
        self.assertEqual(c2.get(f"/api/conversations/{cid}/messages").status_code, 404)
        self.assertEqual(c2.get(f"/api/reports/message/{mid}.md").status_code, 404)
        self.assertEqual(c2.get(f"/api/reports/conversation/{cid}.md").status_code, 404)
        self.assertEqual(c2.post(f"/api/conversations/{cid}/ask", json={"question": "hi"}, headers=H).status_code, 404)
        ds_id = self.c.get("/api/datasets").json["datasets"][0]["dataset_id"]
        self.assertEqual(c2.delete(f"/api/datasets/{ds_id}", headers=H).status_code, 404)
        self.assertEqual(len(self.c.get("/api/datasets").json["datasets"]), 3)

    def test_state_survives_app_restart(self):
        """Datasets (disk) and conversation context (SQLite) persist; a new app instance on the same data dir works."""
        from app import create_app
        upload(self.c)
        cid = self.c.post("/api/conversations", headers=H).json["id"]
        self.ask(cid, "Which region has the highest revenue?")
        cfg = self.app.extensions["svc"].cfg
        c2 = create_app(cfg).test_client()
        self.assertEqual(c2.post("/api/auth/login", json={"username": "alice", "password": "password123"}, headers=H).status_code, 200)
        self.assertEqual(len(c2.get("/api/datasets").json["datasets"]), 3)
        r = c2.post(f"/api/conversations/{cid}/ask", json={"question": "Show me its monthly trend"}, headers=H).json["result"]
        self.assertEqual(r["mode"], "trend")
        self.assertIn("North", " ".join(r["assumptions"]))

    def test_metrics_endpoint_records_tool_calls(self):
        upload(self.c)
        self.ask(self.c.post("/api/conversations", headers=H).json["id"], "Which region has the highest revenue?")
        m = self.c.get("/api/metrics").json
        self.assertGreater(m["tools"]["run_sql"]["calls"], 0)
        self.assertIn("hit_rate", m["cache"])


if __name__ == "__main__":
    unittest.main()
