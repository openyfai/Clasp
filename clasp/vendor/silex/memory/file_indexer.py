"""
silex/memory/file_indexer.py — Indexes local files into ChromaDB for RAG.

Stores chunks in a separate collection `kronos_files` (not `aria_workspace`)
so file content doesn't pollute the tool-retrieved workspace snippets.

Chunking strategy:
  .py / .ts / .js / .tsx / .jsx  → split at function/class definitions
  .md / .txt / .rst               → split at heading lines (## or ---)
  .json / .yaml / .toml           → whole file if <3KB, else top-level keys
  Everything else                 → fixed 800-char windows with 100-char overlap
"""
from __future__ import annotations
import os
import uuid
import hashlib
import logging
from pathlib import Path
from typing import Iterator

log = logging.getLogger("silex.memory.file_indexer")

# Files larger than this are skipped entirely
MAX_FILE_BYTES = 500_000  # 500KB

SUPPORTED_EXTENSIONS = {
    ".py", ".ts", ".js", ".tsx", ".jsx",     # code
    ".md", ".txt", ".rst",                    # prose
    ".json", ".yaml", ".yml", ".toml",        # config
    ".html", ".css", ".scss",                 # web
    ".sh", ".bash", ".zsh",                   # shell
    ".go", ".rs", ".c", ".cpp", ".h",         # other code
}

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    "dist", "build", ".ruff_cache", ".mypy_cache",
}


