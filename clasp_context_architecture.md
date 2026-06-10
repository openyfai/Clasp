# Clasp — Industrial Architecture Context

*This document describes what Clasp builds on top of the Silex/Agent engines.*
*For engine internals, see: `clasp_context_engines.md`*

---

## The Problem Clasp Solves

Modern plants have SCADA + historians (they store everything) but no system that understands **causality**. When yield drops, engineers spend 2 days tracing through 40 correlated variables. Clasp does that in 4 seconds.

### Current Tool Gaps

| Tool | What it does | What it can't do |
|---|---|---|
| SCADA | Real-time control and monitoring | No memory, no causality |
| Historian (OSIsoft PI) | Stores years of time-series data | Just storage — can't answer WHY |
| Traditional ML (C3.ai, Seeq) | Statistical correlations | Correlation ≠ causation, can't trace root causes |
| Human engineers | Know why things happen | Retire, leave, can't scale, take days |

**Clasp fills this gap** by building a living causal graph that gets smarter the longer it runs.

---

## System Architecture (4 Layers)

```
┌─────────────────────────────────────────┐
│           LAYER 4: INTERFACE             │
│   Dashboard  |  Alert API  |  REST API  │
│   "Why did yield drop?" → explanation   │
└──────────────────┬──────────────────────┘
                   │
┌──────────────────▼──────────────────────┐
│           LAYER 3: AGENTS               │
│  WatcherAgent | RootCauseAgent | OptimAgent │
└──────────────────┬──────────────────────┘
                   │ reads/writes
┌──────────────────▼──────────────────────┐
│     LAYER 2: IndustrialSilexEngine      │
│   (wraps KnowledgeGraph + CausalLearner)│
│   Industrial causal graph               │
│   Causal learning loop                  │
│   Confidence scoring + lag timing       │
└──────────────────┬──────────────────────┘
                   │ feeds
┌──────────────────▼──────────────────────┐
│        LAYER 1: DATA INGESTION          │
│   OPC-UA Connector | CSV Adapter        │
│   TEP Simulator (for testing)           │
└──────────────────┬──────────────────────┘
                   │ reads from
┌──────────────────▼──────────────────────┐
│      PHYSICAL PLANT (sensors, PLCs)     │
└─────────────────────────────────────────┘
```

---

## Layer 2: IndustrialSilexEngine (core)

This is the heart of Clasp. It wraps `KnowledgeGraph` and adds industrial intelligence.

### Key Concepts

**Industrial Node Types** (stored as `KnowledgeNode` with custom `node_type`):
- `ProcessVariable` — a measured sensor reading (temperature, pressure, flow)
- `EquipmentUnit` — a physical device (valve CV-14, pump P3, reactor R3)
- `AlarmEvent` — a triggered alarm or fault code
- `OperatorAction` — something a human did (opened valve, changed setpoint)
- `QualityMetric` — product quality measurement (yield, purity, viscosity)

**Industrial Edge Types** (stored as `CausalEdge` with custom `edge_type`):
- `causes_with_lag` — most important; carries `{lag_seconds, confidence, condition, occurrences}` in metadata
- `part_of` — this sensor is on this equipment unit
- `precedes` — temporal ordering, not yet full causal certainty

### Causal Learning Loop

On every sensor reading:
1. **Significance test**: Z-score of new value vs. rolling mean/std. If not significant, skip.
2. **Effect scan**: Find all variables that changed significantly in the next `time_window` seconds.
3. **Evidence accumulation**: Track how many times A → B has been observed with lag L.
4. **Edge creation**: When `occurrences >= min_occurrences` AND `confidence >= min_confidence`, write `causes_with_lag` edge.
5. **Edge reinforcement**: Each new observation of an existing edge increases its strength (capped at 1.0).

**Tunable parameters:**
```python
time_window = 3600       # look for effects within 1 hour
min_occurrences = 5      # need 5+ observations to assert causality
min_confidence = 0.7     # minimum confidence to write a causal edge
significance_zscore = 2.0 # Z-score threshold for "significant change"
```

### Root Cause Analysis (backward BFS)

```
Input: (affected_node_id, event_time)

Algorithm:
  current = affected_node
  for depth in range(max_depth):
    incoming_edges = graph.get_incoming_edges(current, edge_type="causes_with_lag")
    for each edge:
      expected_cause_time = current_time - edge.lag_seconds
      actual_change = time_buffer.get_change_near(edge.source, expected_cause_time, ±5min)
      score = edge.confidence × actual_change.magnitude
    best_cause = argmax(score)
    chain.append(best_cause)
    current = best_cause.node
  return reversed(chain)  # root → problem

Output: ordered list of causal steps (root cause first)
```

This uses `KnowledgeGraph.find_causal_chain()` from Silex as the graph traversal primitive, then enriches it with actual timestamps from the `TimeBuffer`.

---

## Layer 3: Agents

### WatcherAgent — "It warns you before the problem"

**What it does:**
- Runs as an `asyncio` background task
- On startup, pre-loads all causal paths that lead to `QualityMetric` or `AlarmEvent` nodes
- On every new sensor observation, checks if it matches a known precursor pattern
- If pattern is active (multiple precursors co-occurring), fires an alert

**Key property**: The Watcher uses the causal graph's known patterns — it doesn't re-discover, it pattern-matches. This means it gets smarter as the graph grows.

