"""Run a fixed set of questions through the orchestrator and dump the JSON results (used by the UI smoke test and gallery)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.support import Engine  # noqa: E402

QUESTIONS = ["Which region generated the highest revenue?", "Show monthly sales trends.", "Which products are underperforming?",
             "What are the top five customers?", "Detect anomalies in the dataset.", "Forecast sales for the next 6 months.",
             "Give me an executive summary", "Check data quality", "distribution of revenue", "scatter of unit price vs quantity",
             "revenue by category as a pie chart", "Why did revenue fall in March in the North region?", "Revenue by region monthly trend"]

if __name__ == "__main__":
    e = Engine()
    out = [e.ask(q) for q in QUESTIONS]
    json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/results.json", "w"), default=str)
    print(f"wrote {len(out)} results")
