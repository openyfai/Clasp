# Clasp — Post-Audit Production Hardening Plan

This document details the step-by-step technical plan to resolve the architectural leaks, reliability bottlenecks, and UI placeholders identified in the **End-to-End Audit Report**. Achieving these milestones transitions Clasp from an MVP to a commercial-grade, production-ready product.

---

## User Review Required

> [!WARNING]
> **Path Isolation:** We will modify the vendored code inside `clasp/vendor/` to clean up hardcoded `~/.kronos` paths. Although these files were originally read-only copies, this edit is critical to prevent Clasp from modifying or corrupting native Kronos installations on the host system.
> 
> **Read-Only Gate:** We will keep the `OPCUAConnector` strictly in read-only mode (`node.read_data_value()`) for safety. Closed-loop writing will remain stubbed and disabled by default until an explicit manual activation process is defined.

---

## Open Questions

> [!IMPORTANT]
> **Q1: Local LLM Configuration**
> For on-premise deployments, factories prefer running a local model (e.g., Qwen 2.5 7B via LM Studio or Ollama) to keep data secure. Should we pre-configure the default settings to search for a local provider at `http://localhost:1234/v1` before trying cloud API keys?
> 
> **Q2: Historical Database Pruning**
> Ingestion of high-frequency data (every second) can grow the SQLite database by gigabytes per month. Do you want to include a automatic rolling garbage-collector that deletes sensor records older than 14 days by default?

---

## Proposed Changes

### 1. Vendored Core Path Correction (Critical)

#### [MODIFY] [database.py](file:///E:/Clasp/clasp/vendor/silex/storage/database.py)
* Replace any hardcoded `Path.home() / ".kronos"` string literals with `config.CLASP_HOME`.
* Ensure lock files (like `db_write.lock`) are created inside the `~/.clasp/` runtime directory.

#### [MODIFY] [audit_logger.py](file:///E:/Clasp/clasp/vendor/agent/security/audit_logger.py)
* Redirect audit logging paths from `~/.kronos/workspace/` to `~/.clasp/workspace/`.

#### [MODIFY] [orchestrator.py](file:///E:/Clasp/clasp/vendor/agent/orchestrator.py)
* Ensure dynamic worker sandboxes and temp script executions are written to subfolders under `~/.clasp/`.

---

### 2. OPC-UA Subscription & Reconnection (High)

#### [MODIFY] [opcua_connector.py](file:///E:/Clasp/clasp/industrial/ingest/opcua_connector.py)
* Refactor the data ingestion loop:
  * Remove the polling `while True: await node.read_data_value()` loop.
  * Implement native OPC-UA push-based subscriptions via `client.create_subscription()`.
  * Define a `SubscriptionHandler` class to handle incoming telemetry updates and push them directly to Clasp's time-buffer queue.
* Implement a resilient network monitoring loop:
  * Catch network disconnect exceptions (`ConnectionError`, socket timeouts).
  * Run an exponential backoff reconnect handler (reconnecting at 2s, 4s, 8s, up to 30s limits) until the connection is restored.
  * Broadcast "Connection Offline / Online" logs to the system log stream.

---

### 3. Settings & Authentication API (Medium)

#### [MODIFY] [routes.py](file:///E:/Clasp/clasp/industrial/api/routes.py)
* Add a `GET /api/settings` route to read parameter mappings (confidence scores, sliding windows, active provider configurations) from `~/.clasp/settings.json`.
* Add a `POST /api/settings` route to validate and write edits back to the json file.
* Add a `GET /api/auth/me` endpoint returning mock process operator credentials (matching Windows AD parameters in production).

---

### 4. React UI Completion (Medium / Low)

#### [MODIFY] [App.jsx](file:///E:/Clasp/dashboard/src/App.jsx)
* Add tab selection state handling to conditional render the new views.
* Add array slicing inside `addLog` to truncate dashboard logs when they exceed 200 items.

#### [NEW] [SettingsView.jsx](file:///E:/Clasp/dashboard/src/components/SettingsView.jsx)
* Form view mapped to `/api/settings` to edit system confidence thresholds, data paths, and LLM configuration keys.

#### [NEW] [AnalysisView.jsx](file:///E:/Clasp/dashboard/src/components/AnalysisView.jsx)
* A page showing historical analytics, listing the frequency of past anomalies and sorting them by their identified root cause.

---

## Verification Plan

### Automated Tests
* Run full verification tests:
  ```bash
  pytest tests/test_phase0_imports.py -v
  ```
* Run a mock connection test to simulate sudden OPC-UA client connection drops:
  ```bash
  python tests/test_opcua_reconnects.py
  ```

### Manual Verification
1. **Zero-Kronos Check:** Delete the `~/.kronos` folder entirely on your development machine. Run `clasp start` and verify that the system generates a `~/.clasp` directory instead, with zero files written to `.kronos`.
2. **Dashboard Verification:** Click through the new tabs ("Settings", "Analysis"). Change a parameter in the Settings panel, hit Save, and verify the backend updates `~/.clasp/settings.json` correctly.
3. **Log Truncation:** Keep the dashboard running in simulation mode for 5 minutes. Verify that the browser memory footprint remains stable and the console logs truncate correctly.
