# Architecture

## Component diagram

```mermaid
flowchart TD
    USER([User]) --> UI["Web UI<br/>HTML + CSS + vanilla JS<br/>custom SVG charts"]
    UI -->|"JSON / SSE (X-Requested-With, session cookie)"| API["Flask API<br/>auth · upload · ask · reports"]
    API --> ORCH["Analysis Orchestrator<br/>plan → execute → validate → narrate"]
    ORCH --> PLAN["Planner<br/>rules (default) · LLM plan (optional, schema-validated)"]
    ORCH --> MEM["Conversation Memory<br/>compact structured state"]
    ORCH --> ROUTER["Tool Router<br/>arg validation · timing · logging · metrics"]

    ROUTER --> T1["get_dataset_schema / search_metadata"]
    ROUTER --> T2["get_data_quality"]
    ROUTER --> T3["run_sql"]
    ROUTER --> T4["run_pandas_analysis"]
    ROUTER --> T5["create_chart"]
    ROUTER --> T6["detect_anomalies"]
    ROUTER --> T7["forecast_metric"]
    ROUTER --> T8["generate_insights"]
    ROUTER --> T9["generate_report"]

    T1 --> SEM["Semantic index<br/>TF-IDF + business-term synonyms<br/>(metadata only)"]
    T2 --> QUAL["Quality engine<br/>pandas / NumPy"]
    T3 --> ENG["AnalyticsEngine interface<br/>SQLiteEngine (swap: DuckDB)"]
    T4 --> PD["pandas · SciPy"]
    T6 --> SK["scikit-learn IsolationForest<br/>IQR / z-score"]
    T7 --> FC["Holt · linear · seasonal<br/>backtest model selection"]
    T9 --> REP["Markdown · HTML (matplotlib PNG) · CSV"]

    ORCH -.->|"optional"| LLM["LLMProvider<br/>NullProvider | OpenAI-compatible<br/>number-guarded narration"]
    ROUTER --> CACHE[("TTL/LRU cache<br/>key = dataset fingerprint + query")]
    API --> DB[("SQLite app DB<br/>users · datasets · conversations · messages")]
    API --> LOG["Structured JSON logs<br/>request_id · tool · durations"]
    ENG --- WS["Workspace (per user)<br/>DataFrames · schema · relationships"]
    UPLOAD["Ingestion<br/>validate · decode · type-infer"] --> WS
```

## Request flow (question → answer)

```mermaid
sequenceDiagram
    participant U as UI
    participant A as Flask API
    participant O as Orchestrator
    participant P as Planner
    participant R as ToolRouter
    participant E as SQL engine / pandas
    U->>A: POST /api/conversations/{id}/ask/stream
    A->>O: run(question, conversation state)
    O->>P: plan(question, schema, memory)
    P-->>O: plan {mode, metric, dimension, filters, ...}
    O-->>U: status "Generating SQL…"
    O->>R: run_sql(build_sql(plan))
    R->>E: validate → authorizer-guarded execute
    E-->>R: result frame (cached by fingerprint)
    O-->>U: status "Validating results…"
    O->>R: independent total query (reconciliation)
    O->>R: run_pandas_analysis / create_chart / detect_anomalies …
    O->>O: narrate from computed facts (LLM optional, numbers verified)
    O-->>A: result + new memory state
    A-->>U: SSE "result" (persisted to SQLite)
```

## Key design decisions

| Decision | Why |
|---|---|
| **Plan-and-execute, not free-form code** | The planner produces a small JSON plan; deterministic builders turn it into SQL. No LLM- or user-authored code is ever executed. |
| **Every number is computed** | SQL, pandas, SciPy, scikit-learn. The narrator formats computed facts; the optional LLM may only rephrase them and its numbers are verified against the facts (`numbers_supported`) or discarded. |
| **Result validation** | Grouped sums are reconciled against an independent total query; shortfalls (e.g. rows with no matching customer) are reported, not hidden. |
| **SQL defence in depth** | static validator (single SELECT/WITH, keyword blocklist) → SQLite *authorizer* (only reads of registered tables) → timeout → row cap. |
| **Swap points** | `AnalyticsEngine` (SQLite→DuckDB), `LLMProvider`, Flask blueprint (→FastAPI), static UI (→React). |
| **Per-user workspace in memory** | Simple and fast for an assessment; files persist on disk and are reloaded lazily after restart. |
| **Cache key = dataset fingerprint + normalised query** | Uploading/removing data changes the fingerprint, so stale results are never served. |

## Module map

```
app/
  __init__.py          app factory, security headers, error handlers, request logging
  config.py            env/.env configuration (secrets excluded from repr)
  api/routes.py        HTTP endpoints (auth, datasets, ask + SSE, reports, metrics)
  agents/              orchestrator · planner · narrator · memory · llm (provider abstraction)
  tools/               base (Tool, ToolRouter) + one file per tool
  services/            ingestion · profiling · quality · relationships · semantic · sql_engine ·
                       query_builder · codegen · anomaly · forecasting · reporting · overview · registry
  core/                db · cache · logging_setup · security · errors
  static/              index.html · style.css · app.js · charts.js
```
