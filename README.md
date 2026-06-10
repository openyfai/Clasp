# Clasp — Industrial Process Intelligence

**Clasp** builds a living causal graph of industrial plants. Instead of dashboards that show *what* happened, Clasp answers **WHY** — tracing causal chains backward from any fault to its root cause, and forward to warn about emerging problems before they happen.

## Architecture

```
LAYER 4: Dashboard (React + D3.js)     ← "Someone can see it"
LAYER 3: Agents (Watcher, RootCause, Optimizer)
LAYER 2: IndustrialSilexEngine         ← core causal engine
LAYER 1: Data Ingestion (TEP / CSV / OPC-UA)
```

Built on top of the Silex cognitive engine and Kronos agent orchestration layer from [Kronos](e:\AGI).

## Quickstart (Development)

### 1. Install dependencies

```bash
cd e:\Clasp
pip install -e ".[dev]"
```

### 2. Export vendor engines (Phase 0)

```bash
python scripts/export_engines.py
```

### 3. Run Phase 0 tests

```bash
pytest tests/test_phase0_imports.py -v
```

### 4. Download TEP dataset (Phase 2)

```bash
python scripts/download_tep.py
```

### 5. Start the API server

```bash
uvicorn clasp.industrial.api.main:app --reload
```

## Project Structure

```
clasp/
├── vendor/           ← vendored Silex + Agent (read-only)
├── industrial/       ← all Clasp code
│   ├── schemas.py    ← industrial node/edge types
│   ├── engine.py     ← IndustrialSilexEngine
│   ├── causal_learner.py
│   ├── time_buffer.py
│   ├── ingest/       ← TEP simulator, CSV adapter, OPC-UA
│   ├── agents/       ← Watcher, RootCause, Optimizer
│   └── api/          ← FastAPI backend
└── config.py         ← paths + parameters

dashboard/            ← React + D3.js frontend (Phase 5)
data/tep/             ← Tennessee Eastman Process dataset
tests/                ← test suites per phase
scripts/              ← export_engines.py, download_tep.py
```

## Milestones

| # | Name | Definition of Done |
|---|---|---|
| M1 | "It learns" | ≥20 causal edges after 10min TEP normal data at 500× |
| M2 | "It explains" | Root cause traced to XMV_10 for TEP Fault 5 |
| M3 | "It warns" | Watcher fires ≥5min before quality drop |
| M4 | "Someone can see it" | 2-min demo: data → alert → investigation |
| M5 | "A real plant can use it" | asyncua data flows end-to-end |

## Licensing

The Clasp core engine is source-available under the **Business Source License 1.1 (BSL 1.1)**, transitioning to the **Apache License 2.0** on July 1, 2030.

It is completely free to run in non-production, local development, testing, and academic environments. However, any use of the Software in a production plant environment or to provide a commercial service to third parties requires a separate commercial subscription from openyfai (YF). See the `LICENSE` file at the root of the project for full terms and conditions.