class FileIndexer:
    """Indexes a folder into a ChromaDB collection for semantic search."""

    def __init__(self, vector_store=None):
        """
        Args:
            vector_store: A VectorStore instance. If None, creates its own
                          ChromaDB client pointed at ~/.kronos/storage/vector_db
                          using a separate 'kronos_files' collection.
        """
        self._vs = vector_store
        self._collection = None
        self._init_collection()

    def _init_collection(self):
        try:
            import chromadb
            from chromadb.utils import embedding_functions
            from clasp.vendor.silex.utils.config import SILEX_VECTOR_DB
            client = chromadb.PersistentClient(path=str(SILEX_VECTOR_DB))
            ef = embedding_functions.DefaultEmbeddingFunction()
            self._collection = client.get_or_create_collection(
                name="kronos_files", embedding_function=ef
            )
            log.info("FileIndexer initialized (collection: kronos_files)")
        except Exception as exc:
            log.warning("FileIndexer: ChromaDB not available: %s", exc)

    def index_folder(self, folder: str | Path, force: bool = False) -> dict:
        """
        Index all supported files under `folder`.
        Returns: {"indexed": int, "skipped": int, "errors": int}
        """
        folder = Path(folder).resolve()
        if not folder.is_dir():
            raise ValueError(f"Not a directory: {folder}")

        stats = {"indexed": 0, "skipped": 0, "errors": 0}
        for file_path in self._walk(folder):
            try:
                n = self._index_file(file_path, force=force)
                if n > 0:
                    stats["indexed"] += 1
                else:
                    stats["skipped"] += 1
            except Exception as exc:
                log.warning("Error indexing %s: %s", file_path, exc)
                stats["errors"] += 1

        log.info("Indexed folder %s: %s", folder, stats)
        return stats

    def index_file(self, file_path: str | Path, force: bool = False) -> int:
        """Index a single file. Returns number of chunks added."""
        return self._index_file(Path(file_path).resolve(), force=force)

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        """Search the file index. Returns list of {content, path, start_line, distance}."""
        if not self._collection:
            return []
        try:
            results = self._collection.query(query_texts=[query], n_results=n_results)
            out = []
            if results["documents"]:
                for i, doc in enumerate(results["documents"][0]):
                    meta = results["metadatas"][0][i] if results["metadatas"] else {}
                    out.append({
                        "content": doc,
                        "path": meta.get("path", ""),
                        "start_line": meta.get("start_line", 0),
                        "distance": results["distances"][0][i] if results.get("distances") else None,
                    })
            return out
        except Exception as exc:
            log.warning("File search failed: %s", exc)
            return []

    def clear(self):
        """Wipe all indexed files."""
        if not self._collection:
            return
        try:
            import chromadb
            from chromadb.utils import embedding_functions
            from clasp.vendor.silex.utils.config import SILEX_VECTOR_DB
            client = chromadb.PersistentClient(path=str(SILEX_VECTOR_DB))
            client.delete_collection("kronos_files")
            ef = embedding_functions.DefaultEmbeddingFunction()
            self._collection = client.get_or_create_collection(
                name="kronos_files", embedding_function=ef
            )
            log.info("FileIndexer: cleared kronos_files collection")
        except Exception as exc:
            log.warning("FileIndexer clear failed: %s", exc)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _walk(self, folder: Path) -> Iterator[Path]:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in files:
                fp = Path(root) / fname
                if fp.suffix.lower() in SUPPORTED_EXTENSIONS:
                    if fp.stat().st_size <= MAX_FILE_BYTES:
                        yield fp

    def _index_file(self, file_path: Path, force: bool = False) -> int:
        if not self._collection:
            return 0
        text = file_path.read_text(encoding="utf-8", errors="replace")
        file_hash = hashlib.md5(text.encode()).hexdigest()

        # Skip if already indexed with same content hash
        if not force:
            existing = self._collection.get(where={"path": str(file_path)}, limit=1)
            if existing["ids"]:
                stored_hash = existing["metadatas"][0].get("hash", "") if existing["metadatas"] else ""
                if stored_hash == file_hash:
                    return 0  # unchanged

        # Remove stale chunks for this file
        self._collection.delete(where={"path": str(file_path)})

        # Chunk the file
        chunks = list(self._chunk(text, file_path))
        if not chunks:
            return 0

        texts = [c["text"] for c in chunks]
        metas = [{
            "path": str(file_path),
            "start_line": c["start_line"],
            "hash": file_hash,
        } for c in chunks]
        ids = [str(uuid.uuid4()) for _ in chunks]

        self._collection.add(documents=texts, metadatas=metas, ids=ids)
        return len(chunks)

    def _chunk(self, text: str, file_path: Path) -> Iterator[dict]:
        ext = file_path.suffix.lower()
        if ext in {".py", ".ts", ".js", ".tsx", ".jsx"}:
            yield from self._chunk_code(text)
        elif ext in {".md", ".txt", ".rst"}:
            yield from self._chunk_prose(text)
        else:
            yield from self._chunk_fixed(text)

    def _chunk_code(self, text: str) -> Iterator[dict]:
        """Split at function/class definitions."""
        import re
        pattern = re.compile(r'^(def |class |async def |function |const |export )', re.MULTILINE)
        lines = text.splitlines()
        split_lines = {m.start() for m in pattern.finditer(text)}
        # Convert char offsets to line numbers
        char = 0
        split_line_nums = set()
        for i, line in enumerate(lines):
            if char in split_lines:
                split_line_nums.add(i)
            char += len(line) + 1

        chunk_start = 0
        current = []
        for i, line in enumerate(lines):
            if i in split_line_nums and current and i > chunk_start:
                yield {"text": "\n".join(current), "start_line": chunk_start}
                current = []
                chunk_start = i
            current.append(line)
            if len("\n".join(current)) > 1200 and current:
                yield {"text": "\n".join(current), "start_line": chunk_start}
                current = []
                chunk_start = i + 1
        if current:
            yield {"text": "\n".join(current), "start_line": chunk_start}

    def _chunk_prose(self, text: str) -> Iterator[dict]:
        """Split at markdown headings."""
        import re
        lines = text.splitlines()
        chunk = []
        start = 0
        for i, line in enumerate(lines):
            if re.match(r'^#{1,3} |^[-=]{3,}$', line) and chunk:
                yield {"text": "\n".join(chunk), "start_line": start}
                chunk = []
                start = i
            chunk.append(line)
            if len("\n".join(chunk)) > 1500:
                yield {"text": "\n".join(chunk), "start_line": start}
                chunk = []
                start = i + 1
        if chunk:
            yield {"text": "\n".join(chunk), "start_line": start}

    def _chunk_fixed(self, text: str, window: int = 800, overlap: int = 100) -> Iterator[dict]:
        """Fixed-size windows with overlap."""
        lines = text.splitlines()
        pos = 0
        start_line = 0
        while pos < len(text):
            chunk = text[pos:pos + window]
            yield {"text": chunk, "start_line": start_line}
            pos += window - overlap
            start_line += chunk[:window - overlap].count("\n")
