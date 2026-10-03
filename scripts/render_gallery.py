"""Render real analysis charts to PNG with the server-side renderer (matplotlib) for the docs.
NOTE: these are chart renders of real results, NOT screenshots of the web UI."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.services.reporting import render_chart_png  # noqa: E402
from tests.support import Engine  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
PICKS = {"region_ranking": "Which region generated the highest revenue?", "monthly_trend": "Show monthly sales trends.",
         "forecast": "Forecast sales for the next 6 months.", "anomalies": "Detect anomalies in the dataset.",
         "category_pie": "revenue by category as a pie chart", "underperforming_products": "Which products are underperforming?",
         "revenue_distribution": "distribution of revenue"}
if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    e = Engine()
    for name, q in PICKS.items():
        (OUT / f"{name}.png").write_bytes(render_chart_png(e.ask(q)["charts"][0]))
        print("wrote", name)
