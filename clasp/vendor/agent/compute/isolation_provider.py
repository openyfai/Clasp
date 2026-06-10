"""
Isolation Provider interface for autonomous agent orchestration.
This abstraction separates the orchestrator logic from the underlying execution backend.
"""

from abc import ABC, abstractmethod
from typing import Any
from pathlib import Path


class SandboxInstance(ABC):
    """Represents a single, active isolated environment (e.g., a specific container)."""
    
    @property
    @abstractmethod
    def workspace_dir(self) -> Path:
        """The host path to the workspace directory mounted into this sandbox."""
        pass

    @property
    @abstractmethod
    def worker_id(self) -> str:
        """The unique identifier for this sandbox."""
        pass
    
    @abstractmethod
    async def execute(self, command: str, lease: Any) -> str:
        """Executes a command inside this sandbox."""
        pass

    @abstractmethod
    async def kill(self) -> None:
        """Terminates this sandbox."""
        pass


class IsolationProvider(ABC):
    """
    Manages the lifecycle of sandboxes. This could be a simple factory (spawning 
    containers on demand) or a warm pool manager (claiming pre-initialized containers).
    """

    @abstractmethod
    async def provision_sandbox(self) -> SandboxInstance:
        """Claims or provisions a new isolated environment for execution."""
        pass

    @abstractmethod
    async def teardown_sandbox(self, sandbox: SandboxInstance) -> None:
        """Releases or destroys the sandbox after the task is complete."""
        pass
