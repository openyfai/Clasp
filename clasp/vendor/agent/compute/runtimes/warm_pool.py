"""
Warm pool manager for Docker containers.
Maintains a pool of pre-initialized containers for low-latency sandboxing.
Containers are claimed per-task and destroyed after completion (replenished async).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from clasp.vendor.agent.compute.isolation_provider import IsolationProvider, SandboxInstance

try:
    import docker
except ImportError:
    docker = None

log = logging.getLogger("agent.warm_pool")

def _local_fallback_allowed() -> bool:
    return os.environ.get("KRONOS_ALLOW_LOCAL_FALLBACK", "").lower() in (
        "1", "true", "yes", "on",
    ) or os.environ.get("KRONOS_DEV_MODE", "").lower() in (
        "1", "true", "yes", "on",
    )


class WarmDockerSandbox(SandboxInstance):
    def __init__(self, client: Any, container: Any, worker_id: str, workspace_dir: Path, project_root: Path):
        self.client = client
        self.container = container
        self._worker_id = worker_id
        self._workspace_dir = workspace_dir
        self.project_root = project_root

        from clasp.vendor.agent.security.path_guardian import FilesystemPathGuardian
        self._guardian = FilesystemPathGuardian(workspace_dir)
        self._project_guardian = FilesystemPathGuardian(project_root)

    @property
    def workspace_dir(self) -> Path:
        return self._workspace_dir

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def execute(self, command: str, lease: Any) -> str:
        try:
            self._guardian.verify_and_canonicalize(self.workspace_dir)
            self._project_guardian.verify_and_canonicalize(self.project_root)
        except PermissionError as e:
            return f"Security Violation: {e}"

        lease.write_egress_policy(self.workspace_dir)

        socket_path = self.workspace_dir / "kronos.sock"
        if socket_path.exists():
            import json
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(str(socket_path)),
                    timeout=2.0,
                )
                payload = {
                    "command": command,
                    "lease_token": lease.to_token(),
                }
                writer.write(json.dumps(payload).encode("utf-8"))
                await writer.drain()
                writer.write_eof()

                response_data = await reader.read()
                response = json.loads(response_data.decode("utf-8"))
                if "error" in response:
                    return f"Security Violation: {response['error']}"
                output = response.get("output", "")
                exit_code = response.get("exit_code", -1)
                return f"--- SANDBOX OUTPUT (Warm UDS) ---\n{output}\n--- END OUTPUT ---\nExit Code: {exit_code}"
            except Exception as e:
                log.debug("UDS connection failed: %s. Falling back to docker exec.", e)

        try:
            exec_result = await asyncio.to_thread(
                self.container.exec_run,
                cmd=["sh", "-c", command],
                workdir="/workspace",
                demux=False,
            )
            output = exec_result.output.decode("utf-8", errors="replace") if exec_result.output else ""
            exit_code = exec_result.exit_code
            return f"--- SANDBOX OUTPUT (Warm Alpine) ---\n{output}\n--- END OUTPUT ---\nExit Code: {exit_code}"
        except Exception as e:
            return f"Error executing command in warm sandbox: {e}"

    async def kill(self) -> None:
        try:
            await asyncio.to_thread(self.container.kill)
        except Exception:
            pass
        try:
            await asyncio.to_thread(self.container.remove, force=True)
        except Exception:
            pass


class LocalFallbackSandbox(SandboxInstance):
    """Dev-only fallback when Docker is unavailable. Not for production."""

    def __init__(self, worker_id: str, workspace_dir: Path, project_root: Path):
        self._worker_id = worker_id
        self._workspace_dir = workspace_dir
        self.project_root = project_root

        from clasp.vendor.agent.security.path_guardian import FilesystemPathGuardian
        self._guardian = FilesystemPathGuardian(workspace_dir)
        self._project_guardian = FilesystemPathGuardian(project_root)

    @property
    def workspace_dir(self) -> Path:
        return self._workspace_dir

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def execute(self, command: str, lease: Any) -> str:
        if not _local_fallback_allowed():
            return (
                "Security Violation: Docker unavailable and local fallback is disabled. "
                "Set KRONOS_ALLOW_LOCAL_FALLBACK=1 for dev mode only."
            )

        try:
            self._guardian.verify_and_canonicalize(self.workspace_dir)
            self._project_guardian.verify_and_canonicalize(self.project_root)
        except PermissionError as e:
            return f"Security Violation: {e}"

        if not lease.validate("run_terminal_command"):
            return "Security Violation: Invalid or expired actuation lease."

        import subprocess

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

    async def kill(self) -> None:
        pass


class DockerWarmPoolManager(IsolationProvider):
    def __init__(self, workspace_root: Path, project_root: Path, pool_size: int = 4):
        self.workspace_root = workspace_root
        self.project_root = project_root
        self.pool_size = pool_size
        self._pool: asyncio.Queue[WarmDockerSandbox] = asyncio.Queue()

        self.client = None
        if docker:
            try:
                self.client = docker.from_env()
            except Exception as e:
                log.warning("Docker not available for Warm Pool: %s", e)

        self._replenish_task: Optional[asyncio.Task] = None

        if self.client:
            try:
                self.client.images.get("python:3.11-alpine")
            except Exception:
                self.client.images.pull("python:3.11-alpine")

            try:
                self.client.networks.get("kronos_sandbox")
            except Exception:
                self.client.networks.create("kronos_sandbox", internal=True)

            try:
                proxy = self.client.containers.get("kronos_egress_proxy")
                if proxy.status != "running":
                    proxy.start()
            except Exception:
                self.client.containers.run(
                    image="python:3.11-alpine",
                    name="kronos_egress_proxy",
                    command=["python", "-u", "/project/agent/security/network_proxy.py"],
                    volumes={
                        str(self.project_root.resolve()): {"bind": "/project", "mode": "ro"},
                        str(self.workspace_root.parent.resolve()): {"bind": "/kronos", "mode": "rw"},
                    },
                    detach=True,
                )
                self.client.networks.get("kronos_sandbox").connect(
                    self.client.containers.get("kronos_egress_proxy")
                )

            self._replenish_task = asyncio.create_task(self._replenish_loop())

    async def _spawn_container(self, network_disabled: bool = True) -> Optional[WarmDockerSandbox]:
        if not self.client:
            return None

        worker_id = f"worker_{uuid.uuid4().hex[:8]}"
        workspace_dir = self.workspace_root / worker_id
        workspace_dir.mkdir(parents=True, exist_ok=True)

        env = {
            "GIT_DIR": "/workspace/.git",
            "GIT_WORK_TREE": "/workspace",
            "WORKSPACE_DIR": "/workspace",
        }
        if not network_disabled:
            env.update({
                "HTTP_PROXY": "http://kronos_egress_proxy:8080",
                "HTTPS_PROXY": "http://kronos_egress_proxy:8080",
                "http_proxy": "http://kronos_egress_proxy:8080",
                "https_proxy": "http://kronos_egress_proxy:8080",
            })

        try:
            container = await asyncio.to_thread(
                self.client.containers.run,
                image="python:3.11-alpine",
                command=["python", "-u", "/project/agent/compute/sidecar_daemon.py"],
                volumes={
                    str(self.project_root.resolve()): {"bind": "/project", "mode": "ro"},
                    str(workspace_dir.resolve()): {"bind": "/workspace", "mode": "rw"},
                },
                environment=env,
                working_dir="/workspace",
                detach=True,
                remove=False,
                mem_limit="256m",
                pids_limit=128,
                labels={
                    "kronos.managed": "true",
                    "kronos.worker_id": worker_id,
                },
                network="kronos_sandbox",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                network_disabled=network_disabled,
            )
            return WarmDockerSandbox(self.client, container, worker_id, workspace_dir, self.project_root)
        except Exception as e:
            log.error("Failed to spawn warm container: %s", e)
            return None

    async def _replenish_loop(self) -> None:
        while True:
            try:
                if self._pool.qsize() < self.pool_size:
                    sandbox = await self._spawn_container(network_disabled=True)
                    if sandbox:
                        await self._pool.put(sandbox)
                        log.debug("Replenished warm pool. Pool size: %d", self._pool.qsize())
                else:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Error in replenish loop: %s", e)
                await asyncio.sleep(5)

    async def provision_sandbox(self, lease: Any = None) -> SandboxInstance:
        if not self.client:
            if not _local_fallback_allowed():
                raise RuntimeError(
                    "Docker unavailable and local fallback is disabled (fail-closed). "
                    "Install Docker or set KRONOS_ALLOW_LOCAL_FALLBACK=1 for dev only."
                )
            log.warning("Docker not available, using LocalFallbackSandbox (dev mode).")
            worker_id = f"worker_{uuid.uuid4().hex[:8]}"
            workspace_dir = self.workspace_root / worker_id
            workspace_dir.mkdir(parents=True, exist_ok=True)
            return LocalFallbackSandbox(worker_id, workspace_dir, self.project_root)

        network_disabled = not (lease and getattr(lease, "network_allowed", False))
        log.debug("Claiming warm sandbox (network_disabled=%s)...", network_disabled)

        try:
            sandbox = self._pool.get_nowait()
        except asyncio.QueueEmpty:
            sandbox = await self._spawn_container(network_disabled=network_disabled)
            if sandbox is None:
                raise RuntimeError("Failed to provision sandbox from warm pool")

        if lease:
            lease.write_egress_policy(sandbox.workspace_dir)
        return sandbox

    async def teardown_sandbox(self, sandbox: SandboxInstance) -> None:
        log.debug("Tearing down sandbox %s...", sandbox.worker_id)
        await sandbox.kill()

    async def shutdown(self) -> None:
        if self._replenish_task:
            self._replenish_task.cancel()
            try:
                await self._replenish_task
            except asyncio.CancelledError:
                pass

        while not self._pool.empty():
            sandbox = self._pool.get_nowait()
            await sandbox.kill()
