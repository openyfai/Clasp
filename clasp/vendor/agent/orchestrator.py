"""
Worker Orchestrator for coordinating isolated agent worker execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from clasp.vendor.agent.compute.isolation_provider import IsolationProvider, SandboxInstance
from clasp.vendor.agent.compute.process import WorkerProcess, WorkerStatus
from clasp.vendor.agent.compute.runtimes.warm_pool import DockerWarmPoolManager
from clasp.vendor.agent.jobs import WorkerJob, WorkerJobResult
from clasp.vendor.agent.telemetry.schema import WorkerEvent, WorkerLifecycle, append_replay_record, emit_worker_event

log = logging.getLogger("agent.orchestrator")


class WorkerHandle:
    """Handle representing a spawned worker container."""

    def __init__(
        self,
        task_id: str,
        sandbox: SandboxInstance,
        execution_task: asyncio.Task,
        process: WorkerProcess,
        job: Optional[WorkerJob] = None,
    ) -> None:
        self.task_id = task_id
        self.sandbox = sandbox
        self.workspace_dir = sandbox.workspace_dir
        self.execution_task = execution_task
        self.process = process
        self.job = job
        self._structured_result: Optional[WorkerJobResult] = None

    async def result(self) -> str:
        """Await completion and return output string."""
        try:
            output = await self.execution_task
            return output
        except asyncio.CancelledError:
            self.process.status = WorkerStatus.KILLED
            return "Error: Worker execution was cancelled."
        except Exception as e:
            self.process.status = WorkerStatus.FAILED
            return f"Error: Worker execution failed: {e}"

    async def structured_result(self) -> WorkerJobResult:
        """Await completion and return structured WorkerJobResult."""
        output = await self.result()
        if self._structured_result is not None:
            return self._structured_result
        exit_match = re.search(r"Exit Code: (-?\d+)", output)
        exit_code = int(exit_match.group(1)) if exit_match else (-1 if "Error:" in output else 0)
        success = self.process.status == WorkerStatus.DONE and exit_code == 0
        job_id = self.job.job_id if self.job else self.task_id
        lineage = [self.job.parent_task_id] if self.job and self.job.parent_task_id else []
        self._structured_result = WorkerJobResult(
            job_id=job_id,
            worker_id=self.task_id,
            success=success,
            exit_code=exit_code,
            output=output,
            artifact_path=self.job.expected_artifact if self.job else None,
            lineage=[x for x in lineage if x],
        )
        return self._structured_result

    async def kill(self) -> None:
        self.process.status = WorkerStatus.KILLED
        if not self.execution_task.done():
            self.execution_task.cancel()
        await self.sandbox.kill()

    def status(self) -> str:
        return self.process.status.value

    @property
    def state(self) -> str:
        return self.process.status.value


class WorkerOrchestrator:
    """Manages lifecycle of isolated agent workers."""

    _instance: Optional["WorkerOrchestrator"] = None

    def __init__(
        self,
        max_workers: int = 4,
        workspace_root: Optional[Any] = None,
        project_root: Optional[Any] = None,
        event_emitter: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        self.max_workers = max_workers
        if workspace_root:
            self.workspace_root = Path(workspace_root)
        else:
            from clasp.vendor.silex.utils.config import KRONOS_WORKSPACE
            self.workspace_root = KRONOS_WORKSPACE
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self._semaphore = asyncio.Semaphore(max_workers)
        self._workers: dict[str, WorkerProcess] = {}
        self._handles: dict[str, WorkerHandle] = {}
        self.provider: Optional[IsolationProvider] = None
        self.event_emitter = event_emitter
        WorkerOrchestrator._instance = self

    async def startup(self) -> None:
        if not self.provider:
            self.provider = DockerWarmPoolManager(
                workspace_root=self.workspace_root,
                project_root=self.project_root,
                pool_size=self.max_workers,
            )

    async def shutdown(self) -> None:
        for handle in list(self._handles.values()):
            try:
                await handle.kill()
            except Exception:
                pass
        self._handles.clear()
        self._workers.clear()
        if self.provider and hasattr(self.provider, "shutdown"):
            await self.provider.shutdown()

    @classmethod
    def instance(cls) -> "WorkerOrchestrator":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_event_emitter(self, emitter: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self.event_emitter = emitter

    def _validate_lease_for_spawn(self, lease: Any, tools: list[str]) -> Optional[str]:
        if lease is None:
            return "Missing actuation lease"
        if not hasattr(lease, "validate_spawn"):
            if not lease.validate(tools[0] if tools else "run_terminal_command"):
                return "Invalid or expired actuation lease"
            return None
        if not lease.validate_spawn(tools):
            return "Invalid or expired actuation lease — tools not permitted"
        return None

    async def spawn_job(self, job: WorkerJob, lease: Any) -> WorkerHandle:
        """
        Spawn a worker from a structured WorkerJob.

        If job.worker_class == 'cognitive_worker', runs a bounded child CognitiveLoop
        instead of a shell sandbox, returning a synthetic WorkerHandle wrapping the result.
        """
        from clasp.vendor.agent.jobs import WorkerClass
        from clasp.vendor.agent.security.lease import ActuationLease

        if not isinstance(lease, ActuationLease):
            lease = ActuationLease.issue(
                task_id=job.job_id,
                agent_id=job.agent_id,
                ttl_seconds=job.timeout_seconds,
                allowed_tools=job.allowed_tools,
                writable_paths=job.writable_paths,
                allowed_domains=job.allowed_domains,
                network_allowed=job.network_allowed,
            )

        if job.worker_class == WorkerClass.COGNITIVE:
            return await self._spawn_cognitive_worker(job, lease)

        if job.workspace_mode == "worktree":
            from clasp.vendor.agent.collaboration.worktree import WorktreeManager

            wt = WorktreeManager(self.project_root)
            worktree_path = wt.create_worktree(job.agent_id)
            job.writable_paths = list(set(job.writable_paths + [str(worktree_path)]))

        return await self.spawn_worker(
            task=job.command,
            tools=job.allowed_tools,
            lease=lease,
            objective=job.objective,
            parent_task_id=job.parent_task_id,
            job=job,
        )

    async def _spawn_cognitive_worker(self, job: WorkerJob, lease: Any) -> "WorkerHandle":
        """
        Runs a bounded CognitiveLoop as the worker and wraps the result in a
        WorkerHandle-compatible object so callers don't need to distinguish.
        """
        from clasp.vendor.agent.subagent import run_cognitive_subagent
        from clasp.vendor.agent.compute.process import WorkerStatus

        task_id = job.job_id
        process = WorkerProcess(task_id=task_id)
        process.status = WorkerStatus.RUNNING
        await emit_worker_event(
            self.event_emitter,
            WorkerEvent(
                worker_id=task_id,
                lifecycle=WorkerLifecycle.RUNNING.value,
                objective=job.objective[:120],
                parent_id=job.parent_task_id,
            ),
        )

        async def _run_child() -> str:
            result = await run_cognitive_subagent(
                objective=job.objective,
                job_id=task_id,
                scoped_tools=job.allowed_tools,
                max_turns=job.max_turns,
                budget_tokens=job.budget_tokens,
                ancestry=job.ancestry,
                max_depth=job.max_depth,
            )
            job.structured_summary = result.summary
            if result.success:
                process.status = WorkerStatus.DONE
                await emit_worker_event(
                    self.event_emitter,
                    WorkerEvent(
                        worker_id=task_id,
                        lifecycle=WorkerLifecycle.DONE.value,
                        objective=job.objective[:120],
                        detail=(result.summary or "")[:120],
                    ),
                )
            else:
                process.status = WorkerStatus.FAILED
                await emit_worker_event(
                    self.event_emitter,
                    WorkerEvent(
                        worker_id=task_id,
                        lifecycle=WorkerLifecycle.FAILED.value,
                        objective=job.objective[:120],
                        detail=(result.error or result.summary or "")[:120],
                    ),
                )
            append_replay_record(task_id, "cognitive_worker_done", {
                "success": result.success,
                "turns": result.turns_used,
                "tokens": result.tokens_used,
            })
            return result.summary or result.error

        task = asyncio.create_task(_run_child())

        # Build a minimal SandboxInstance placeholder
        class _FakeSandbox:
            workspace_dir = str(self.project_root)

        handle = WorkerHandle(
            task_id=task_id,
            sandbox=_FakeSandbox(),  # type: ignore[arg-type]
            execution_task=task,
            process=process,
            job=job,
        )
        self._active_workers[task_id] = handle
        return handle

    async def spawn_worker(
        self,
        task: str,
        tools: list[str],
        lease: Any,
        objective: str = "",
        parent_task_id: Optional[str] = None,
        job: Optional[WorkerJob] = None,
    ) -> WorkerHandle:
        """Spawn an isolated worker to run a task command."""
        if not self.provider:
            await self.startup()

        from clasp.vendor.agent.security.audit_logger import get_audit_logger

        audit = get_audit_logger(self.workspace_root)

        lease_error = self._validate_lease_for_spawn(lease, tools)
        if lease_error:
            audit.log_security_violation("unknown", "LEASE_VIOLATION", lease_error)
            raise PermissionError(lease_error)

        sandbox = await self.provider.provision_sandbox(lease=lease)
        worker_id = sandbox.worker_id
        worker_workspace = sandbox.workspace_dir

        process = WorkerProcess(
            pid=None,
            status=WorkerStatus.PENDING,
            task_id=worker_id,
            sandbox_id=worker_id,
            metadata={
                "task_description": task,
                "tools": tools,
                "objective": objective or task[:120],
                "parent_task_id": parent_task_id,
            },
        )
        self._workers[worker_id] = process

        await emit_worker_event(
            self.event_emitter,
            WorkerEvent(
                worker_id=worker_id,
                lifecycle=WorkerLifecycle.PENDING.value,
                objective=objective or task[:120],
                parent_id=parent_task_id,
            ),
        )

        async def _run_job() -> str:
            async with self._semaphore:
                process.status = WorkerStatus.RUNNING
                await emit_worker_event(
                    self.event_emitter,
                    WorkerEvent(worker_id=worker_id, lifecycle=WorkerLifecycle.RUNNING.value, objective=objective or task[:120]),
                )

                heartbeat_file = worker_workspace / "heartbeat.txt"
                if heartbeat_file.exists():
                    try:
                        heartbeat_file.unlink()
                    except Exception:
                        pass

                wrapped_command = (
                    f"(while true; do date +%s > /workspace/heartbeat.txt; sleep 10; done) & "
                    f"HEARTBEAT_PID=$!; "
                    f"{task}; "
                    f"RC=$?; "
                    f"kill $HEARTBEAT_PID 2>/dev/null; "
                    f"exit $RC"
                )

                stop_monitor = asyncio.Event()

                async def _monitor_heartbeat() -> None:
                    await asyncio.sleep(15)
                    while not stop_monitor.is_set():
                        await asyncio.sleep(5)
                        if not heartbeat_file.exists():
                            continue
                        try:
                            mtime = heartbeat_file.stat().st_mtime
                            if time.time() - mtime > 30.0:
                                log.warning("Worker %s heartbeat timeout (>30s silent). Killing.", worker_id)
                                process.status = WorkerStatus.FAILED
                                await sandbox.kill()
                                break
                        except Exception:
                            pass

                monitor_task = asyncio.create_task(_monitor_heartbeat())
                output = ""
                exit_code = -1

                try:
                    output = await sandbox.execute(wrapped_command, lease)

                    if output.startswith("Security Violation:"):
                        audit.log_security_violation(worker_id, "PATH_GUARDIAN_BLOCK", output)
                        process.status = WorkerStatus.FAILED
                        exit_code = -1
                    else:
                        exit_match = re.search(r"Exit Code: (-?\d+)", output)
                        exit_code = int(exit_match.group(1)) if exit_match else 0
                        audit.log_command(worker_id, task, exit_code)
                        if process.status != WorkerStatus.FAILED:
                            if exit_code != 0:
                                process.status = WorkerStatus.FAILED
                            else:
                                process.status = WorkerStatus.DONE
                except Exception as e:
                    process.status = WorkerStatus.FAILED
                    output = f"Error: Worker execution error: {e}"
                    exit_code = -1
                finally:
                    stop_monitor.set()
                    try:
                        monitor_task.cancel()
                        await monitor_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass
                    if heartbeat_file.exists():
                        try:
                            heartbeat_file.unlink()
                        except Exception:
                            pass

                    output_file = worker_workspace / "output.json"
                    try:
                        output_file.write_text(
                            json.dumps({"task_id": worker_id, "output": output, "exit_code": exit_code}),
                            encoding="utf-8",
                        )
                    except Exception:
                        pass

                    lifecycle = (
                        WorkerLifecycle.DONE.value
                        if process.status == WorkerStatus.DONE
                        else WorkerLifecycle.FAILED.value
                    )
                    await emit_worker_event(
                        self.event_emitter,
                        WorkerEvent(
                            worker_id=worker_id,
                            lifecycle=lifecycle,
                            objective=objective or task[:120],
                            exit_code=exit_code,
                            detail=output[:200],
                        ),
                    )
                    append_replay_record(
                        self.workspace_root,
                        {
                            "worker_id": worker_id,
                            "task": task,
                            "tools": tools,
                            "exit_code": exit_code,
                            "status": process.status.value,
                            "timestamp": time.time(),
                        },
                    )

                    if self.provider and hasattr(self.provider, "teardown_sandbox"):
                        try:
                            await self.provider.teardown_sandbox(sandbox)
                        except Exception as exc:
                            log.warning("Sandbox teardown failed for %s: %s", worker_id, exc)

                    self._handles.pop(worker_id, None)
                    self._workers.pop(worker_id, None)

                return output

        execution_task = asyncio.create_task(_run_job())
        handle = WorkerHandle(
            task_id=worker_id,
            sandbox=sandbox,
            execution_task=execution_task,
            process=process,
            job=job,
        )
        self._handles[worker_id] = handle
        return handle

    async def run_isolated(self, command: str, lease: Any, tool_name: str = "run_terminal_command") -> str:
        """Execute a command in an isolated worker under lease control."""
        if not lease.validate(tool_name):
            from clasp.vendor.agent.security.audit_logger import get_audit_logger

            get_audit_logger(self.workspace_root).log_security_violation(
                "unknown", "LEASE_VIOLATION", f"Tool {tool_name} not permitted"
            )
            return "Command execution rejected: Invalid or expired actuation lease."

        handle = await self.spawn_worker(command, [tool_name], lease)
        return await handle.result()
