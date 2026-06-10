"""
Docker-based container isolation runtime.
"""

import asyncio
from pathlib import Path
from typing import Any, Optional
from clasp.vendor.agent.compute.runtimes.base import BaseRuntime

try:
    import docker
except ImportError:
    docker = None


class DockerRuntime(BaseRuntime):
    """
    Spins up and manages an isolated Docker container (alpine:latest) to execute commands.
    """

    def __init__(self, workspace_dir: Path, project_root: Path) -> None:
        self.workspace_dir = workspace_dir
        self.project_root = project_root
        self.container_id: Optional[str] = None
        self._container: Any = None
        self.client = None
        # Application-level path validation guard
        from clasp.vendor.agent.security.path_guardian import FilesystemPathGuardian
        self._guardian = FilesystemPathGuardian(workspace_dir)
        self._project_guardian = FilesystemPathGuardian(project_root)
        if docker:
            try:
                self.client = docker.from_env()
            except Exception:
                pass

    async def execute(self, command: str, lease: Any) -> str:
        """Executes the command inside a sandboxed Alpine Docker container."""
        # Validate paths before mounting/running container
        try:
            self._guardian.verify_and_canonicalize(self.workspace_dir)
            self._project_guardian.verify_and_canonicalize(self.project_root)
        except PermissionError as e:
            return f"Security Violation: {e}"

        if not self.client:
            return await self._fallback_subprocess(command)

        try:
            # Ensure alpine image is pulled
            try:
                await asyncio.to_thread(self.client.images.get, "alpine:latest")
            except Exception:
                await asyncio.to_thread(self.client.images.pull, "alpine:latest")

            self.workspace_dir.mkdir(parents=True, exist_ok=True)

            # Enforce egress default-deny: if lease.network_allowed is False, disable network access.
            network_disabled = True
            if lease and hasattr(lease, "network_allowed"):
                network_disabled = not lease.network_allowed

            # Replicate the exact same docker parameters as silex/tools/system.py
            # with security hardening (GIT_DIR/GIT_WORK_TREE separation)
            container = await asyncio.to_thread(
                self.client.containers.run,
                image="alpine:latest",
                command=["sh", "-c", command],
                volumes={
                    str(self.project_root.resolve()): {"bind": "/project", "mode": "ro"},
                    str(self.workspace_dir.resolve()): {"bind": "/workspace", "mode": "rw"}
                },
                environment={
                    "GIT_DIR": "/workspace/.git",
                    "GIT_WORK_TREE": "/workspace"
                },
                working_dir="/workspace",
                detach=True,
                remove=True,
                network_disabled=network_disabled,
                mem_limit="256m",
                pids_limit=128,
                labels={
                    "kronos.managed": "true",
                    "kronos.worker_id": str(self.workspace_dir.name),
                },
            )

            self._container = container
            self.container_id = container.id

            # Wait for execution with 60-second limit
            try:
                result = await asyncio.to_thread(container.wait, timeout=60)
            except Exception:
                await self.kill()
                return "Error: Sandboxed command timed out after 60 seconds."

            logs = (await asyncio.to_thread(container.logs)).decode("utf-8", errors="replace")
            exit_code = result.get("StatusCode", 0)

            return f"--- SANDBOX OUTPUT (Alpine Linux) ---\n{logs}\n--- END OUTPUT ---\nExit Code: {exit_code}"

        except Exception as e:
            return f"Error executing sandboxed command: {str(e)}"

    async def kill(self) -> None:
        """Kills the active container."""
        if self._container:
            try:
                await asyncio.to_thread(self._container.kill)
            except Exception:
                pass
            self._container = None
            self.container_id = None

    async def is_alive(self) -> bool:
        """Checks if the container is still running."""
        if not self._container:
            return False
        try:
            # Refresh container status
            await asyncio.to_thread(self._container.reload)
            status = self._container.status
            return status == "running"
        except Exception:
            return False

    async def _fallback_subprocess(self, command: str) -> str:
        """Dev-only fallback when Docker is unavailable."""
        import os
        import subprocess

        allow = os.environ.get("KRONOS_ALLOW_LOCAL_FALLBACK", "").lower() in ("1", "true", "yes", "on")
        allow = allow or os.environ.get("KRONOS_DEV_MODE", "").lower() in ("1", "true", "yes", "on")
        if not allow:
            return (
                "Security Violation: Docker unavailable and local fallback is disabled. "
                "Set KRONOS_ALLOW_LOCAL_FALLBACK=1 for dev mode only."
            )

        try:
            self.workspace_dir.mkdir(parents=True, exist_ok=True)
            result = await asyncio.to_thread(
                subprocess.run,
                ["sh", "-c", command] if os.name != "nt" else ["cmd", "/c", command],
                cwd=str(self.workspace_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = result.stdout + result.stderr
            return f"--- SANDBOX OUTPUT (Local Fallback) ---\n{output}\n--- END OUTPUT ---\nExit Code: {result.returncode}"
        except subprocess.TimeoutExpired:
            return "Error: Local fallback command timed out after 60 seconds."
        except Exception as e:
            return f"Error: Local fallback execution failed: {e}"
