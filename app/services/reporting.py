"""Report export (Markdown, HTML with embedded PNG charts, CSV) built from stored analysis results."""
from __future__ import annotations

import base64
import csv
import html
import io
from datetime import datetime, timezone
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

NO_ASSUMPTIONS = "No special assumptions: all columns and filters were named in the question; standard definitions were applied (see Analysis method)."
PALETTE = ["#4f46e5", "#06b6d4", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#ec4899", "#64748b"]


def render_chart_png(spec: dict[str, Any]) -> bytes:
    """Server-side rendering of a chart spec (used for HTML reports). Data comes from the spec only."""
    fig, ax = plt.subplots(figsize=(7.5, 3.8), dpi=110)
    t = spec.get("type")
    labels, series = spec.get("labels", []), spec.get("series", [])
    if t == "bar":
        w = 0.8 / max(len(series), 1)
        for i, s in enumerate(series):
            ax.bar([x + i * w for x in range(len(labels))], [v or 0 for v in s["values"]], w, label=s["name"], color=PALETTE[i % 8])
        ax.set_xticks([x + 0.4 - w / 2 for x in range(len(labels))])
        ax.set_xticklabels(labels, rotation=30 if len(labels) > 5 else 0, ha="right" if len(labels) > 5 else "center")
    elif t == "hbar":
        vals = series[0]["values"]
        ax.barh(labels[::-1], [v or 0 for v in vals][::-1], color=PALETTE[0])
    elif t == "line":
        for i, s in enumerate(series):
            ax.plot(range(len(labels)), s["values"], marker="o" if len(labels) < 40 else None, ms=3, label=s["name"],
                    color=PALETTE[i % 8], linestyle="--" if s.get("dashed") else "-")
        if spec.get("band"):
            ax.fill_between(range(len(labels)), [v if v is not None else float("nan") for v in spec["band"]["lower"]],
                            [v if v is not None else float("nan") for v in spec["band"]["upper"]], alpha=0.2, color=PALETTE[2], label="95% interval (approx.)")
        step = max(1, len(labels) // 12)
        ax.set_xticks(range(0, len(labels), step))
        ax.set_xticklabels(labels[::step], rotation=45, ha="right")
    elif t == "pie":
        ax.pie(series[0]["values"], labels=labels, autopct="%1.1f%%", colors=PALETTE, startangle=90)
    elif t == "scatter":
        ax.scatter([p["x"] for p in spec["points"]], [p["y"] for p in spec["points"]], s=10, color=PALETTE[0], alpha=0.6)
    elif t == "histogram":
        ax.bar([(b["x0"] + b["x1"]) / 2 for b in spec["bins"]], [b["count"] for b in spec["bins"]],
               width=[(b["x1"] - b["x0"]) * 0.95 for b in spec["bins"]], color=PALETTE[0])
    if spec.get("reference") and t in ("hbar", "bar"):
        (ax.axvline if t == "hbar" else ax.axhline)(spec["reference"]["value"], color="#ef4444", ls="--", lw=1, label=spec["reference"].get("label", ""))
    ax.set_title(spec.get("title", ""), fontsize=11)
    if t not in ("pie",):
        ax.set_xlabel(spec.get("x_label", "")), ax.set_ylabel(spec.get("y_label", ""))
        ax.grid(alpha=0.25)
    if len(series) > 1 or spec.get("band") or spec.get("reference"):
        ax.legend(fontsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def _tbl_md(columns: list[str], rows: list[list[Any]]) -> str:
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(esc(c) for c in columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(esc(v) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def _chart_data_rows(spec: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    if spec.get("labels") is not None and spec.get("series"):
        cols = [spec.get("x_label") or "label"] + [s["name"] for s in spec["series"]]
        return cols, [[l] + [s["values"][i] for s in spec["series"]] for i, l in enumerate(spec["labels"])]
    if spec.get("bins"):
        return ["from", "to", "count"], [[round(b["x0"], 2), round(b["x1"], 2), b["count"]] for b in spec["bins"]]
    return [], []


def to_markdown(results: list[dict[str, Any]], title: str = "AI Data Analyst Report") -> str:
    out = [f"# {title}", f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_", ""]
    for i, r in enumerate(results, 1):
        out += [f"## {i}. {r.get('question', 'Analysis')}", "", f"**Datasets used:** {', '.join(r.get('sources') or []) or 'n/a'}", "",
                "### Key findings", "", r.get("answer", ""), ""]
        out += [f"- {x}" for x in r.get("insights", [])] + [""]
        for ch in r.get("charts", []):
            cols, rows = _chart_data_rows(ch)
            if cols:
                out += [f"**Chart: {ch.get('title', '')}** ({ch.get('type')})", "", _tbl_md(cols, rows[:60]), ""]
        if r.get("anomalies"):
            out += ["### Anomalies", "", _tbl_md(["Row", "ID", "Actual", "Expected range", "Score", "Reason"],
                    [[a["row"], a.get("id") or "", a["actual"], f"{a['expected_low']} – {a['expected_high']}", a["score"], a["reason"]] for a in r["anomalies"][:15]]), ""]
        if r.get("forecast"):
            f = r["forecast"]["forecast"]
            out += ["### Forecast (estimate)", "", f"Model: {r['forecast']['model']}. {r['forecast']['disclaimer']}", "",
                    _tbl_md(["Period", "Forecast", "Lower (95%, approx.)", "Upper (95%, approx.)"], list(zip(f["periods"], f["point"], f["lower"], f["upper"]))), ""]
        if r.get("sql"):
            out += ["### SQL", "", "```sql", r["sql"], "```", ""]
        if r.get("pandas_code"):
            out += ["### Equivalent pandas", "", "```python", r["pandas_code"], "```", ""]
        if r.get("method"):
            out += ["### Analysis method", ""] + [f"{n}. {s}" for n, s in enumerate(r["method"], 1)] + [""]
        if r.get("validation"):
            out += ["### Validation", ""] + [f"- {v}" for v in r["validation"]] + [""]
        out += ["### Assumptions", ""] + [f"- {a}" for a in (r.get("assumptions") or [NO_ASSUMPTIONS])] + [""]
    return "\n".join(out)


def to_html(results: list[dict[str, Any]], title: str = "AI Data Analyst Report") -> str:
    e = html.escape
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{e(title)}</title><style>"
             "body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1e293b;line-height:1.5}"
             "h1{color:#4f46e5}pre{background:#0f172a;color:#e2e8f0;padding:1rem;border-radius:8px;overflow:auto}"
             "table{border-collapse:collapse;width:100%;font-size:.85rem}td,th{border:1px solid #e2e8f0;padding:4px 8px;text-align:left}"
             "th{background:#f1f5f9}.card{border:1px solid #e2e8f0;border-radius:12px;padding:1rem 1.5rem;margin:1.5rem 0}</style></head><body>",
             f"<h1>{e(title)}</h1><p><em>Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</em></p>"]
    for i, r in enumerate(results, 1):
        parts.append(f"<div class='card'><h2>{i}. {e(r.get('question', 'Analysis'))}</h2>")
        parts.append(f"<p><strong>Datasets used:</strong> {e(', '.join(r.get('sources') or []) or 'n/a')}</p>")
        parts.append(f"<h3>Key findings</h3><p>{e(r.get('answer', ''))}</p><ul>" + "".join(f"<li>{e(x)}</li>" for x in r.get("insights", [])) + "</ul>")
        for ch in r.get("charts", []):
            try:
                b64 = base64.b64encode(render_chart_png(ch)).decode()
                parts.append(f"<img alt='{e(ch.get('title', 'chart'))}' style='max-width:100%' src='data:image/png;base64,{b64}'>")
            except Exception:
                parts.append(f"<p><em>Chart '{e(ch.get('title', ''))}' could not be rendered.</em></p>")
        if r.get("anomalies"):
            parts.append("<h3>Anomalies</h3><table><tr><th>Row</th><th>ID</th><th>Actual</th><th>Expected range</th><th>Score</th><th>Reason</th></tr>" +
                         "".join(f"<tr><td>{a['row']}</td><td>{e(str(a.get('id') or ''))}</td><td>{a['actual']}</td><td>{a['expected_low']} – {a['expected_high']}</td><td>{a['score']}</td><td>{e(a['reason'])}</td></tr>" for a in r["anomalies"][:15]) + "</table>")
        if r.get("forecast"):
            f = r["forecast"]["forecast"]
            parts.append(f"<h3>Forecast (estimate)</h3><p>Model: {e(r['forecast']['model'])}. {e(r['forecast']['disclaimer'])}</p><table><tr><th>Period</th><th>Forecast</th><th>Lower</th><th>Upper</th></tr>" +
                         "".join(f"<tr><td>{p}</td><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for p, a, b, c in zip(f["periods"], f["point"], f["lower"], f["upper"])) + "</table>")
        if r.get("sql"):
            parts.append(f"<h3>SQL</h3><pre>{e(r['sql'])}</pre>")
        if r.get("pandas_code"):
            parts.append(f"<h3>Equivalent pandas</h3><pre>{e(r['pandas_code'])}</pre>")
        if r.get("method"):
            parts.append("<h3>Analysis method</h3><ol>" + "".join(f"<li>{e(s)}</li>" for s in r["method"]) + "</ol>")
        if r.get("validation"):
            parts.append("<h3>Validation</h3><ul>" + "".join(f"<li>{e(v)}</li>" for v in r["validation"]) + "</ul>")
        parts.append("<h3>Assumptions</h3><ul>" + "".join(f"<li>{e(a)}</li>" for a in (r.get("assumptions") or [NO_ASSUMPTIONS])) + "</ul>")
        parts.append("</div>")
    parts.append("</body></html>")
    return "".join(parts)


def _csv_safe(v: Any) -> Any:
    """Neutralise spreadsheet formula injection in exported cells."""
    return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") and not v.replace(".", "", 1).lstrip("-+").isdigit() else v


def to_csv(result: dict[str, Any]) -> str:
    tbl = result.get("table") or {}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(tbl.get("columns", []))
    for row in tbl.get("rows", []):
        w.writerow([_csv_safe(v) for v in row])
    return buf.getvalue()
