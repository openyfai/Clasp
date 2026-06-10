"""
Git worktree isolation for parallel coding workers.

Creates isolated workspaces under ~/.kronos/worktrees/ so multiple agents
can modify code without colliding on the same working directory.
"""

from __future__ import annotations

import logging
import subprocess
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger("agent.collaboration.worktree")


class WorktreeManager:
    """Manages per-worker git worktrees for code-capable agents."""

    def __init__(self, repo_root: Path, worktrees_root: Optional[Path] = None):
        self.repo_root = Path(repo_root).resolve()
        if worktrees_root:
            self.worktrees_root = Path(worktrees_root).resolve()
        else:
            from clasp.vendor.silex.utils.config import KRONOS_HOME
            self.worktrees_root = KRONOS_HOME / "worktrees"
        self.worktrees_root.mkdir(parents=True, exist_ok=True)

    def create_worktree(
        self,
        agent_id: str,
        branch_prefix: str = "kronos/agent",
        base_ref: str = "HEAD",
    ) -> Path:
        """
        Create an isolated git worktree for a worker agent.
        Returns the absolute path to the worktree directory.
        """
        if not (self.repo_root / ".git").exists() and not (self.repo_root / ".git").is_file():
            # Not a git repo — fall back to ephemeral directory only
            fallback = self.worktrees_root / agent_id / uuid.uuid4().hex[:8]
            fallback.mkdir(parents=True, exist_ok=True)
            log.warning("Repo %s is not a git repository; using ephemeral dir %s", self.repo_root, fallback)
            return fallback

        branch_name = f"{branch_prefix}/{agent_id}/{uuid.uuid4().hex[:8]}"
        dest = self.worktrees_root / agent_id / uuid.uuid4().hex[:8]
        dest.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "git", "worktree", "add",
            "-b", branch_name,
            str(dest),
            base_ref,
        ]
        result = subprocess.run(
            cmd,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")

        log.info("Created worktree %s on branch %s", dest, branch_name)
        return dest.resolve()

    def remove_worktree(self, worktree_path: Path) -> None:
        """Remove a worktree and prune stale references."""
        path = Path(worktree_path).resolve()
        if not path.exists():
            return
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", str(path)],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            log.warning("git worktree remove failed: %s", result.stderr.strip())
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
