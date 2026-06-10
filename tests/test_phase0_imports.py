"""
tests/test_phase0_imports.py
============================
Phase 0 Definition of Done — smoke tests.

These tests verify that:
  1. The vendor export ran successfully.
  2. All critical Silex + Agent classes can be imported without errors.
  3. The config patch redirected ~/.kronos → ~/.clasp.
  4. Clasp's own config module loads correctly.

Run:
    pytest tests/test_phase0_imports.py -v

All tests must be GREEN before starting Phase 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make sure the project root is on sys.path when running tests directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# 1. Vendor copy exists
# ---------------------------------------------------------------------------

def test_vendor_directory_exists():
    vendor = PROJECT_ROOT / "clasp" / "vendor"
    assert vendor.exists(), (
        "clasp/vendor/ does not exist. Run: python scripts/export_engines.py"
    )


def test_vendor_version_file_exists():
    version_file = PROJECT_ROOT / "clasp" / "vendor" / "VENDOR_VERSION"
    assert version_file.exists(), (
        "VENDOR_VERSION not found. Run: python scripts/export_engines.py"
    )


def test_silex_directory_exists():
    silex = PROJECT_ROOT / "clasp" / "vendor" / "silex"
    assert silex.exists(), "clasp/vendor/silex/ missing — export did not run"


def test_agent_directory_exists():
    agent = PROJECT_ROOT / "clasp" / "vendor" / "agent"
    assert agent.exists(), "clasp/vendor/agent/ missing — export did not run"


def test_knowledge_graph_directory_exists():
    kg = PROJECT_ROOT / "clasp" / "vendor" / "silex" / "knowledge_graph"
    assert kg.exists(), (
        "clasp/vendor/silex/knowledge_graph/ is missing. "
        "This is required — world/graph.py imports from it."
    )


# ---------------------------------------------------------------------------
# 2. Core Silex imports
# ---------------------------------------------------------------------------

def test_import_database():
    """Database (aiosqlite async engine) must import cleanly."""
    from clasp.vendor.silex.storage.database import Database  # noqa: F401
    assert Database is not None


def test_import_knowledge_graph():
    """KnowledgeGraph (NetworkX-backed causal graph) must import cleanly."""
    from clasp.vendor.silex.world.graph import KnowledgeGraph  # noqa: F401
    assert KnowledgeGraph is not None


def test_import_schemas():
    """Pydantic schemas must import cleanly."""
    from clasp.vendor.silex.models.schemas import (  # noqa: F401
        KnowledgeNode,
        CausalEdge,
    )
    assert KnowledgeNode is not None
    assert CausalEdge is not None


def test_import_memory_store():
    """MemoryStore must import cleanly."""
    from clasp.vendor.silex.memory.memory_store import MemoryStore  # noqa: F401
    assert MemoryStore is not None


def test_import_llm_factory():
    """LLM factory must import cleanly."""
    from clasp.vendor.silex.llm.factory import build_provider  # noqa: F401
    assert callable(build_provider)


# ---------------------------------------------------------------------------
# 3. Agent imports
# ---------------------------------------------------------------------------

def test_import_worker_orchestrator():
    """WorkerOrchestrator must import cleanly."""
    from clasp.vendor.agent.orchestrator import WorkerOrchestrator  # noqa: F401
    assert WorkerOrchestrator is not None


def test_import_worker_job():
    """WorkerJob schema must import cleanly."""
    from clasp.vendor.agent.jobs import WorkerJob  # noqa: F401
    assert WorkerJob is not None


def test_import_actuation_lease():
    """ActuationLease must import cleanly."""
    from clasp.vendor.agent.security.lease import ActuationLease  # noqa: F401
    assert ActuationLease is not None


# ---------------------------------------------------------------------------
# 4. Config patch verification
# ---------------------------------------------------------------------------

def test_config_patch_applied():
    """
    The vendor config.py must have CLASP_HOME pointing to ~/.clasp,
    NOT ~/.kronos. If this fails, the patch in export_engines.py did not apply.
    """
    from clasp.vendor.silex.utils import config as vendor_config
    clasp_home = Path.home() / ".clasp"
    assert hasattr(vendor_config, "CLASP_HOME"), (
        "CLASP_HOME not in vendor config — patch was not applied. "
        "Re-run: python scripts/export_engines.py"
    )
    assert vendor_config.CLASP_HOME == clasp_home, (
        f"CLASP_HOME points to {vendor_config.CLASP_HOME}, expected {clasp_home}"
    )
    assert vendor_config.KRONOS_HOME == clasp_home, (
        f"KRONOS_HOME alias not pointing to CLASP_HOME. "
        f"Got {vendor_config.KRONOS_HOME}"
    )


def test_kronos_home_is_clasp_home():
    """KRONOS_HOME must equal ~/.clasp (not ~/.kronos)."""
    from clasp.vendor.silex.utils import config as vendor_config
    expected = Path.home() / ".clasp"
    assert vendor_config.KRONOS_HOME == expected, (
        f"KRONOS_HOME = {vendor_config.KRONOS_HOME} — should be {expected}. "
        "Patch not applied."
    )


# ---------------------------------------------------------------------------
# 5. Clasp config
# ---------------------------------------------------------------------------

def test_clasp_config_paths():
    """Clasp's own config module must load and define expected paths."""
    from clasp import config as clasp_config
    assert clasp_config.CLASP_HOME == Path.home() / ".clasp"
    assert clasp_config.CLASP_DB.name == "clasp.db"
    assert clasp_config.CLASP_SETTINGS.name == "settings.json"
    assert clasp_config.CLASP_SECRETS.name == "secrets.json"


def test_clasp_causal_defaults():
    """Causal engine parameter defaults must be sensible."""
    from clasp import config as clasp_config
    assert clasp_config.CAUSAL_TIME_WINDOW > 0
    assert clasp_config.CAUSAL_MIN_OCCURRENCES >= 1
    assert 0.0 < clasp_config.CAUSAL_MIN_CONFIDENCE < 1.0
    assert clasp_config.CAUSAL_SIGNIFICANCE_Z > 0.0
