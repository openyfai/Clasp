"""
silex/utils/logger.py — Structured logging for ARIA/Kronos.

Behaviour
─────────
  Interactive CLI mode (INK_ACTIVE=1 env var set by scripts/run.py):
    • All output → ~/.kronos/kronos.log (file only, no terminal noise)
    • RichHandler is NOT attached — Ink owns the terminal exclusively

  All other modes (daemon, telegram, tests, sub-commands):
    • RichHandler on stderr, same as before

Security: show_locals disabled to prevent API key leakage in tracebacks.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


# ── Log file path ─────────────────────────────────────────────────────────────
from clasp.vendor.silex.utils.config import KRONOS_HOME
_LOG_DIR  = KRONOS_HOME
_LOG_FILE = _LOG_DIR / "kronos.log"

_FILE_FORMATTER = logging.Formatter(
    "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _is_ink_active() -> bool:
    """True when the Ink UI subprocess is rendering and owns the terminal."""
    return os.environ.get("KRONOS_INK_ACTIVE", "") == "1"


def _ensure_file_handler(logger: logging.Logger) -> None:
    """Attach a rotating file handler if not already present."""
    # Avoid duplicate file handlers on repeated calls
    for h in logger.handlers:
        if isinstance(h, (logging.FileHandler,)):
            return

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(_LOG_FILE, encoding="utf-8", mode="a")
    fh.setFormatter(_FILE_FORMATTER)
    logger.addHandler(fh)


def setup_logger(name: str = "aria", level: str = "INFO") -> logging.Logger:
    """
    Create a logger for a silex/aria module.

    When KRONOS_INK_ACTIVE=1:  file-only (zero terminal output)
    Otherwise:                  RichHandler on stderr (original behaviour)
    """
    # Fix Windows charmap encode errors when printing emojis (e.g. ❌)
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    logger = logging.getLogger(name)

    if not logger.handlers:
        if _is_ink_active():
            # ── Ink mode: silent file-only logging ────────────────────────
            _ensure_file_handler(logger)
        else:
            # ── Normal mode: Rich stderr handler ──────────────────────────
            from rich.logging import RichHandler
            handler = RichHandler(
                rich_tracebacks=True,
                show_time=True,
                show_path=False,
                markup=True,
                # SECURITY: Never show locals — they can contain API keys.
                tracebacks_show_locals=False,
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


def redirect_root_logger_to_file() -> None:
    """
    Called once by scripts/run.py immediately after bridge.start() confirms
    Ink is active. Removes ALL existing stream handlers from the root logger
    and replaces them with a single file handler so that no silex/aria/asyncio
    log line can leak to the terminal while Ink is rendering.
    """
    root = logging.getLogger()

    # Remove every handler that writes to a stream (stdout/stderr/console)
    to_remove = [
        h for h in root.handlers
        if isinstance(h, (logging.StreamHandler,))
        and not isinstance(h, logging.FileHandler)
    ]
    for h in to_remove:
        root.removeHandler(h)

    _ensure_file_handler(root)
    root.setLevel(logging.DEBUG)   # file captures everything; terminal sees nothing
