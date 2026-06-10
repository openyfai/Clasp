# Copyright (c) 2026 openyfai (YF)
# Licensed under the Business Source License 1.1 (BSL 1.1)
# See LICENSE file in the project root for full license terms.

"""
scripts/export_engines.py
=========================
Phase 0 -- Vendor export script.

Run once:  python scripts/export_engines.py

What it does:
  1. Copies selected silex/ and agent/ subdirectories from e:\\AGI into
     clasp/vendor/ (read-only vendored snapshot).
  2. Performs the single surgical patch to vendor/silex/utils/config.py:
       KRONOS_HOME = Path.home() / ".kronos"
     ->  CLASP_HOME  = Path.home() / ".clasp"
        KRONOS_HOME = CLASP_HOME    alias keeps all downstream paths working
  3. Writes clasp/vendor/VENDOR_VERSION with the current git commit of e:\\AGI.

Re-run any time you want to pull a fresh snapshot from e:\\AGI.
The patch is re-applied automatically after every copy.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).resolve().parent          # e:\Clasp\scripts
PROJECT_ROOT = SCRIPT_DIR.parent                         # e:\Clasp
VENDOR_DIR   = PROJECT_ROOT / "clasp" / "vendor"
AGI_ROOT     = Path(r"e:\AGI")

# ---------------------------------------------------------------------------
# What to copy from silex/
# Explicitly listed -- we do NOT copy silex wholesale.
# ---------------------------------------------------------------------------

SILEX_MODULES = [
    "storage",          # Database (aiosqlite)
    "world",            # KnowledgeGraph + NetworkX
    "knowledge_graph",  # [WARN] Required: world/graph.py imports knowledge_graph.ontology.Ontology
    "models",           # KnowledgeNode, CausalEdge, NodeType, EdgeType
    "memory",           # MemoryStore (episodic/semantic)
    "llm",              # LLM factory + providers + registry
    "utils",            # config.py -- patched after copy
    "runtime",          # RuntimeSettingsStore -- imported by utils/config.py
    "security",         # Security primitives used by orchestrator
]

# Individual files to copy from silex root
SILEX_ROOT_FILES = [
    "__init__.py",
]

# ---------------------------------------------------------------------------
# What to copy from agent/ (full package -- orchestrator has deep intra deps)
# ---------------------------------------------------------------------------
# NOTE: We copy the ENTIRE agent/ tree, not just selected files.
# orchestrator.py imports from agent.compute, agent.telemetry, agent.collaboration,
# agent.security -- selective copy would miss these and break on import.
# After the full copy, batch_rewrite_imports() rewrites all absolute imports.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def copy_module(src_root: Path, dst_root: Path, module_name: str) -> None:
    src = src_root / module_name
    dst = dst_root / module_name
    if not src.exists():
        print(f"  [WARN]  Skipping {module_name} -- not found at {src}", file=sys.stderr)
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    print(f"  [OK]  {module_name}/")


def copy_file(src: Path, dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    shutil.copy2(src, dst)
    print(f"  [OK]  {src.name}")


def get_vendor_version() -> str:
    """Get the current git commit hash of e:\\AGI."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(AGI_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def patch_silex_config(config_path: Path) -> None:
    """
    Surgical patch to vendor/silex/utils/config.py:
      Replace the single line that sets KRONOS_HOME to ~/.kronos
      with two lines that set CLASP_HOME to ~/.clasp and alias KRONOS_HOME.

    This redirects ALL 20+ downstream paths (SILEX_DB, KRONOS_CONFIG, etc.)
    to ~/.clasp/ without any other code changes.
    """
    if not config_path.exists():
        print(f"  [WARN]  config.py not found at {config_path} -- skipping patch", file=sys.stderr)
        return

    original = config_path.read_text(encoding="utf-8")
    old_line  = 'KRONOS_HOME = Path.home() / ".kronos"'
    new_lines = (
        'CLASP_HOME   = Path.home() / ".clasp"    # Clasp vendor patch\n'
        'KRONOS_HOME  = CLASP_HOME                 # alias -- all downstream paths now point to ~/.clasp'
    )

    if old_line not in original:
        if 'CLASP_HOME' in original:
            print("  [OK]  config.py already patched -- skipping")
        else:
            print(
                f"  [WARN]  Expected line not found in config.py.\n"
                f"     Looking for: {old_line!r}\n"
                f"     Please patch manually.",
                file=sys.stderr
            )
        return

    patched = original.replace(old_line, new_lines, 1)
    config_path.write_text(patched, encoding="utf-8")
    print(f"  [OK]  Patched KRONOS_HOME -> CLASP_HOME in {config_path.name}")


