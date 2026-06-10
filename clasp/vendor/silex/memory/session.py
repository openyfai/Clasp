"""
Session Manager — ARIA's continuity tracker.

Manages conversation sessions, turn history, and aggregate stats.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from datetime import datetime, timezone

from clasp.vendor.silex.models.schemas import Session, Turn
from clasp.vendor.silex.storage.database import Database
from clasp.vendor.silex.utils.logger import setup_logger

log = setup_logger("aria.session")

current_session_var: ContextVar[Session | None] = ContextVar("current_session_var", default=None)


class SessionManager:
    """Tracks conversation sessions and turn history."""

    def __init__(self, db: Database):
        self.db = db

    @property
    def current(self) -> Session | None:
        return current_session_var.get()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def start_session(self) -> Session:
        """Begin a new conversation session."""
        session = Session()
        await self.db.execute(
            """
            INSERT INTO sessions (id, started_at, turn_count, memories_created,
                                  goals_modified, avg_confidence, topics)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.started_at,
                session.turn_count,
                session.memories_created,
                session.goals_modified,
                session.avg_confidence,
                json.dumps(session.topics),
            ),
        )
        current_session_var.set(session)
        log.info(f"Started session {session.id[:8]}...")
        return session

    async def resume_or_start(self) -> Session:
        """
        Attempt to resume the most recent unclosed session.
        Falls back to creating a new session if none exists.
        This prevents amnesia on server restart.
        """
        row = await self.db.fetch_one(
            "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        )
        if row:
            session = self._row_to_session(row)
            current_session_var.set(session)
            log.info(f"Resumed session {session.id[:8]}... ({session.turn_count} prior turns)")
            return session
        return await self.start_session()

    async def resume_specific(self, session_id: str) -> Session | None:
        """
        Resume a specific session by ID (used when the web client sends its last known session).
        Returns None if the session_id doesn't exist in the DB.
        """
        row = await self.db.fetch_one(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,)
        )
        if row:
            session = self._row_to_session(row)
            # Clear the ended_at so the session is considered active again
            await self.db.execute(
                "UPDATE sessions SET ended_at = NULL WHERE id = ?",
                (session_id,)
            )
            current_session_var.set(session)
            log.info(f"Reconnected to session {session.id[:8]}... ({session.turn_count} prior turns)")
            return session
        return None

    async def end_session(self) -> None:
        """End the current session."""
        session = self.current
        if session is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ?",
            (now, session.id),
        )
        log.info(f"Ended session {session.id[:8]}...")
        current_session_var.set(None)

    # ------------------------------------------------------------------
    # Turn management
    # ------------------------------------------------------------------

    async def record_turn(
        self,
        user_input: str,
        reasoning: str,
        response: str,
        self_reflection: str,
        confidence: float,
        memories_added: int = 0,
        goals_changed: int = 0,
        scratchpad: str | None = None,
        priority_tags: list[str] | None = None,
    ) -> Turn:
        """Record a conversation turn and update session stats."""
        session = self.current
        if session is None:
            raise RuntimeError("No active session. Call start_session() or ensure current_session_var is set first.")

        session.turn_count += 1
        session.memories_created += memories_added
        session.goals_modified += goals_changed

        # Running average of confidence
        n = session.turn_count
        prev_avg = session.avg_confidence
        session.avg_confidence = prev_avg + (confidence - prev_avg) / n

        turn = Turn(
            session_id=session.id,
            turn_number=session.turn_count,
            user_input=user_input,
            reasoning=reasoning,
            response=response,
            self_reflection=self_reflection,
            confidence=confidence,
            scratchpad=scratchpad,
            priority_tags=priority_tags or [],
        )

        # Store the turn
        await self.db.execute(
            """
            INSERT INTO turns (id, session_id, turn_number, user_input,
                               reasoning, response, self_reflection, confidence, scratchpad, priority_tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn.id,
                turn.session_id,
                turn.turn_number,
                turn.user_input,
                turn.reasoning,
                turn.response,
                turn.self_reflection,
                turn.confidence,
                turn.scratchpad,
                json.dumps(turn.priority_tags),
                turn.created_at,
            ),
        )

        # Update session stats
        await self.db.execute(
            """
            UPDATE sessions
            SET turn_count = ?, memories_created = ?, goals_modified = ?, avg_confidence = ?
            WHERE id = ?
            """,
            (
                session.turn_count,
                session.memories_created,
                session.goals_modified,
                session.avg_confidence,
                session.id,
            ),
        )

        # Populate FTS5 index (fails silently if FTS5 is not compiled into SQLite)
        try:
            await self.db.execute(
                "INSERT INTO turns_fts(user_input, response, id) VALUES (?, ?, ?)",
                (turn.user_input, turn.response, turn.id),
            )
        except Exception:
            pass

        return turn

    async def get_recent_turns(self, limit: int = 10) -> list[Turn]:
        """Get the most recent turns from the current session."""
        session = self.current
        if session is None:
            return []

        rows = await self.db.fetch_all(
            """
            SELECT * FROM turns
            WHERE session_id = ?
            ORDER BY turn_number DESC
            LIMIT ?
            """,
            (session.id, limit),
        )

        turns = [self._row_to_turn(r) for r in rows]
        turns.reverse()  # Chronological order
        return turns

    async def compress_turns(self, session_id: str, old_turn_ids: list[str], new_virtual_turn: Turn) -> None:
        """Replace old raw turns with a compressed virtual turn."""
        async with self.db.transaction():
            for turn_id in old_turn_ids:
                await self.db.execute("DELETE FROM turns WHERE id = ?", (turn_id,))
            
            await self.db.execute(
                """
                INSERT INTO turns (id, session_id, turn_number, user_input,
                                   reasoning, response, self_reflection, confidence, scratchpad, priority_tags, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_virtual_turn.id,
                    new_virtual_turn.session_id,
                    new_virtual_turn.turn_number,
                    new_virtual_turn.user_input,
                    new_virtual_turn.reasoning,
                    new_virtual_turn.response,
                    new_virtual_turn.self_reflection,
                    new_virtual_turn.confidence,
                    new_virtual_turn.scratchpad,
                    json.dumps(new_virtual_turn.priority_tags),
                    new_virtual_turn.created_at,
                ),
            )

    async def get_last_reflection(self) -> str | None:
        """Get the self_reflection from the most recent turn in the current session."""
        session = self.current
        if session is None:
            return None
        row = await self.db.fetch_one(
            """
            SELECT self_reflection FROM turns
            WHERE session_id = ?
            ORDER BY turn_number DESC
            LIMIT 1
            """,
            (session.id,),
        )
        if row and row["self_reflection"]:
            return row["self_reflection"]
        return None

    async def get_recent_failures(self, limit: int = 3) -> list[dict]:
        """Fetch the most recent failures from the current session."""
        session = self.current
        if session is None:
            return []
        rows = await self.db.fetch_all(
            """
            SELECT failure_type, description, created_at FROM recent_failures
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session.id, limit),
        )
        return [dict(r) for r in rows]

    async def get_all_sessions(self) -> list[Session]:
        """Get all past sessions."""
        rows = await self.db.fetch_all(
            "SELECT * FROM sessions ORDER BY started_at DESC"
        )
        return [self._row_to_session(r) for r in rows]

    async def get_session(self, session_id: str) -> Session | None:
        """Get a specific session by ID."""
        row = await self.db.fetch_one(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,)
        )
        return self._row_to_session(row) if row else None

    async def get_turns_for_session(self, session_id: str) -> list[Turn]:
        """Get all turns for a specific session."""
        rows = await self.db.fetch_all(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_number ASC",
            (session_id,)
        )
        return [self._row_to_turn(r) for r in rows]

    async def get_total_turns(self) -> int:
        """Get total turns across all sessions."""
        row = await self.db.fetch_one("SELECT COUNT(*) as cnt FROM turns")
        return row["cnt"] if row else 0

    async def get_time_since_last_user_message(self) -> float:
        """Calculate and return the elapsed time in seconds since the last real user message."""
        from datetime import datetime, timezone
        row = await self.db.fetch_one(
            """
            SELECT created_at FROM turns
            WHERE user_input NOT LIKE '[SYSTEM:%'
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        if not row:
            session = self.current
            if session:
                try:
                    started_at = datetime.fromisoformat(session.started_at.replace("Z", "+00:00"))
                except ValueError:
                    started_at = datetime.now(timezone.utc)
                return (datetime.now(timezone.utc) - started_at).total_seconds()
            return 0.0

        try:
            last_time = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        return (datetime.now(timezone.utc) - last_time).total_seconds()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_turn(row: dict) -> Turn:
        try:
            priority_tags = json.loads(row.get("priority_tags", "[]"))
        except Exception:
            priority_tags = []
        return Turn(
            id=row["id"],
            session_id=row["session_id"],
            turn_number=row["turn_number"],
            user_input=row["user_input"],
            reasoning=row["reasoning"],
            response=row["response"],
            self_reflection=row["self_reflection"],
            confidence=row["confidence"],
            scratchpad=row.get("scratchpad"),
            priority_tags=priority_tags,
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_session(row: dict) -> Session:
        return Session(
            id=row["id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            turn_count=row["turn_count"],
            memories_created=row["memories_created"],
            goals_modified=row["goals_modified"],
            avg_confidence=row["avg_confidence"],
            topics=json.loads(row["topics"]),
            memory_summary=row.get("memory_summary"),
        )

    async def update_memory_summary(self, summary: str) -> None:
        """Persist a fresh memory digest for the current session (cache-stable prefix)."""
        session = self.current
        if session is None:
            return
        await self.db.execute(
            "UPDATE sessions SET memory_summary = ? WHERE id = ?",
            (summary, session.id),
        )
        session.memory_summary = summary
        log.debug("Memory summary updated for session %s...", session.id[:8])

    async def search_turns(self, query: str, limit: int = 20) -> list[Turn]:
        """Search across all turns using FTS5, falling back to LIKE if unavailable."""
        try:
            # FTS5: escape each token with double-quotes to handle special chars
            tokens = [f'"{t}"' for t in query.split() if len(t) > 2]
            if not tokens:
                return []
            fts_query = " ".join(tokens)
            rows = await self.db.fetch_all(
                """SELECT t.* FROM turns t
                   WHERE t.id IN (
                       SELECT id FROM turns_fts WHERE turns_fts MATCH ? LIMIT ?
                   )
                   ORDER BY t.turn_number DESC""",
                (fts_query, limit),
            )
            return [self._row_to_turn(r) for r in rows]
        except Exception:
            keywords = [kw.strip().lower() for kw in query.split() if len(kw.strip()) > 2]
            if not keywords:
                return []
            rows = await self.db.fetch_all(
                "SELECT * FROM turns WHERE (LOWER(user_input) LIKE ? OR LOWER(response) LIKE ?) "
                "ORDER BY turn_number DESC LIMIT ?",
                (f"%{keywords[0]}%", f"%{keywords[0]}%", limit),
            )
            return [self._row_to_turn(r) for r in rows]
