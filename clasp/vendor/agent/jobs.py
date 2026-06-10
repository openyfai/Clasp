"""
Structured worker job model for Kronos orchestration.

Moves delegation from raw shell strings toward typed tasks with explicit
capabilities, artifacts, and lineage metadata.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


class WorkerClass:
    STRUCTURAL = "structural_executor"
    COGNITIVE = "cognitive_worker"


@dataclass
class WorkerJob:
    """A typed unit of work delegated to an isolated worker sandbox."""

    objective: str
    command: str
    allowed_tools: list[str] = field(default_factory=lambda: ["run_terminal_command"])
    writable_paths: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    network_allowed: bool = False
    timeout_seconds: float = 600.0
    expected_artifact: Optional[str] = None
    parent_task_id: Optional[str] = None
    agent_id: str = "kronos_main"
    workspace_mode: str = "ephemeral"  # ephemeral | worktree
    job_id: str = field(default_factory=lambda: f"job_{uuid.uuid4().hex[:10]}")
    # Cognitive sub-agent fields
    worker_class: str = WorkerClass.STRUCTURAL  # structural_executor | cognitive_worker
    max_turns: int = 10
    budget_tokens: int = 50_000
    ancestry: list[str] = field(default_factory=list)
    max_depth: int = 3
    # Private summary for fan-in (cognitive workers only)
    structured_summary: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerJob":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_command(cls, command: str, **kwargs: Any) -> "WorkerJob":
        objective = kwargs.pop("objective", command[:120])
        return cls(objective=objective, command=command, **kwargs)


@dataclass
class WorkerJobResult:
    """Structured result returned from a completed worker job."""

    job_id: str
    worker_id: str
    success: bool
    exit_code: int
    output: str
    artifact_path: Optional[str] = None
    lineage: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)
