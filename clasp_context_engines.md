# Clasp — Engine Context: What We Export & How to Use It

*This document is the authoritative reference for how Clasp uses the Silex and Agent engines from Kronos.*
*Source project: `e:\AGI` (Kronos / openyfai-kronos v1.0.0)*

---

## 1. The Silex Engine (silex/)

Silex is Kronos's cognitive backbone. For Clasp, we vendor-copy it and use only the lower layers.

### What Silex Is

Silex is **not** an LLM wrapper. It is a **persistent causal knowledge system**:

- `Database` — async SQLite engine (aiosqlite). All persistent state lives here.
- `KnowledgeGraph` — NetworkX-backed in-memory graph, SQLite-persisted. Nodes = facts/entities. Edges = typed causal relationships.
- `MemoryStore` — Episodic/semantic memory CRUD with vector search (ChromaDB).
- `LLM layer` — Multi-provider abstraction (Gemini, OpenAI-compat, Ollama, LM Studio).

### Files We Use in Clasp

| File | Class / Function | Purpose in Clasp |
|---|---|---|
| `silex/storage/database.py` | `Database` | Async SQLite pool. ALL SQLite goes through this. |
| `silex/world/graph.py` | `KnowledgeGraph` | Core causal graph. We extend its node/edge types. |
| `silex/models/schemas.py` | `KnowledgeNode`, `CausalEdge`, `NodeType`, `EdgeType` | Base Pydantic schemas. We extend, never modify. |
| `silex/memory/memory_store.py` | `MemoryStore` | Used by agents to store investigation results as memories. |
| `silex/llm/factory.py` | `build_provider()` | Creates the LLM client used by Root Cause Agent for narration. |
| `silex/llm/registry.py` | `get_provider_profile()` | LLM provider configuration. |
| `silex/utils/config.py` | `KRONOS_HOME`, `get_settings_store()` | We replace `KRONOS_HOME` with `CLASP_HOME` in our config. |

### KnowledgeGraph API (key methods for Clasp)

```python
from vendor.silex.storage.database import Database
from vendor.silex.world.graph import KnowledgeGraph

db = Database(path="~/.clasp/clasp.db")
await db.initialize()

graph = KnowledgeGraph(db)
await graph.load()  # or graph.load_relevant(query) for fast startup

# Add a node
node = KnowledgeNode(
    content="Reactor R3 temperature",
    node_type="ProcessVariable",   # our custom type
    confidence=1.0,
    source="opcua_connector"
)
await graph.add_node(node)

# Add a causal edge
edge = CausalEdge(
    source_node=cause_node_id,
    target_node=effect_node_id,
    edge_type="causes_with_lag",   # our custom edge type
    strength=0.85,
    evidence="Observed 12 times with 45s average lag"
)
await graph.add_edge(edge)

# Backward trace from a problem node
chain = await graph.find_causal_chain(root_id, problem_id)

# Get causal neighborhood for a node
neighborhood = await graph.get_neighborhood(node_id, depth=2)

# Retrieve relevant context for a query
context = await graph.retrieve_relevant_context("yield drop", max_nodes=15)
```

### Database Schema (tables already defined in Silex)

Silex creates these tables automatically via `Database.initialize()`:
- `knowledge_nodes` — nodes with `id, content, node_type, confidence, source, metadata`
- `causal_edges` — edges with `id, source_node, target_node, edge_type, strength, evidence`
- `memories` — episodic/semantic memory store
- `sessions` — session continuity

For Clasp we add our own tables:
- `sensor_observations` — raw time-series buffer (persisted for replay/debugging)
- `causal_patterns` — discovered patterns with lag/confidence
- `alerts` — generated alerts history

### NodeType & EdgeType Extension Strategy

Silex's `NodeType` and `EdgeType` are Python `str Enum` classes. We extend them in Clasp's schemas without modifying the vendored code:

```python
# clasp/industrial/schemas.py
from vendor.silex.models.schemas import NodeType, EdgeType

class IndustrialNodeType(str, Enum):
    """Industrial-specific node types, used alongside Silex's NodeType."""
    PROCESS_VARIABLE = "ProcessVariable"
    EQUIPMENT_UNIT   = "EquipmentUnit"
    ALARM_EVENT      = "AlarmEvent"
    OPERATOR_ACTION  = "OperatorAction"
    QUALITY_METRIC   = "QualityMetric"

class IndustrialEdgeType(str, Enum):
    CAUSES_WITH_LAG = "causes_with_lag"   # lag_seconds + confidence in metadata
    PART_OF         = "part_of"
    PRECEDES        = "precedes"          # temporal, not yet fully causal
```

