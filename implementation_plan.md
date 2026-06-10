# Project Clasp — Implementation Plan
**Industrial Process Intelligence on Silex**
*Built on: Silex Memory Engine + WorkerOrchestrator from Kronos (e:\AGI)*

---

## What Is Clasp?

Clasp is a **standalone industrial AI system** that embeds the Silex cognitive engine and the Kronos agent orchestration layer to build a **living causal map** of industrial plants.

The core value: instead of dashboards that show *what* happened, Clasp answers **WHY** — tracing causal chains backward from any fault to its root cause, and forward to warn about emerging problems before they happen.

Derived from the KRONOS_INDUSTRIAL_BLUEPRINT.md.

---

## Engine Export Strategy

### What we export from `e:\AGI` (read-only copy)

| Source (Kronos) | What it gives Clasp |
|---|---|
| `silex/storage/database.py` | Async SQLite engine (aiosqlite-backed) |
| `silex/world/graph.py` | `KnowledgeGraph` — NetworkX + SQLite causal graph with BFS CTE traversal |
| `silex/models/schemas.py` | `KnowledgeNode`, `CausalEdge`, `NodeType`, `EdgeType` — Pydantic schemas |
| `silex/memory/memory_store.py` | `MemoryStore` — episodic/semantic memory CRUD |
| `silex/llm/` (base, registry, factory, router) | Multi-provider LLM abstraction (Gemini, OpenAI-compat, Ollama) |
| `agent/orchestrator.py` + `agent/subagent.py` | `WorkerOrchestrator` — isolated worker spawning |
| `agent/security/` | `ActuationLease` — permission-scoped execution |

### Export method
We **copy** (not install) the `silex` and `agent` packages into `clasp/` as vendored subpackages:
```
clasp/
  vendor/
    silex/    ← copied from e:\AGI\silex
    agent/    ← copied from e:\AGI\agent
```
This keeps Clasp **fully self-contained** and decoupled from Kronos. When Silex gets an update, we pull selectively.

> [!IMPORTANT]
> The vendored `silex` and `agent` code is **read-only** in Clasp. All industrial extensions live in `clasp/industrial/`, never inside `vendor/`.

---

## Project Structure

```
clasp/
├── vendor/
│   ├── silex/          ← vendored Silex engine (copied)
│   └── agent/          ← vendored orchestrator (copied)
│
├── industrial/         ← all Clasp-specific code lives here
│   ├── __init__.py
│   ├── schemas.py      ← industrial node/edge types (extends silex schemas)
│   ├── engine.py       ← IndustrialSilexEngine (wraps KnowledgeGraph + DB)
│   ├── causal_learner.py   ← time-series causal discovery loop
│   ├── time_buffer.py  ← rolling in-memory time-series buffer
│   │
│   ├── ingest/
│   │   ├── opcua_connector.py   ← asyncua OPC-UA client
│   │   ├── csv_adapter.py       ← historical CSV/historian replay
│   │   └── tep_simulator.py     ← Tennessee Eastman Process simulator
│   │
│   ├── agents/
│   │   ├── watcher_agent.py     ← continuous precursor monitor
│   │   ├── root_cause_agent.py  ← backward causal trace + LLM narration
│   │   └── optimizer_agent.py   ← parameter change recommendations
│   │
│   └── api/
│       ├── main.py      ← FastAPI app
│       ├── routes.py    ← REST endpoints
│       └── ws.py        ← WebSocket alert stream
│
├── dashboard/          ← React + D3.js frontend
│   ├── src/
│   │   ├── views/
│   │   │   ├── LiveView.jsx        ← real-time plant state
│   │   │   ├── CausalExplorer.jsx  ← interactive graph
│   │   │   └── InvestigationView.jsx ← root cause timeline
│   │   └── components/
│   └── package.json
│
├── tests/
│   ├── test_causal_learner.py
│   ├── test_root_cause_agent.py
│   └── test_watcher_agent.py
│
├── data/
│   └── tep/           ← Tennessee Eastman Process dataset (downloaded)
│
├── pyproject.toml
├── README.md
└── .env.example
```

---

## Proposed Changes — Layer by Layer

---

### Layer 0 — Vendor Export

