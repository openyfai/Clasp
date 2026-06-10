"""
Memory Store - ARIA's persistent knowledge.

Handles storing, retrieving, searching, and managing memories in SQLite.
The retrieval strategy uses three pools: recency, importance, and relevance.

Polish additions:
  - Duplicate detection before storing
  - Memory deletion (forget)
  - Memory search command
  - Manual memory injection
  - Importance decay over time
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from math import exp

from clasp.vendor.silex.memory.admission_control import AdmissionController
from clasp.vendor.silex.security.guard_middleware import MemoryGuardMiddleware
from clasp.vendor.silex.models.schemas import Memory, MemorySource, MemoryType
from clasp.vendor.silex.storage.database import Database
from clasp.vendor.silex.utils.config import (
    MAX_IMPORTANT_MEMORIES,
    MAX_RECENT_MEMORIES,
    MAX_RELEVANT_MEMORIES,
)
from clasp.vendor.silex.memory.vector_store import VectorStore
import hashlib
from clasp.vendor.silex.utils.logger import setup_logger

log = setup_logger("silex.memory")


class MemoryStore:
    """SQLite-backed persistent memory for ARIA."""

    def __init__(self, db: Database):
        self.db = db
        self.vs = VectorStore(collection_name="aria_memories")
        self.amac = AdmissionController()
        self.guard = MemoryGuardMiddleware()
        self._fts5_available: bool | None = None  # lazily probed on first search

    async def _check_fts5(self) -> bool:
        """Return True if the memories_fts FTS5 virtual table is available."""
        if self._fts5_available is None:
            try:
                await self.db.fetch_one("SELECT count(*) FROM memories_fts LIMIT 0")
                self._fts5_available = True
            except Exception:
                self._fts5_available = False
        return self._fts5_available

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def add(self, memory: Memory) -> Memory | None:
        """Store a new memory (with duplicate detection and A-MAC gating)."""
        # Check for duplicates - skip if a very similar memory exists
        if await self._is_duplicate(memory.content):
            log.debug(f"Skipped duplicate memory: {memory.content[:40]}...")
            return None

        # A-MAC Evaluation
        async def novelty_checker(cand: str) -> float:
            if self.vs.is_active:
                results = await asyncio.to_thread(self.vs.search, cand, 1)
                if results and "distance" in results[0]:
                    return results[0]["distance"]
            return 1.0

        content_type = memory.memory_type.value if isinstance(memory.memory_type, MemoryType) else memory.memory_type
        
        prov_dict = memory.provenance if isinstance(memory.provenance, dict) else {}
        source_context = prov_dict.get("context", "")
        session_id = prov_dict.get("session_id", None)
        user_id = prov_dict.get("user_id", "default")
        
        guard_result = self.guard.validate_write_attempt(memory.id, memory.content)
        if not guard_result["allowed"]:
            log.warning(f"MemoryGuard rejected memory write for {memory.id}")
            return None
            
        if guard_result["flagged"]:
            memory.confidence *= 0.5
            memory.importance *= 0.5
            
        prov_dict["hmac_signature"] = guard_result.get("signature")
        memory.provenance = prov_dict
        
        amac_result = await self.amac.evaluate_admission(
            memory.content,
            content_type,
            source_context,
            novelty_checker
        )
        
        if not amac_result["admitted"]:
            log.info(f"Memory rejected by A-MAC (Score: {amac_result['composite_score']:.2f}): {memory.content[:40]}...")
            return None

        integrity_hash = hashlib.sha256(f"{memory.id}|{memory.content}|{amac_result['composite_score']}".encode()).hexdigest()
        
        mapped_type = "fact"
        if content_type in ("preference", "normative", "character"):
            mapped_type = "preference"
        elif content_type in ("plan", "project"):
            mapped_type = "plan"
        elif content_type == "transient":
            mapped_type = "transient"

        # Write to both tables
        await self.db.execute(
            """
            INSERT INTO memories (id, content, source, memory_type, importance,
                                  confidence, created_at, last_accessed,
                                  access_count, tags, level, child_memory_ids, provenance_json,
                                  related_memories, archived_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.id,
                memory.content,
                memory.source.value if isinstance(memory.source, MemorySource) else memory.source,
                content_type,
                memory.importance,
                memory.confidence,
                memory.created_at,
                memory.last_accessed,
                memory.access_count,
                json.dumps(memory.tags),
                memory.level,
                json.dumps(memory.child_memory_ids),
                json.dumps(memory.provenance),
                json.dumps(memory.related_memories),
                memory.archived_at,
            ),
        )
        
        await self.db.execute(
            """
            INSERT INTO admitted_memories (
                memory_id, user_id, session_id, content, content_type,
                utility_score, confidence_score, novelty_score, recency_score, type_prior,
                composite_score, admitted_at, integrity_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.id,
                user_id,
                session_id,
                memory.content,
                mapped_type,
                amac_result["utility"],
                amac_result["confidence"],
                amac_result["novelty"],
                amac_result["recency"],
                amac_result["type_prior"],
                amac_result["composite_score"],
                datetime.now(timezone.utc).timestamp(),
                integrity_hash
            )
        )

        if self.vs.is_active:
            await asyncio.to_thread(self.vs.add_chunks, [memory.content], [{"type": content_type, "timestamp": datetime.now(timezone.utc).timestamp()}], ids=[memory.id])

        # Populate FTS5 index (silently skipped if FTS5 not compiled into SQLite)
        try:
            if await self._check_fts5():
                await self.db.execute(
                    "INSERT INTO memories_fts(content, id) VALUES (?, ?)",
                    (memory.content, memory.id),
                )
        except Exception:
            self._fts5_available = False

        log.debug(f"Stored memory (A-MAC {amac_result['composite_score']:.2f}): {memory.content[:60]}...")
        return memory

    async def get(self, memory_id: str) -> Memory | None:
        """Retrieve a single memory by ID."""
        row = await self.db.fetch_one(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        )
        if row is None:
            return None
        return self._row_to_memory(row)

    async def get_by_index(self, index: int) -> Memory | None:
        """Retrieve a memory by its display index (1-based, sorted by importance)."""
        rows = await self.db.fetch_all(
            "SELECT * FROM memories ORDER BY importance DESC"
        )
        if 1 <= index <= len(rows):
            return self._row_to_memory(rows[index - 1])
        return None

    async def delete(self, memory_id: str) -> bool:
        """Delete a memory by ID."""
        row = await self.db.fetch_one(
            "SELECT content FROM memories WHERE id = ?", (memory_id,)
        )
        if row is None:
            return False
        await self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

        if self.vs.is_active:
            await asyncio.to_thread(self.vs.delete_by_ids, [memory_id])

        try:
            if await self._check_fts5():
                await self.db.execute(
                    "DELETE FROM memories_fts WHERE id = ?", (memory_id,)
                )
        except Exception:
            pass

        log.info(f"Deleted memory: {row['content'][:40]}...")
        return True

    async def delete_by_index(self, index: int) -> bool:
        """Delete a memory by its display index (1-based)."""
        memory = await self.get_by_index(index)
        if memory:
            return await self.delete(memory.id)
        return False

    async def update_access(self, memory_id: str) -> None:
        """Mark a memory as accessed (updates timestamp and counter)."""
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """
            UPDATE memories
            SET last_accessed = ?, access_count = access_count + 1
            WHERE id = ?
            """,
            (now, memory_id),
        )

    async def count(self) -> int:
        """Get total memory count."""
        row = await self.db.fetch_one("SELECT COUNT(*) as cnt FROM memories")
        return row["cnt"] if row else 0

    async def all_memories(self) -> list[Memory]:
        """Retrieve all memories (use sparingly)."""
        rows = await self.db.fetch_all(
            "SELECT * FROM memories ORDER BY importance DESC"
        )
        return [self._row_to_memory(r) for r in rows]

    async def search(self, query: str) -> list[Memory]:
        """Search memories by keyword (for the :search command)."""
        return await self._search_relevant(query, limit=50)

    async def add_manual(self, content: str, importance: float = 0.5, level: int = 1, child_memory_ids: list[str] = None) -> Memory:
        """Add a memory manually from user command or pruner flush.

        Bypasses A-MAC threshold and duplicate check (intentional), but still
        runs the injection guard to prevent prompt-injection via memory content.
        """
        # Apply injection guard even on manual/system memories
        guard_result = self.guard.validate_write_attempt(f"manual-{content[:32]}", content)
        if not guard_result["allowed"]:
            log.warning("MemoryGuard blocked add_manual content: %s...", content[:40])
            return Memory(
                content=content,
                source=MemorySource.USER,
                importance=importance,
                tags=["manual", "guard_blocked"],
                level=level,
                child_memory_ids=child_memory_ids or [],
            )
        if guard_result["flagged"]:
            importance = importance * 0.7

        memory = Memory(
            content=content,
            source=MemorySource.USER,
            importance=importance,
            tags=["manual"],
            level=level,
            child_memory_ids=child_memory_ids or [],
        )
        # Bypass duplicate check for manual memories - user explicitly wants it
        await self.db.execute(
            """
            INSERT INTO memories (id, content, source, memory_type, importance,
                                  confidence, created_at, last_accessed,
                                  access_count, tags, level, child_memory_ids, provenance_json,
                                  related_memories, archived_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.id,
                memory.content,
                memory.source.value,
                memory.memory_type.value,
                memory.importance,
                memory.confidence,
                memory.created_at,
                memory.last_accessed,
                memory.access_count,
                json.dumps(memory.tags),
                memory.level,
                json.dumps(memory.child_memory_ids),
                json.dumps(memory.provenance),
                json.dumps(memory.related_memories),
                memory.archived_at,
            ),
        )
        if self.vs.is_active:
            type_val = memory.memory_type.value if isinstance(memory.memory_type, MemoryType) else memory.memory_type
            await asyncio.to_thread(self.vs.add_chunks, [memory.content], [{"type": type_val, "timestamp": datetime.now(timezone.utc).timestamp()}], ids=[memory.id])

        try:
            if await self._check_fts5():
                await self.db.execute(
                    "INSERT INTO memories_fts(content, id) VALUES (?, ?)",
                    (memory.content, memory.id),
                )
        except Exception:
            self._fts5_available = False

        log.info(f"Manual memory stored: {content[:40]}...")
        return memory

    # ------------------------------------------------------------------
    # Retrieval Strategy
    # ------------------------------------------------------------------

    async def retrieve_context(self, query: str) -> list[Memory]:
        """
        Retrieve memory context using hybrid search (RRF) blending keyword and semantic pools.
        """
        candidates: dict[str, Memory] = {}

        # Pool 1: Recent
        recent = await self._get_recent(MAX_RECENT_MEMORIES)
        for m in recent:
            candidates[m.id] = m

        # Pool 2: Important
        important = await self._get_important(MAX_IMPORTANT_MEMORIES)
        for m in important:
            candidates[m.id] = m

        # Pool 3: Relevant (keyword search)
        keyword_results = []
        if query.strip():
            keyword_results = await self._search_relevant(query, MAX_RELEVANT_MEMORIES * 2)
            for m in keyword_results:
                candidates[m.id] = m

        # Pool 4: Semantic (vector search)
        semantic_results = []
        semantic_memories = []
        if query.strip() and self.vs.is_active:
            semantic_results = await asyncio.to_thread(self.vs.search, query, MAX_RELEVANT_MEMORIES * 2)
            semantic_ids = [res["id"] for res in semantic_results if res.get("id")]
            if semantic_ids:
                placeholders = ",".join("?" * len(semantic_ids))
                rows = await self.db.fetch_all(
                    f"SELECT * FROM memories WHERE id IN ({placeholders}) AND archived_at IS NULL",
                    tuple(semantic_ids)
                )
                import math
                now_ts = datetime.now(timezone.utc).timestamp()
                for row in rows:
                    res = next((r for r in semantic_results if r["id"] == row["id"]), None)
                    if res:
                        created_at = datetime.fromisoformat(row["created_at"])
                        age_days = (now_ts - created_at.timestamp()) / 86400.0
                        adjusted_score = (1.0 - res.get("distance", 1.0)) * math.exp(-age_days / 180.0)
                        if adjusted_score > 0.1:
                            m = self._row_to_memory(row)
                            semantic_memories.append(m)
                            candidates[m.id] = m

        # Reciprocal Rank Fusion (RRF) for hybrid search relevance blending
        rrf_scores = {}
        if query.strip():
            # Rank keyword results by TF-IDF keyword relevance
            def get_keyword_relevance(m):
                query_words = {w.lower() for w in query.split() if len(w) > 2}
                content_words_list = [w.lower() for w in m.content.split() if len(w) > 2]
                if not query_words:
                    return 0.0
                matching_terms = query_words & set(content_words_list)
                matching_term_count = len(matching_terms)
                matching_term_freq = sum(content_words_list.count(w) for w in matching_terms)
                import math
                return (matching_term_count / len(query_words)) * math.log(1 + matching_term_freq)

            keyword_sorted = sorted(keyword_results, key=get_keyword_relevance, reverse=True)
            keyword_ranks = {m.id: idx + 1 for idx, m in enumerate(keyword_sorted)}

            # Rank semantic results by vector similarity distance
            semantic_ranks = {}
            if self.vs.is_active and semantic_results:
                def get_semantic_distance(m):
                    res = next((r for r in semantic_results if r["id"] == m.id), None)
                    return res["distance"] if res and "distance" in res else 1.0
                semantic_sorted = sorted(semantic_memories, key=get_semantic_distance)
                semantic_ranks = {m.id: idx + 1 for idx, m in enumerate(semantic_sorted)}

            # Compute RRF score (k = 60)
            all_relevant_ids = set(keyword_ranks.keys()) | set(semantic_ranks.keys())
            raw_rrf_scores = {}
            for m_id in all_relevant_ids:
                rank_k = keyword_ranks.get(m_id, 1e9)
                rank_s = semantic_ranks.get(m_id, 1e9)
                raw_rrf_scores[m_id] = 1.0 / (60.0 + rank_k) + 1.0 / (60.0 + rank_s)

            # Normalize RRF score to range [0.0, 1.0]
            if raw_rrf_scores:
                max_rrf = 2.0 / 61.0
                for m_id, raw_score in raw_rrf_scores.items():
                    rrf_scores[m_id] = min(raw_score / max_rrf, 1.0)

        # Fetch A-MAC composite scores to reward high-quality admitted memories
        amac_scores: dict[str, float] = {}
        if candidates:
            ids = list(candidates.keys())
            placeholders = ",".join("?" * len(ids))
            try:
                amac_rows = await self.db.fetch_all(
                    f"SELECT memory_id, composite_score FROM admitted_memories"
                    f" WHERE memory_id IN ({placeholders})",
                    tuple(ids),
                )
                amac_scores = {r["memory_id"]: float(r["composite_score"]) for r in amac_rows}
            except Exception:
                pass

        result = sorted(
            candidates.values(),
            key=lambda m: self._retrieval_score(m, query, rrf_scores, amac_scores.get(m.id, 0.5)),
            reverse=True,
        )

        # Update access timestamps for retrieved memories
        for m in result:
            await self.update_access(m.id)

        log.debug(f"Retrieved {len(result)} memories for context")
        return result

    async def _get_recent(self, limit: int) -> list[Memory]:
        """Get most recently accessed memories."""
        rows = await self.db.fetch_all(
            "SELECT * FROM memories WHERE archived_at IS NULL ORDER BY last_accessed DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_memory(r) for r in rows]

    async def _get_important(self, limit: int) -> list[Memory]:
        """Get highest importance memories."""
        rows = await self.db.fetch_all(
            "SELECT * FROM memories WHERE archived_at IS NULL ORDER BY importance DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_memory(r) for r in rows]

    async def _search_relevant(self, query: str, limit: int) -> list[Memory]:
        """
        Keyword relevance search: FTS5 (BM25) when available, LIKE fallback.
        """
        keywords = [kw.strip().lower() for kw in query.split() if len(kw.strip()) > 2]
        if not keywords:
            return []

        # Try FTS5 first — returns BM25-ranked results
        if await self._check_fts5():
            try:
                # Wrap each token in double-quotes to handle punctuation safely
                fts_query = " ".join(f'"{kw}"' for kw in keywords)
                fts_rows = await self.db.fetch_all(
                    "SELECT id FROM memories_fts WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
                    (fts_query, limit),
                )
                if fts_rows:
                    ids = [r["id"] for r in fts_rows]
                    placeholders = ",".join("?" * len(ids))
                    rows = await self.db.fetch_all(
                        f"SELECT * FROM memories WHERE id IN ({placeholders}) AND archived_at IS NULL"
                        f" ORDER BY importance DESC",
                        tuple(ids),
                    )
                    return [self._row_to_memory(r) for r in rows]
            except Exception as exc:
                log.debug("FTS5 search failed, falling back to LIKE: %s", exc)
                self._fts5_available = False  # reset so next call retries

        # Fallback: LIKE search (original behaviour)
        conditions = " OR ".join(["LOWER(content) LIKE ?" for _ in keywords])
        params = tuple(f"%{kw}%" for kw in keywords)
        rows = await self.db.fetch_all(
            f"SELECT * FROM memories WHERE archived_at IS NULL AND ({conditions}) ORDER BY importance DESC LIMIT ?",
            (*params, limit),
        )
        return [self._row_to_memory(r) for r in rows]

    @staticmethod
    def _retrieval_score(
        memory: Memory,
        query: str,
        rrf_scores: dict[str, float] | None = None,
        amac_boost: float = 0.5,
    ) -> float:
        """
        Fuse importance, reliability, recency, relevance, source trust, and A-MAC quality.

        amac_boost is the composite A-MAC score from admitted_memories (0.0–1.0).
        Centered around 0.5 so memories without an A-MAC record are neutral.
        """
        import math
        query_words = {w.lower() for w in query.split() if len(w) > 2}
        content_words_list = [w.lower() for w in memory.content.split() if len(w) > 2]
        relevance = 0.0
        if rrf_scores and memory.id in rrf_scores:
            relevance = rrf_scores[memory.id]
        elif query_words:
            matching_terms = query_words & set(content_words_list)
            matching_term_count = len(matching_terms)
            matching_term_freq = sum(content_words_list.count(w) for w in matching_terms)
            relevance = (matching_term_count / len(query_words)) * math.log(1 + matching_term_freq)

        age_days = 20.7944  # fallback to achieve exp(-age_days/30) = 0.5
        try:
            last_accessed = datetime.fromisoformat(memory.last_accessed)
            age_days = max((datetime.now(timezone.utc) - last_accessed).days, 0)
        except Exception:
            pass

        source_trust = {
            MemorySource.USER: 0.9,
            MemorySource.SYSTEM: 0.85,
            MemorySource.REFLECTION: 0.65,
            MemorySource.INFERENCE: 0.55,
        }.get(memory.source, 0.5)

        type_bonus = {
            MemoryType.PREFERENCE: 0.08,
            MemoryType.PROCEDURAL: 0.06,
            MemoryType.PROJECT: 0.06,
            MemoryType.NORMATIVE: 0.10,
            MemoryType.CHARACTER: 0.09,
        }.get(memory.memory_type, 0.0)

        # A-MAC quality signal: centered at 0.5 so score ∈ [-0.03, +0.03]
        amac_signal = (amac_boost - 0.5) * 0.06

        return (
            memory.importance * exp(-age_days / 30) * 0.43
            + memory.confidence * 0.20
            + relevance * 0.25
            + source_trust * 0.09
            + type_bonus
            + amac_signal
        )

    # ------------------------------------------------------------------
    # Duplicate Detection
    # ------------------------------------------------------------------

    async def _is_duplicate(self, content: str) -> bool:
        """
        Check if a very similar memory already exists.
        
        Uses vector semantic similarity to catch rephrased facts.
        Falls back to word overlap if VectorStore is offline.
        """
        if self.vs.is_active:
            results = await asyncio.to_thread(self.vs.search, content, 1)
            # Distance < 0.2 typically indicates semantic equivalence with MiniLM
            if results and results[0].get("distance", 1.0) < 0.2:
                return True

        content_lower = content.lower().strip()
        content_words = set(content_lower.split())

        if not content_words:
            return False

        # Fallback: Check against recent memories
        recent = await self._get_recent(50)
        for mem in recent:
            existing_words = set(mem.content.lower().strip().split())
            if not existing_words:
                continue

            # Calculate word overlap
            overlap = content_words & existing_words
            smaller = min(len(content_words), len(existing_words))

            if smaller > 0 and len(overlap) / smaller >= 0.8:
                return True

        return False

    async def archive(self, memory_id: str) -> bool:
        """Soft-archive a memory without destroying provenance."""
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE memories SET archived_at = ? WHERE id = ?",
            (now, memory_id),
        )
        return True

    async def update_confidence(self, memory_id: str, confidence: float) -> bool:
        """Adjust memory confidence for correction workflows."""
        confidence = max(0.0, min(1.0, confidence))
        await self.db.execute(
            "UPDATE memories SET confidence = ? WHERE id = ?",
            (confidence, memory_id),
        )
        return True

    async def merge(self, keep_id: str, merge_id: str) -> bool:
        """Merge two memories by archiving the duplicate and linking provenance."""
        keep = await self.get(keep_id)
        duplicate = await self.get(merge_id)
        if not keep or not duplicate:
            return False
        related = set(keep.related_memories)
        related.add(merge_id)
        provenance = dict(keep.provenance)
        provenance.setdefault("merged_memory_ids", [])
        provenance["merged_memory_ids"].append(merge_id)
        await self.db.execute(
            "UPDATE memories SET related_memories = ?, provenance_json = ? WHERE id = ?",
            (json.dumps(sorted(related)), json.dumps(provenance), keep_id),
        )
        await self.archive(merge_id)
        return True

    async def decay_importance(self, days: int = 7, decay_factor: float = 0.95):
        """Multiplies importance by decay_factor for memories not accessed in the last `days`."""
        await self.db.execute(
            """
            UPDATE memories
            SET importance = importance * ?
            WHERE (julianday('now') - julianday(last_accessed)) > ?
              AND archived_at IS NULL
            """,
            (decay_factor, days)
        )
        log.info(f"Decayed importance of memories untouched in {days} days by factor {decay_factor}.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_memory(row: dict) -> Memory:
        """Convert a database row to a Memory model."""
        return Memory(
            id=row["id"],
            content=row["content"],
            source=row["source"],
            memory_type=row.get("memory_type", "semantic"),
            importance=row["importance"],
            confidence=row.get("confidence", 0.5),
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            access_count=row["access_count"],
            tags=json.loads(row["tags"]),
            level=row.get("level", 1),
            child_memory_ids=json.loads(row.get("child_memory_ids", "[]")),
            provenance=json.loads(row.get("provenance_json", "{}")),
            related_memories=json.loads(row["related_memories"]),
            archived_at=row.get("archived_at"),
        )
    # ------------------------------------------------------------------
    # Semantic Profiles (Phase 7)
    # ------------------------------------------------------------------

    async def get_semantic_profile(self, term: str) -> dict | None:
        """Retrieve the objective mapping for a subjective term."""
        row = await self.db.fetch_one(
            "SELECT * FROM semantic_profiles WHERE term = ?", (term.lower(),)
        )
        if row:
            return {
                "term": row["term"],
                "objective_proxies": json.loads(row["objective_proxies"]),
                "context_tags": json.loads(row["context_tags"]),
                "confidence": row["confidence"],
                "updated_at": row["updated_at"]
            }
        return None

    async def save_semantic_profile(self, term: str, objective_proxies: list[str], confidence: float = 0.5):
        """Save or update a semantic profile."""
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """
            INSERT INTO semantic_profiles (term, objective_proxies, confidence, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(term) DO UPDATE SET
                objective_proxies = excluded.objective_proxies,
                confidence = excluded.confidence,
                updated_at = excluded.updated_at
            """,
            (term.lower(), json.dumps(objective_proxies), confidence, now)
        )

    async def get_all_semantic_profiles(self) -> dict[str, list[str]]:
        """Retrieve all learned semantic mappings."""
        rows = await self.db.fetch_all("SELECT term, objective_proxies FROM semantic_profiles")
        return {row["term"]: json.loads(row["objective_proxies"]) for row in rows}
