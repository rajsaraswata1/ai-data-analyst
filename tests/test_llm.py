"""LLM path tested against a LOCAL STUB server (no real LLM API was reachable/used)."""
import json
import logging
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.agents.llm import NullProvider, OpenAICompatibleProvider, build_provider, numbers_supported
from app.agents.planner import RulePlanner, validate_llm_plan
from app.core.logging_setup import JsonFormatter
from tests.support import Engine

SECRET = "sk-test-SECRET-123"


class Stub(BaseHTTPRequestHandler):
    plan: dict = {}
    narration_mode = "honest"
    auth_seen: list = []

    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Stub.auth_seen.append(self.headers.get("Authorization"))
        system, user = body["messages"][0]["content"], body["messages"][1]["content"]
        if "query planner" in system:
            content = json.dumps(Stub.plan)
        else:
            facts = json.loads(user.split("<facts>")[1].split("</facts>")[0])
            val = facts.get("top_value", facts.get("value", 0))
            text = f"The leader reached {val}." if Stub.narration_mode == "honest" else "Revenue hit 987654321 which is huge."
            content = json.dumps({"answer": text, "insights": ["LLM insight"]})
        out = json.dumps({"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)


class LLMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def engine(self, url=None):
        e = Engine(llm_provider="openai", llm_api_key=SECRET, llm_base_url=url or self.url, llm_model="stub", llm_timeout=3)
        e.orch.provider = build_provider(e.cfg)
        return e

    def test_provider_selection(self):
        self.assertIsInstance(build_provider(Engine().cfg), NullProvider)
        e = self.engine()
        self.assertIsInstance(e.orch.provider, OpenAICompatibleProvider)
        self.assertNotIn(SECRET, repr(e.cfg))                         # key never appears in repr
        from app.config import Config
        self.assertIsInstance(build_provider(Config(llm_provider="openai", llm_api_key="", llm_model="")), NullProvider)

    def test_valid_plan_and_honest_narration_used(self):
        Stub.plan = {"mode": "rank", "dimension": {"table": "sales", "column": "region"}, "limit": 2, "order": "desc"}
        Stub.narration_mode = "honest"
        e = self.engine()
        r = e.ask("Which region generated the highest revenue?")
        self.assertTrue(r["llm"]["used_for_planning"])
        self.assertTrue(r["llm"]["used_for_narration"])
        self.assertEqual(len(r["table"]["rows"]), 2)                   # LLM's limit=2 was applied
        self.assertTrue(r["answer"].startswith("The leader reached 1930088.92") or "1930088.92" in r["answer"])
        self.assertIn(f"Bearer {SECRET}", Stub.auth_seen)

    def test_invented_numbers_are_rejected(self):
        Stub.plan = {"mode": "rank", "dimension": {"table": "sales", "column": "region"}}
        Stub.narration_mode = "hallucinate"
        r = self.engine().ask("Which region generated the highest revenue?")
        self.assertFalse(r["llm"]["used_for_narration"])
        self.assertIn("rejected", r["llm"])
        self.assertNotIn("987654321", r["answer"])
        self.assertIn("North", r["answer"])                            # deterministic answer kept

    def test_invalid_plan_fields_are_dropped(self):
        e = Engine()
        base = RulePlanner().plan("Which region has the highest revenue?", e.ws, None)
        bad = {"mode": "rank", "metric": {"table": "sales", "column": "does_not_exist", "agg": "sum"},
               "dimension": {"table": "ghost", "column": "x"}, "filters": [{"table": "sales", "column": "region", "value": "Atlantis"}],
               "limit": 10 ** 9, "order": "sideways", "chart": "hologram"}
        plan, rejected = validate_llm_plan(bad, e.ws, base)
        self.assertEqual(plan["metric"], base["metric"])
        self.assertEqual(plan["dimension"], base["dimension"])
        self.assertEqual(plan["limit"], base["limit"])
        self.assertIn("metric", rejected)
        self.assertIn("dimension", rejected)
        self.assertEqual(validate_llm_plan("garbage", e.ws, base)[0], base)

    def test_unreachable_llm_falls_back_gracefully(self):
        r = self.engine("http://127.0.0.1:9").ask("Which region generated the highest revenue?")   # closed port
        self.assertTrue(r["ok"])
        self.assertFalse(r["llm"]["used_for_narration"])
        self.assertIn("North", r["answer"])

    def test_number_guard_unit(self):
        facts = {"top_value": 1930088.92, "share": 34.2, "n": 4}
        self.assertTrue(numbers_supported("North leads with 1.93M (34.2%) across 4 regions in 2025.", facts))
        self.assertTrue(numbers_supported("Total is 1,930,088.92.", facts))
        self.assertFalse(numbers_supported("North leads with 2.5M.", facts))
        self.assertFalse(numbers_supported("Growth of 77.7%.", facts))

    def test_secrets_never_logged(self):
        records = []

        class H(logging.Handler):
            def emit(self, rec):
                records.append(JsonFormatter().format(rec))

        lg = logging.getLogger("analyst")
        h = H()
        lg.addHandler(h)
        old = lg.level
        lg.setLevel(logging.DEBUG)
        try:
            Stub.plan = {"mode": "rank"}
            self.engine().ask("Which region generated the highest revenue?")
            lg.info("x", extra={"api_key": SECRET, "password": "p", "note": "ok"})
        finally:
            lg.removeHandler(h)
            lg.setLevel(old)
        joined = "\n".join(records)
        self.assertTrue(records)
        self.assertNotIn(SECRET, joined)
        self.assertIn("llm_call", joined)
        self.assertIn('"api_key": "***"', joined)


if __name__ == "__main__":
    unittest.main()
