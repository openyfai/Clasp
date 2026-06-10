"""
Orchestration telemetry event schema for Ink UI and audit surfaces.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional


class WorkerLifecycle(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    KILLED = "killed"


@dataclass
class WorkerEvent:
    worker_id: str
    lifecycle: str
    objective: str = ""
    parent_id: Optional[str] = None
    ancestry_chain: list[str] = field(default_factory=list)
    detail: str = ""
    exit_code: Optional[int] = None
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")

    def to_ink_message(self) -> dict[str, Any]:
        return {
            "type": "worker",
            "data": asdict(self),
        }


async def emit_worker_event(
    emitter: Optional[Callable[[dict[str, Any]], Awaitable[None]]],
    event: WorkerEvent,
) -> None:
    """Emit a worker lifecycle event to Ink bridge or other async emitter."""
    if emitter is None:
        return
    await emitter(event.to_ink_message())


def append_replay_record(workspace_root: Path, record: dict[str, Any]) -> None:
    """Append a deterministic replay record for orchestration auditing."""
    replay_file = workspace_root.parent / "orchestration_replay.ndjson"
    replay_file.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(replay_file, "a", encoding="utf-8") as f:
        f.write(line)
