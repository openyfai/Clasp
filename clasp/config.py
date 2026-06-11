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

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

# Pre-shared API key for bearer token auth. Set this to a strong random string.
# Generate one with: python -c "import secrets; print(secrets.token_hex(32))"
# In production, set CLASP_API_KEY environment variable. If unset, auth is
# disabled with a loud warning (development convenience only).
CLASP_API_KEY: str | None = os.getenv("CLASP_API_KEY")

# CORS allowed origins. Comma-separated list. Defaults to localhost only.
# Example: CLASP_CORS_ORIGINS=http://dashboard.plant.local,https://clasp.mycompany.com
_cors_env = os.getenv("CLASP_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000")
CLASP_CORS_ORIGINS: list[str] = [o.strip() for o in _cors_env.split(",") if o.strip()]

# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

# Set CLASP_DEMO_MODE=true to start the TEP background demo on server boot.
# Defaults to false — a real plant deployment should NOT auto-start demo data.
CLASP_DEMO_MODE: bool = os.getenv("CLASP_DEMO_MODE", "false").lower() == "true"

# Max calls per minute per IP to POST /api/investigate (LLM endpoint).
CLASP_INVESTIGATE_RATE_LIMIT: int = int(os.getenv("CLASP_INVESTIGATE_RATE_LIMIT", "10"))