#### [NEW] `clasp/vendor/` directory
Copy `silex/` and `agent/` from `e:\AGI` using a script:
```bash
# export_engines.py — run once
python scripts/export_engines.py
```

This script rsync-copies the two packages and pins a `VENDOR_VERSION` file with the commit hash from Kronos, so you always know which version of Silex is embedded.

---

### Layer 1 — Industrial Schemas

#### [NEW] `clasp/industrial/schemas.py`

Extends `silex.models.schemas` with industrial-specific Pydantic models and enums without modifying the vendored code:

**New NodeTypes** (extend `NodeType` enum via Pydantic/string):
- `ProcessVariable` — measured value (temperature, pressure, flow, level)
- `EquipmentUnit` — physical device (valve, pump, reactor)
- `AlarmEvent` — triggered alarm or fault
- `OperatorAction` — a human action (opened valve, changed setpoint)
- `QualityMetric` — product quality measurement (yield, purity)

**New EdgeTypes** (extend `EdgeType`):
- `causes_with_lag` — `causes(lag_seconds, confidence, condition)`
- `part_of` — sensor measures equipment unit
- `precedes` — temporal ordering, no causal certainty yet

**New Models:**
- `SensorObservation(node_id, value, timestamp, unit)`
- `CausalPattern(precursor_node, outcome_node, lag_seconds, confidence, occurrences)`
- `Alert(type, outcome_risk, pattern, estimated_time_to_impact, confidence)`

---

### Layer 2 — Industrial Engine

#### [NEW] `clasp/industrial/time_buffer.py`
Rolling in-memory buffer (pandas DataFrame) for the last N seconds of sensor readings. Supports:
- `record(node_id, value, timestamp)`
- `get_changes_after(timestamp, window_seconds)` — returns significant changes
- `get_changes_before(timestamp, window_seconds)` — for backward lookup
- `is_significant_change(node_id, value)` — Z-score threshold

#### [NEW] `clasp/industrial/causal_learner.py`
The core discovery engine. On every significant sensor change:
1. Scans the time buffer forward for correlated changes (potential effects)
2. Tests if correlation count ≥ `min_occurrences` threshold
3. If yes → writes a `causes_with_lag` edge to `KnowledgeGraph`
4. Also scans backward for what may have caused this

Uses the vendored `KnowledgeGraph.add_edge()` and `KnowledgeGraph.find_causal_chain()` directly.

#### [NEW] `clasp/industrial/engine.py`
`IndustrialSilexEngine` — the top-level entry point for Clasp:
- Wraps `Database` + `KnowledgeGraph` + `MemoryStore`
- Exposes `observe(node_id, value, timestamp)` — main ingestion method
- Exposes `root_cause_analysis(affected_node, event_time, max_depth)` — backward BFS
- Exposes `get_current_state()` — latest reading per node
- Exposes `export_graph_for_visualization()` — serializes graph for D3.js
- **Does NOT subclass** Silex internals — wraps them via composition

---

### Layer 3 — Data Ingestion

#### [NEW] `clasp/industrial/ingest/tep_simulator.py`
Replays the Tennessee Eastman Process CSV at configurable speed multipliers. No real hardware needed. Supports fault injection at a specific time index.

#### [NEW] `clasp/industrial/ingest/csv_adapter.py`
Generic historical CSV adapter. Reads any time-series CSV with configurable column mapping → `observe()` calls.

#### [NEW] `clasp/industrial/ingest/opcua_connector.py`
Async OPC-UA client using `asyncua`. Discovers the node tree, maps OPC-UA node IDs to Clasp node IDs, streams live readings.

---

### Layer 4 — Domain Agents

#### [NEW] `clasp/industrial/agents/root_cause_agent.py`
- Calls `engine.root_cause_analysis(affected_node, event_time)`
- Formats the causal chain as a structured prompt
- Sends to LLM (via vendored `silex.llm`) for plain-language narration
- Returns the explanation + structured chain

#### [NEW] `clasp/industrial/agents/watcher_agent.py`
- Runs as a background task (asyncio)
- Pre-loads all causal paths leading to `QualityMetric` and `AlarmEvent` nodes
- On every `observe()` call, checks if the incoming reading matches a known precursor pattern
- Fires alerts when pattern confidence × observation confidence exceeds threshold

