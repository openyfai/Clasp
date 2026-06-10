"""
Audit Logger for Kronos Sandboxes.
Records execution commands, security events, and system anomalies into a local SQLite database.
"""

import sqlite3
import datetime
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

log = logging.getLogger("agent.security.audit")

class AuditLogger:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    worker_id TEXT,
                    details TEXT
                )
            """)
            conn.commit()

    def log_event(self, event_type: str, worker_id: Optional[str], details: Dict[str, Any]):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO audit_events (timestamp, event_type, worker_id, details) VALUES (?, ?, ?, ?)",
                    (
                        datetime.datetime.utcnow().isoformat(),
                        event_type,
                        worker_id,
                        json.dumps(details)
                    )
                )
                conn.commit()
        except Exception as e:
            log.error(f"Failed to write to audit log: {e}")

    def log_command(self, worker_id: str, command: str, exit_code: int):
        self.log_event("COMMAND_EXECUTION", worker_id, {
            "command": command,
            "exit_code": exit_code
        })

    def log_security_violation(self, worker_id: str, violation_type: str, message: str):
        self.log_event("SECURITY_VIOLATION", worker_id, {
            "violation_type": violation_type,
            "message": message
        })

    def log_network_egress(self, worker_id: Optional[str], target_host: str, allowed: bool):
        self.log_event("NETWORK_EGRESS", worker_id, {
            "target_host": target_host,
            "allowed": allowed
        })

# Global singleton
_global_logger: Optional[AuditLogger] = None

def get_audit_logger(workspace_root: Optional[Path] = None) -> AuditLogger:
    global _global_logger
    if _global_logger is None:
        if workspace_root is None:
            from clasp.vendor.silex.utils.config import KRONOS_WORKSPACE
            workspace_root = KRONOS_WORKSPACE
        db_path = workspace_root.parent / "audit.db"
        _global_logger = AuditLogger(db_path)
    return _global_logger
