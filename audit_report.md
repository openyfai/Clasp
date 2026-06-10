# Clasp Codebase End-to-End Audit Report

## 1. Architectural Integrity & Vendoring
**Status: 🟡 Needs Attention**
* **File Structure:** The vendored Silex and Agent directories remain correctly isolated within `clasp/vendor/`.
* **Config Patching:** The `export_engines.py` script successfully patched `KRONOS_HOME` inside `clasp/vendor/silex/utils/config.py`. **However**, a global codebase scan reveals that `~/.kronos` is hardcoded across multiple files in the vendored directories, bypassing the config patch. For instance, `clasp/vendor/silex/storage/database.py` hardcodes `~/.kronos/runtime/db_write.lock`, and `clasp/vendor/agent/security/audit_logger.py` uses `Path.home() / ".kronos" / "workspace"`. This defeats the environment patching and risks cross-contamination with a host's native Kronos/AGI installation.
* **Ontology & Schemas:** The industrial schemas (`schemas.py`) properly extend the core Silex taxonomy via `metadata` properties (e.g., mapping `IndustrialNodeType.PROCESS_VARIABLE` onto Silex's base `entity` node type). This cleanly enables custom domain logic without breaking core query engines.

## 2. Safety, Sandboxing & Actuation Leases
**Status: 🟢 Pass (By Omission)**
* **Actuation Security:** The `OptimizerAgent` only traverses the causal graph to output textual recommendations and does not invoke any tool or physical command.
* **Operator Verification:** The `OPCUAConnector` only utilizes `node.read_data_value()`. There is zero code in the codebase for `node.write_value()`. Thus, an LLM hallucination literally cannot bypass physical bounds, because the application is fundamentally read-only (air-gapped) from the perspective of the plant. A human operator must manually execute the optimizer recommendations via their separate DCS.
* **Isolation:** The agent logic does not have access to OS-level terminal tools. 

## 3. Data Ingestion & Concurrency
**Status: 🟡 Needs Attention**
* **OPC-UA Pipeline:** The `asyncua` integration uses a standard polling `while True:` loop inside `opcua_connector.py`. While it catches read exceptions and avoids crashing, it:
  1. Relies on polling instead of an OPC-UA `DataChange` subscription (inefficient for high frequency).
  2. Lacks a robust automated `reconnect` logic loop if the underlying socket connection drops completely.
* **Database Contention:** Excellent. `database.py` natively utilizes `PRAGMA journal_mode=WAL` with a 15,000ms `busy_timeout` and an `asyncio.Queue` background worker. This effectively decouples high-frequency telemetry insertion from read query contention.

## 4. UI/UX & Design Alignment
**Status: 🟢 Pass**
* **Design Spec:** `index.css` meticulously implements the requested aesthetic: a full `#000000` main background, `#3f3f3f` cards, and high-contrast alert colors (cyan, red, amber, green). Typography leverages standard system fonts including `SF Pro Display`.
* **Dynamic Features:** The clock is dynamically hooked to a `setInterval`, the tab system is a lightweight SPA using React state (`activeTab`), and the user profile pulls from the mock `auth/me` backend route successfully.
* **CORS & Proxying:** The Vite configuration implements a local development proxy mapping `/api` to `http://localhost:8000`, bypassing cross-origin restrictions cleanly and mimicking production behavior.

## 5. Robustness & Test Coverage
**Status: 🟢 Pass**
* **Smoke & Unit Tests:** The `pytest` suite correctly covers the causal time-buffer buffering and phase integrations using randomly allocated local OPC-UA test ports to prevent collisions.
* **Failure Modes (LLM Outage):** Graceful degradation is correctly implemented. `RootCauseAgent.investigate()` wraps the `llm.complete_json()` call in a broad `try/except` block. If the LLM provider drops offline, the system safely catches the exception and returns the deterministic causal trace accompanied by a static fallback string (`"Error generating explanation..."`). Ingestion and background Watcher loops run purely deterministically, free from LLM dependency.

---

## Prioritized Action Plan

### 🔴 CRITICAL (Showstoppers)
- [ ] **Hardcoded Paths Breach:** Fix the hardcoded `~/.kronos` references scattered across the vendored codebase (e.g., `silex/storage/database.py`, `agent/security/lease.py`, `agent/orchestrator.py`). These must dynamically reference `KRONOS_HOME` or `CLASP_HOME` to prevent corrupting external systems.

### 🟡 HIGH (Pre-Release)
- [ ] **OPC-UA Subscriptions & Reconnects:** Refactor the polling loop in `OPCUAConnector.stream()` to use an OPC-UA `create_subscription()` for push-based telemetry. Add a dedicated connection monitor that will `await client.connect()` upon a `ConnectionError` or socket timeout.

### 🔵 MEDIUM (Technical Debt)
- [ ] **Actuation Architecture:** While read-only mode is safe, if closed-loop control is desired in the future, the `OPCUAConnector` will need a `write_value` method tightly coupled with a cryptographically verifiable human-in-the-loop approval endpoint.
- [ ] **Missing Analysis/Settings UI:** The SPA router has placeholder views for "Analysis" and "Settings". These need actual React components mapped to historical DB queries and `~/.clasp/settings.json` editing.

### 🟢 LOW (Polishing)
- [ ] **Log Retention:** Implement automatic rotation or truncation for `AgentLog` React state array to prevent out-of-memory errors on the dashboard after extended runtimes.
- [ ] **Mock LLM Warning:** Provide a visual UI toast notification if the LLM provider degrades to the mock fallback.