#### [NEW] `clasp/industrial/agents/optimizer_agent.py`
- Queries the graph for edges from controllable inputs to output metrics
- Finds parameter combinations with high historical confidence
- Returns concrete recommendations with predicted improvement and evidence count

---

### Layer 5 — FastAPI Backend

#### [NEW] `clasp/industrial/api/main.py`
FastAPI app with:
- `GET /api/plant/status` — current state of all nodes
- `POST /api/investigate` — trigger root cause analysis
- `GET /api/graph` — full causal graph (for D3.js)
- `GET /api/alerts` — active alerts list
- `WebSocket /ws/alerts` — real-time alert push

---

### Layer 6 — React Dashboard

#### [NEW] `clasp/dashboard/`
Vite + React application:
- **Live View** — color-coded plant state, active alerts
- **Causal Explorer** — D3.js force-directed graph, click to explore neighborhoods
- **Investigation View** — causal chain timeline with timestamps and confidence bars

---

## Open Questions

> [!IMPORTANT]
> **Q1: Where should Clasp live?**
> Should `clasp/` be a new directory inside `e:\AGI\` (sibling to `silex/`) or a completely separate folder (e.g. `e:\Clasp\`)? A separate location is cleaner since it's a different product. **Please confirm.**

> [!IMPORTANT]
> **Q2: LLM for narration**
> The Root Cause Agent needs an LLM to convert the causal chain into plain English. Should it use:
> - The same multi-provider system from Silex (Gemini / LM Studio / OpenAI-compat)?
> - Or a fixed provider configured separately for Clasp?

> [!NOTE]
> **Q3: Dashboard tech**
> The blueprint says React + D3.js. The Kronos repo already has `kronos-ink-ui/` (existing React dashboard). Should Clasp reuse its design language, or start fresh?

> [!NOTE]
> **Q4: TEP Dataset**
> The Tennessee Eastman Process dataset is free but needs to be downloaded manually from Harvard Dataverse. Do you want me to include the download script in the build setup?

---

## Dependencies (new for Clasp, on top of what Silex already uses)

```toml
# clasp/pyproject.toml additions
asyncua = ">=1.0"          # OPC-UA connector
pandas = ">=2.0"           # time-series buffer
numpy = ">=1.24"           # Z-score for significance detection
fastapi = ">=0.110"        # REST API
uvicorn = ">=0.29"         # ASGI server
websockets = ">=12.0"      # WebSocket alerts
```

(Silex core deps — networkx, aiosqlite, pydantic, openai, google-genai — are inherited via the vendor copy.)

---

## Build Milestones

| Milestone | Goal | Done When |
|---|---|---|
| M1 — "It learns" | TEP simulator feeds data, causal graph auto-builds edges | `engine.get_all_edges()` returns ≥20 physically sensible edges after 10min of simulated data |
| M2 — "It explains" | Root Cause Agent traces root cause for 3 TEP faults | `test_root_cause.py` passes for fault types 1, 4, 5 |
| M3 — "It warns" | Watcher fires alert ≥5 min before quality drop | `test_watcher_precursor.py` passes with ≥5min warning for fault 5 |
| M4 — "Someone can see it" | Web dashboard shows live state + alert + investigation | 2-min screen demo: data → alert → investigation → explanation |
| M5 — "A real plant can use it" | OPC-UA connector works with real/simulated OPC-UA server | Data flows from OPC-UA through full stack into graph |

---

## Verification Plan

### Automated Tests
```bash
# Unit tests
pytest clasp/tests/test_causal_learner.py
pytest clasp/tests/test_root_cause_agent.py
pytest clasp/tests/test_watcher_agent.py

# Integration test: TEP Fault 5 end-to-end
python clasp/tests/integration/test_tep_fault5.py
```

### Manual Verification
- Feed TEP normal data (d00.dat) for 10 simulated minutes → inspect graph for sensible edges
- Inject Fault 5 → verify Watcher alert fires → verify Root Cause Agent identifies `XMV_10` (cooling water)
- Open dashboard → confirm live view updates, graph is interactive, investigation renders correctly