---

## 2. The Agent Orchestration Engine (agent/)

### What the Agent Module Is

The `agent/` package provides **isolated, secure worker execution** for Kronos. For Clasp, we use:

| File | Class | Purpose |
|---|---|---|
| `agent/orchestrator.py` | `WorkerOrchestrator` | Manages lifecycle of parallel isolated workers |
| `agent/subagent.py` | `run_cognitive_subagent()` | Runs a bounded cognitive child loop |
| `agent/jobs.py` | `WorkerJob`, `WorkerJobResult` | Job definition schema |
| `agent/security/lease.py` | `ActuationLease` | Permission-scoped execution token |

### WorkerOrchestrator in Clasp

The orchestrator is used for running the **Watcher Agent** and **Root Cause Agent** as isolated background workers:

```python
from vendor.agent.orchestrator import WorkerOrchestrator
from vendor.agent.jobs import WorkerJob, WorkerClass

orchestrator = WorkerOrchestrator(
    max_workers=4,
    workspace_root="~/.clasp/workspace",
    project_root=Path(".")
)
await orchestrator.startup()

# Spawn a cognitive worker (runs bounded reasoning loop)
job = WorkerJob(
    objective="Investigate yield drop at timestamp 1000",
    worker_class=WorkerClass.COGNITIVE,
    allowed_tools=["read_graph", "query_memory"],
    timeout_seconds=120,
    max_turns=10,
)
handle = await orchestrator.spawn_job(job, lease)
result = await handle.structured_result()
```

### ActuationLease

Every worker execution requires an `ActuationLease` — a time-limited, tool-scoped permission token:

```python
from vendor.agent.security.lease import ActuationLease

lease = ActuationLease.issue(
    task_id="investigate-001",
    agent_id="root_cause_agent",
    ttl_seconds=120,
    allowed_tools=["read_graph", "llm_complete"],
    writable_paths=["~/.clasp/workspace"],
    network_allowed=False,
)
```

---

## 3. LLM Layer (for Root Cause Agent narration)

Silex's LLM layer abstracts over Gemini, OpenAI-compat, Ollama, and LM Studio.

```python
from vendor.silex.llm.factory import build_provider

# The provider is configured via .clasp/settings.json / secrets.json
provider = build_provider(provider_id="gemini", model="gemini-2.0-flash")

# Simple completion
response = await provider.complete(prompt)

# Structured output (Pydantic model)
result = await provider.complete_structured(prompt, output_schema=MyModel)
```

The multi-provider registry supports the same providers as Kronos — so if you already have your LM Studio or Gemini key configured in Kronos, the same key works in Clasp (we copy the config format, not the secrets).

---

## 4. What We DON'T Use from Silex

| Silex Module | Why We Skip It |
|---|---|
| `silex/core/cognitive_loop.py` | Kronos's conversation loop — not needed for industrial agents |
| `silex/ui/` | Kronos's CLI/TUI onboarding — Clasp has its own API/dashboard |
| `silex/tools/` | Kronos's tool registry (web, browser, terminal) — Clasp defines its own ingestion tools |
| `silex/autonomy/` | Kronos's autonomy policies — Clasp has simpler, domain-specific agent policies |
| `silex/evolution/` | Self-improvement loop — not applicable to Clasp v1 |
| `silex/voice/` | Voice I/O — not applicable |
| `silex/adapters/telegram` / `discord` | Kronos-specific integrations |

---

## 5. Data & Config Paths

Kronos uses `~/.kronos/` as its home. **Clasp uses `~/.clasp/`** — completely separate:

```
~/.clasp/
├── clasp.db              ← SQLite (same schema as silex.db, separate instance)
├── settings.json         ← LLM provider config, thresholds
├── secrets.json          ← API keys (Gemini, etc.)
├── workspace/            ← worker isolation scratch
└── logs/
```

Config constants go in `clasp/config.py` (mirrors `silex/utils/config.py` pattern):

```python
# clasp/config.py
CLASP_HOME = Path.home() / ".clasp"
CLASP_DB   = CLASP_HOME / "clasp.db"
CLASP_SETTINGS = CLASP_HOME / "settings.json"
CLASP_SECRETS  = CLASP_HOME / "secrets.json"
CLASP_WORKSPACE = CLASP_HOME / "workspace"
```
