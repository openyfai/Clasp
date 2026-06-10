"""
Abstract Base class for all worker isolation runtimes.
"""

from abc import ABC, abstractmethod
from typing import Any

class BaseRuntime(ABC):
    """
    Abstract interface for executing processes inside an isolated runtime
    (e.g. Docker container, secure local subprocess, etc.).
    """

    @abstractmethod
    async def execute(self, command: str, lease: Any) -> str:
        """Execute a command inside the runtime sandbox under a given lease."""
        pass

    @abstractmethod
    async def kill(self) -> None:
        """Terminate the active runtime sandbox."""
        pass

    @abstractmethod
    async def is_alive(self) -> bool:
        """Check if the isolated sandbox environment is still running."""
        pass