**Alert format:**
```python
{
  "type": "PRECURSOR_DETECTED",
  "outcome_risk": "XMEAS_35 (Product Quality)",
  "pattern": "Cooling water flow drop → Condenser temp rise → Reactor temp rise",
  "estimated_time_to_impact": 2700,  # seconds
  "confidence": 0.87
}
```

### RootCauseAgent — "It explains what happened"

**What it does:**
1. Triggered on demand (`POST /api/investigate`) or automatically on alarm
2. Calls `engine.root_cause_analysis(node, time)`
3. Formats the causal chain for an LLM prompt
4. LLM renders it as a 3-5 sentence plain-language explanation for a plant operator
5. Returns both the structured chain and the explanation

**Output example:**
> "Yield dropped because separator pressure was low. Separator pressure was low because cooling water valve CV-14 partially closed at 14:32. CV-14 closed because an actuator fault triggered a safe-state response. The actuator fault was caused by a power spike on Bus 3 at 14:29."

### OptimizerAgent — "It suggests improvements"

**What it does:**
- Queries the graph for edges from *controllable* inputs to output metrics
- Finds historical combinations with high confidence × magnitude
- Recommends specific parameter changes with predicted outcome

**Output example:**
> "Increasing feed temperature by 3°C and reducing flow rate by 5% is predicted to improve yield by ~4% based on 14 historical observations."

---

## Layer 1: Data Ingestion

### TEP Simulator (for development)

The **Tennessee Eastman Process (TEP)** is the standard industrial AI benchmark:
- 52 variables (41 measured, 12 manipulated)
- 21 known fault types with known root causes
- Public dataset (Harvard Dataverse, free)
- Ground truth = we know exactly what causes what → perfect for validating Clasp

How we use it:
- **Phase 0**: Feed normal data (d00.dat) at 500× speed to pre-build the causal graph
- **Phase testing**: Inject specific fault types and verify root cause detection
- **Demo**: Inject Fault 5, show Watcher alert → Root Cause → explanation

### OPC-UA Connector (for real plants)

OPC-UA is the universal industrial protocol (like HTTP for factories).

Library: `asyncua` (Python async OPC-UA client)

Workflow:
1. Connect to OPC-UA server
2. `discover_nodes()` — walk the address space, register all variables in the Industrial Engine
3. `stream(interval=1.0)` — continuously read values → `engine.observe()`

For testing without real hardware: `asyncua` also ships a simulator you can run locally.

### CSV/Historian Adapter

Most plants have years of data in flat files or OSIsoft PI historians. The CSV adapter:
- Reads any time-indexed CSV
- Maps column names to node IDs via a config file
- Feeds rows into `engine.observe()` in order, with configurable speed multiplier

---

## Layer 4: Interface

### REST API (FastAPI)

```
GET  /api/plant/status     → current readings for all nodes
GET  /api/graph            → full causal graph (D3.js-ready JSON)
GET  /api/alerts           → list of active alerts
POST /api/investigate      → body: {node_id, timestamp} → root cause explanation
WS   /ws/alerts            → real-time alert push stream
```

### Dashboard (React + D3.js)

Three views:
1. **Live View** — grid of plant variables, color-coded by status (normal/warning/alarm). Active alerts at the top with causal summary.
2. **Causal Explorer** — D3.js force-directed graph. Click any node to see its causal neighborhood. Edge thickness = confidence. Edge color = edge type.
3. **Investigation View** — Timeline showing the causal chain: each step with variable name, value, timestamp, confidence.

---

## Testing Strategy

### Phase 0 (no hardware, just TEP data)

```bash
# Step 1: Build causal graph from normal operation
python -m clasp.sim --data data/tep/d00.dat --speed 500 --mode normal
# → After 10 minutes: inspect graph for ≥20 sensible edges

# Step 2: Inject known fault
python -m clasp.sim --data data/tep/d05.dat --speed 1 --fault-type 5
# → Watcher should alert; RootCause should trace to XMV_10

# Step 3: Run automated verification
pytest tests/integration/test_tep_fault5.py
```

### Known TEP Fault Ground Truth (Fault 5)

```
Root cause:  XMV(10) — Condenser cooling water flow step change
  ↓ 45s lag
XMEAS(21) — Condenser cooling water outlet temperature rises
  ↓ 60s lag
XMEAS(9)  — Reactor temperature rises
  ↓ 300s lag
XMEAS(35) — Quality metric drops   ← what the operator sees
```

The Root Cause Agent must trace backward from `XMEAS_35` to `XMV_10`. If it does, the causal engine is working correctly.

---

## Milestones

| # | Name | Definition of Done |
|---|---|---|
| M1 | "It learns" | `graph.stats()` shows ≥20 edges after 10min TEP normal data at 500× |
| M2 | "It explains" | `test_root_cause.py` passes for TEP fault types 1, 4, 5 |
| M3 | "It warns" | Watcher fires alert ≥5 min before quality drop in fault 5 |
| M4 | "Someone can see it" | 2-min screen demo: data → alert → investigation → explanation |
| M5 | "A real plant can use it" | Full stack works with asyncua local OPC-UA server |

---

## The Demo That Closes Customers

1. Open dashboard → live process view
2. "Watch — I'm triggering a fault."
3. Fault injected → Watcher alert appears
4. Click "Investigate" → Root Cause explanation renders
5. "Your engineers would have spent 2 days finding this. We found it in 4 seconds."
