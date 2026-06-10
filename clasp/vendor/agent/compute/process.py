"""
Data model representing an active worker process / container.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any

class WorkerStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    KILLED = "killed"

@dataclass
class WorkerProcess:
    """Tracks the state and metadata of an active worker sandbox."""
    pid: Optional[int]
    status: WorkerStatus
    task_id: str
    sandbox_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