def batch_rewrite_imports(vendor_dir: Path) -> None:
    """
    Rewrite all absolute package imports in the vendored code to use
    the full vendored path.  This is needed because both silex and agent
    were originally top-level packages and use absolute imports internally.

    Rewrites:
      'from silex.'       -> 'from clasp.vendor.silex.'
      'from agent.'       -> 'from clasp.vendor.agent.'
      'import silex.'     -> 'import clasp.vendor.silex.'
      'import agent.'     -> 'import clasp.vendor.agent.'
    """
    import re

    patterns = [
        (re.compile(r'\bfrom silex\.'),   'from clasp.vendor.silex.'),
        (re.compile(r'\bimport silex\.'), 'import clasp.vendor.silex.'),
        (re.compile(r'\bfrom agent\.'),   'from clasp.vendor.agent.'),
        (re.compile(r'\bimport agent\.'), 'import clasp.vendor.agent.'),
    ]

    total_files = 0
    total_replacements = 0

    for f in sorted(vendor_dir.rglob('*.py')):
        if '__pycache__' in str(f):
            continue
        try:
            original = f.read_text(encoding='utf-8')
        except Exception as e:
            print(f"  [WARN]  Could not read {f}: {e}", file=sys.stderr)
            continue

        patched = original
        n_replacements = 0
        for pattern, replacement in patterns:
            new_text = pattern.sub(replacement, patched)
            n_replacements += len(pattern.findall(patched))
            patched = new_text

        if patched != original:
            try:
                f.write_text(patched, encoding='utf-8')
                total_files += 1
                total_replacements += n_replacements
            except Exception as e:
                print(f"  [WARN]  Could not write {f}: {e}", file=sys.stderr)

    print(f"  [OK]  {total_replacements} import rewrites across {total_files} files")


def patch_agent_init(init_path: Path) -> None:
    """
    The original agent/__init__.py uses:
        from agent.orchestrator import WorkerOrchestrator
    This is an absolute import that only works when 'agent' is a top-level package.
    When vendored as clasp.vendor.agent it must use a relative import:
        from .orchestrator import WorkerOrchestrator
    """
    if not init_path.exists():
        print(f"  [WARN]  agent/__init__.py not found at {init_path}", file=sys.stderr)
        return

    content = init_path.read_text(encoding="utf-8")
    old = "from agent.orchestrator import WorkerOrchestrator"
    new = "from .orchestrator import WorkerOrchestrator  # patched: was 'from agent.orchestrator'"

    if old not in content:
        if "from .orchestrator" in content:
            print("  [OK]  agent/__init__.py already patched -- skipping")
        else:
            print("  [WARN]  Expected import line not found in agent/__init__.py -- skipping", file=sys.stderr)
        return

    patched = content.replace(old, new, 1)
    init_path.write_text(patched, encoding="utf-8")
    print("  [OK]  Patched agent/__init__.py to use relative import")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("Clasp -- Phase 0 Vendor Export")
    print("=" * 60)

    if not AGI_ROOT.exists():
        print(f"ERROR: AGI source not found at {AGI_ROOT}", file=sys.stderr)
        sys.exit(1)

    # 1. Ensure vendor root exists and write its __init__.py
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    (VENDOR_DIR / "__init__.py").write_text(
        '"""Vendored Silex + Agent engines. Read-only -- do not modify."""\n',
        encoding="utf-8",
    )

    # 2. Copy silex (selective -- only what Clasp needs)
    print("\n[1/5] Copying silex modules ...")
    silex_vendor = VENDOR_DIR / "silex"
    silex_src    = AGI_ROOT / "silex"
    for mod in SILEX_MODULES:
        copy_module(silex_src, silex_vendor, mod)
    for fname in SILEX_ROOT_FILES:
        src_file = silex_src / fname
        if src_file.exists():
            copy_file(src_file, silex_vendor)
        else:
            print(f"  [WARN]  {fname} not found -- skipping", file=sys.stderr)

    # 3. Copy agent (full package -- intra-package deps are deep)
    print("\n[2/5] Copying agent package (full) ...")
    agent_vendor = VENDOR_DIR / "agent"
    agent_src    = AGI_ROOT / "agent"
    if agent_vendor.exists():
        import shutil as _shutil
        _shutil.rmtree(agent_vendor)
    shutil.copytree(
        agent_src, agent_vendor,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    print(f"  [OK]  agent/ (full copy)")

    # 4. Surgical patches
    print("\n[3/5] Patching vendor/silex/utils/config.py ...")
    patch_silex_config(silex_vendor / "utils" / "config.py")

    # 5. Batch rewrite all absolute silex.* and agent.* imports
    print("\n[4/5] Rewriting absolute imports in vendor/ ...")
    batch_rewrite_imports(VENDOR_DIR)

    # 6. Write VENDOR_VERSION
    print("\n[5/5] Writing VENDOR_VERSION ...")
    version = get_vendor_version()
    version_file = VENDOR_DIR / "VENDOR_VERSION"
    version_file.write_text(
        f"# Auto-generated by scripts/export_engines.py -- do not edit\n"
        f"# Source: {AGI_ROOT}\n"
        f"AGI_COMMIT={version}\n",
        encoding="utf-8",
    )
    print(f"  [OK]  VENDOR_VERSION = {version}")

    print("\n" + "=" * 60)
    print("  Vendor export complete.")
    print(f"   silex -> {silex_vendor}")
    print(f"   agent -> {agent_vendor}")
    print("=" * 60)
    print("\nNext: python -m pytest tests/test_phase0_imports.py -v")




if __name__ == "__main__":
    main()
