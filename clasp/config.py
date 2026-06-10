"""
clasp/config.py
===============
Clasp runtime configuration — single source of truth for all paths.

This module is intentionally separate from the vendored silex utils/config.py.
The vendored config is patched to point to ~/.clasp, but Clasp code should
import from HERE, not from vendor.silex.utils.config.

Usage:
    from clasp.config import CLASP_HOME, CLASP_DB, get_clasp_settings
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root if present (development)
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file)

# ---------------------------------------------------------------------------
# Home directory — all Clasp runtime data lives here
# ---------------------------------------------------------------------------

CLASP_HOME      = Path.home() / ".clasp"
CLASP_DB        = CLASP_HOME / "storage" / "clasp.db"
CLASP_SETTINGS  = CLASP_HOME / "config" / "settings.json"
CLASP_SECRETS   = CLASP_HOME / "config" / "secrets.json"
CLASP_WORKSPACE = CLASP_HOME / "workspace"
CLASP_LOGS      = CLASP_HOME / "logs"
CLASP_VECTOR_DB = CLASP_HOME / "storage" / "vector_db"

# ---------------------------------------------------------------------------
# Causal engine parameters (from env or defaults)
# ---------------------------------------------------------------------------

CAUSAL_TIME_WINDOW    = int(os.getenv("CLASP_TIME_WINDOW", "3600"))
CAUSAL_MIN_OCCURRENCES = int(os.getenv("CLASP_MIN_OCCURRENCES", "5"))
CAUSAL_MIN_CONFIDENCE  = float(os.getenv("CLASP_MIN_CONFIDENCE", "0.7"))
CAUSAL_SIGNIFICANCE_Z  = float(os.getenv("CLASP_SIGNIFICANCE_ZSCORE", "2.0"))

# ---------------------------------------------------------------------------
# LLM provider
# ---------------------------------------------------------------------------

LLM_PROVIDER = os.getenv("CLASP_LLM_PROVIDER", "gemini")
LLM_MODEL    = os.getenv("CLASP_LLM_MODEL", "gemini-2.0-flash")

# ---------------------------------------------------------------------------
# TEP Simulator
# ---------------------------------------------------------------------------

TEP_DATA_DIR     = Path(os.getenv("CLASP_TEP_DATA_DIR", "data/tep"))
TEP_DEFAULT_SPEED = float(os.getenv("CLASP_TEP_DEFAULT_SPEED", "500"))

# ---------------------------------------------------------------------------
# API Server
# ---------------------------------------------------------------------------

API_HOST = os.getenv("CLASP_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("CLASP_API_PORT", "8000"))
