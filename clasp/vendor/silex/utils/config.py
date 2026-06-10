"""
Configuration loader for Kronos.

Reads from .env file and provides typed access to all settings.
All runtime data lives under ~/.kronos/
"""

from __future__ import annotations

import os
import shutil
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clasp.vendor.silex.runtime.settings import RuntimeSettingsStore

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Kronos Home paths — single source of truth for all runtime data
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CLASP_HOME   = Path.home() / ".clasp"    # Clasp vendor patch
KRONOS_HOME  = CLASP_HOME                 # alias -- all downstream paths now point to ~/.clasp
SILEX_DB          = KRONOS_HOME / "storage" / "silex.db"
KRONOS_CONFIG     = KRONOS_HOME / "config" / "rules.json"
KRONOS_SECRETS    = KRONOS_HOME / "config" / "secrets.json"
KRONOS_WORKSPACE  = KRONOS_HOME / "workspace"
SILEX_VECTOR_DB   = KRONOS_HOME / "storage" / "vector_db"
KRONOS_SKILLS     = KRONOS_HOME / "skills"
KRONOS_LOGS       = KRONOS_HOME / "logs"
KRONOS_DAEMON_LOG = KRONOS_HOME / "logs" / "daemon.log"
KRONOS_PHANTOM    = KRONOS_HOME / "runtime" / ".phantom"
KRONOS_DAEMON_LOCK = KRONOS_HOME / "runtime" / "daemon.lock"
KRONOS_MANIFEST   = KRONOS_HOME / "workspace" / "workspace_index_manifest.json"
KRONOS_PROCESS_LOCK = KRONOS_HOME / "runtime" / "process.lock"
KRONOS_ONTOLOGY   = KRONOS_HOME / "storage" / "ontology.json"
KRONOS_EXPORTS    = KRONOS_HOME / "workspace" / "exports"
KRONOS_TRACES     = KRONOS_HOME / "logs" / "traces"
KRONOS_PENDING_EDITS = KRONOS_HOME / "workspace" / "pending_edits.json"
KRONOS_BACKUPS    = KRONOS_HOME / "workspace" / "backups"
KRONOS_PLUGINS_PROVIDERS = KRONOS_HOME / "config" / "plugins" / "model-providers"
KRONOS_PLUGINS_TOOLS  = KRONOS_HOME / "plugins" / "tools"
KRONOS_PLUGINS_SKILLS = KRONOS_HOME / "plugins" / "skills"
KRONOS_PERSONA    = KRONOS_HOME / "config" / "persona.yaml"
KRONOS_HMAC_KEY   = KRONOS_HOME / "config" / "hmac_key.bin"


# Legacy aliases kept so existing imports don't break
DATA_DIR   = KRONOS_HOME
DB_PATH    = SILEX_DB


# WORKSPACE: KRONOS_WORKSPACE env > SILEX_WORKSPACE env (backwards compat) > ~/.kronos/workspace
_workspace_env = os.getenv("KRONOS_WORKSPACE") or os.getenv("SILEX_WORKSPACE") or os.getenv("KRONOS_WORKSPACE") or os.getenv("ARIA_WORKSPACE")
if _workspace_env:
    WORKSPACE_DIR = Path(_workspace_env).resolve()
    if not str(WORKSPACE_DIR).startswith(str(KRONOS_WORKSPACE)):
        logging.getLogger("kronos.init").warning(f"SECURITY WARNING: Workspace directory resolved to {WORKSPACE_DIR} outside {KRONOS_WORKSPACE}")
else:
    WORKSPACE_DIR = KRONOS_WORKSPACE

KRONOS_DIRECTIVES_FILE = WORKSPACE_DIR / "kronos_core_directives.md"

_kronos_home_ensured = False

def ensure_kronos_home() -> None:
    """Kept alias for backwards compatibility."""
    ensure_kronos_home()

def ensure_kronos_home() -> None:
    global _kronos_home_ensured
    if _kronos_home_ensured:
        return
    _kronos_home_ensured = True

    log = logging.getLogger("kronos.init")

    # 1. Create base directory structure first
    KRONOS_HOME.mkdir(exist_ok=True)
    (KRONOS_HOME / "storage").mkdir(exist_ok=True)
    (KRONOS_HOME / "config").mkdir(exist_ok=True)
    (KRONOS_HOME / "workspace").mkdir(exist_ok=True)
    (KRONOS_HOME / "runtime").mkdir(exist_ok=True)
    (KRONOS_HOME / "logs").mkdir(exist_ok=True)
    KRONOS_SKILLS.mkdir(exist_ok=True)

    # 2. Automatic migration from ~/.vyn to ~/.kronos
    legacy_vyn_home = Path.home() / ".vyn"
    if legacy_vyn_home.exists() and not (KRONOS_HOME / "storage" / "silex.db").exists():
        try:
            # Copy all files from ~/.vyn to ~/.kronos/ config/storage folders
            for item in legacy_vyn_home.iterdir():
                if item.name == "vyn.db":
                    shutil.copy2(item, SILEX_DB)
                elif item.name == "vyn.db-wal":
                    shutil.copy2(item, KRONOS_HOME / "storage" / "silex.db-wal")
                elif item.name == "vyn.db-shm":
                    shutil.copy2(item, KRONOS_HOME / "storage" / "silex.db-shm")
                elif item.name == "ontology.json":
                    shutil.copy2(item, KRONOS_ONTOLOGY)
                elif item.name in ("config.json", "settings.json"):
                    shutil.copy2(item, KRONOS_CONFIG)
                elif item.name == "secrets.json":
                    shutil.copy2(item, KRONOS_SECRETS)
                elif item.name == "persona.yaml":
                    shutil.copy2(item, KRONOS_PERSONA)
                elif item.name == "vector_db" or item.name == "memory":
                    src_v = item / "vector_db" if item.name == "memory" else item
                    if src_v.exists():
                        shutil.copytree(src_v, SILEX_VECTOR_DB, dirs_exist_ok=True)
                elif item.is_dir():
                    # Generic copy fallback
                    dest_dir = KRONOS_HOME / "config" if item.name == "plugins" else KRONOS_HOME / "workspace" / item.name
                    shutil.copytree(item, dest_dir, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, KRONOS_HOME / "config" / item.name)
            log.info("✓ Successfully migrated existing profile from ~/.vyn to new ~/.kronos layout")
        except Exception as e:
            log.error(f"Failed to migrate legacy ~/.vyn to ~/.kronos: {e}")

    # 3. Internal migrations from legacy flat ~/.kronos folder to structured layout
    def safe_migrate_file(old_path: Path, new_path: Path) -> None:
        if old_path.exists() and not new_path.exists():
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                old_path.rename(new_path)
                log.info(f"Migrated file {old_path.name} to {new_path}")
            except Exception as e:
                log.error(f"Failed to migrate file {old_path.name}: {e}")

    def safe_migrate_dir(old_path: Path, new_path: Path) -> None:
        if old_path.exists() and old_path.is_dir() and not new_path.exists():
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_path), str(new_path))
                log.info(f"Migrated directory {old_path.name} to {new_path}")
            except Exception as e:
                log.error(f"Failed to migrate directory {old_path.name}: {e}")

    # Relocate SQLite files
    safe_migrate_file(KRONOS_HOME / "silex.db", SILEX_DB)
    safe_migrate_file(KRONOS_HOME / "silex.db-wal", KRONOS_HOME / "storage" / "silex.db-wal")
    safe_migrate_file(KRONOS_HOME / "silex.db-shm", KRONOS_HOME / "storage" / "silex.db-shm")
    safe_migrate_file(KRONOS_HOME / "ontology.json", KRONOS_ONTOLOGY)

    # Relocate configuration files
    safe_migrate_file(KRONOS_HOME / "config.json", KRONOS_CONFIG)
    safe_migrate_file(KRONOS_HOME / "secrets.json", KRONOS_SECRETS)
    safe_migrate_file(KRONOS_HOME / "persona.yaml", KRONOS_PERSONA)

    # Relocate workspace metadata
    safe_migrate_file(KRONOS_HOME / "workspace_index_manifest.json", KRONOS_MANIFEST)
    safe_migrate_file(KRONOS_HOME / "pending_edits.json", KRONOS_PENDING_EDITS)

    # Relocate folders
    safe_migrate_dir(KRONOS_HOME / "memory" / "vector_db", SILEX_VECTOR_DB)
    safe_migrate_dir(KRONOS_HOME / "vector_db", SILEX_VECTOR_DB)
    if (KRONOS_HOME / "memory").exists() and not any((KRONOS_HOME / "memory").iterdir()):
        try:
            (KRONOS_HOME / "memory").rmdir()
        except OSError:
            pass

    safe_migrate_dir(KRONOS_HOME / "plugins", KRONOS_HOME / "config" / "plugins")
    safe_migrate_dir(KRONOS_HOME / "exports", KRONOS_EXPORTS)
    safe_migrate_dir(KRONOS_HOME / "backups", KRONOS_BACKUPS)
    safe_migrate_dir(KRONOS_HOME / "traces", KRONOS_TRACES)

    # 4. Ensure all directories are created
    SILEX_VECTOR_DB.mkdir(parents=True, exist_ok=True)
    KRONOS_SKILLS.mkdir(exist_ok=True)
    KRONOS_LOGS.mkdir(exist_ok=True)
    KRONOS_PLUGINS_TOOLS.mkdir(parents=True, exist_ok=True)
    KRONOS_PLUGINS_SKILLS.mkdir(parents=True, exist_ok=True)
    KRONOS_TRACES.mkdir(parents=True, exist_ok=True)
    KRONOS_BACKUPS.mkdir(parents=True, exist_ok=True)
    KRONOS_PLUGINS_PROVIDERS.mkdir(parents=True, exist_ok=True)

    # 5. Skills README + seed bundled skills from the package checkout
    readme_path = KRONOS_SKILLS / "README.md"
    if not any(KRONOS_SKILLS.iterdir()) or not readme_path.exists():
        readme_path.write_text(
            "Add .md files to this directory to extend Kronos with new skills.\n"
            "Each file should describe a workflow or capability.\n"
            "Restart Kronos after adding a skill for it to take effect.\n",
            encoding="utf-8"
        )

    bundled_skills_dir = PROJECT_ROOT / "skills"
    if bundled_skills_dir.is_dir():
        for src in bundled_skills_dir.glob("*.md"):
            if src.stem.lower() == "readme":
                continue
            dest = KRONOS_SKILLS / src.name
            if not dest.exists():
                try:
                    shutil.copy2(src, dest)
                except OSError as exc:
                    log.debug("Could not seed skill %s: %s", src.name, exc)

    if not KRONOS_DIRECTIVES_FILE.exists():
        KRONOS_DIRECTIVES_FILE.write_text(
            "# Kronos Core Directives\n\n"
            "This file contains unbreakable rules and behavioral guidelines. "
            "Any instructions here override general knowledge and normal operating procedures.\n",
            encoding="utf-8"
        )

    # 6. Phantom Cleanup
    if KRONOS_PHANTOM.exists():
        try:
            shutil.rmtree(KRONOS_PHANTOM)
            log.info("Cleaned up leftover phantom directory from previous crash")
        except Exception as e:
            log.error(f"Failed to clean up phantom directory: {e}")

    # 7. Migrate old data from legacy project folders (e.g. data/aria.db)
    old_data_dir = PROJECT_ROOT / "data"
    old_db = old_data_dir / "aria.db"
    
    if old_db.exists() and not SILEX_DB.exists():
        shutil.copy2(old_db, SILEX_DB)
        log.info("Migrated existing database to ~/.kronos/storage/silex.db")
    elif old_db.exists() and SILEX_DB.exists():
        log.warning("WARNING: Both old database (data/aria.db) and new database (~/.kronos/storage/silex.db) exist. Using new database.")

    old_vector_db1 = old_data_dir / "vector_db"
    old_vector_db2 = KRONOS_HOME / "vector_db"
    for old_v in [old_vector_db1, old_vector_db2]:
        if old_v.exists() and old_v.is_dir():
            if not any(SILEX_VECTOR_DB.iterdir()):
                shutil.copytree(old_v, SILEX_VECTOR_DB, dirs_exist_ok=True)
                log.info(f"Migrated existing ChromaDB from {old_v} to {SILEX_VECTOR_DB}")
            break

    # Migrate settings/secrets
    old_settings = [old_data_dir / "settings.json", KRONOS_HOME / "settings.json"]
    for osg in old_settings:
        if osg.exists() and not KRONOS_CONFIG.exists():
            shutil.copy2(osg, KRONOS_CONFIG)
            log.info(f"Migrated settings from {osg} to {KRONOS_CONFIG}")
            break
            
    old_secrets = [old_data_dir / "secrets.json", KRONOS_HOME / "secrets.json"]
    for os_sec in old_secrets:
        if os_sec.exists() and not KRONOS_SECRETS.exists():
            shutil.copy2(os_sec, KRONOS_SECRETS)
            log.info(f"Migrated secrets from {os_sec} to {KRONOS_SECRETS}")
            break

    # Secrets permission
    if not KRONOS_SECRETS.exists():
        KRONOS_SECRETS.write_text("{}", encoding="utf-8")
        
    if os.name != "nt":
        try:
            os.chmod(KRONOS_SECRETS, 0o600)
        except OSError:
            pass
    else:
        log.warning("WARNING: secrets.json has no file permission protection on Windows. Store API keys as environment variables for better security.")

    # 8. Generate HMAC key for plugin signature verification (once)
    if not KRONOS_HMAC_KEY.exists():
        try:
            import secrets
            KRONOS_HMAC_KEY.write_bytes(secrets.token_bytes(32))
            if os.name != "nt":
                os.chmod(KRONOS_HMAC_KEY, 0o600)
            log.info("Generated plugin HMAC key at ~/.kronos/config/hmac_key.bin")
        except Exception as e:
            log.error(f"Failed to generate HMAC key: {e}")

    # 9. Ensure persona.yaml exists
    if not KRONOS_PERSONA.exists():
        import yaml
        default_persona = {
            "agent_name": "Kronos",
            "engine_name": "SILEX",
            "primary_brand": "Kronos (λ)",
            "personality_archetype": "Sovereign CLI Development Engine",
            "tone_modifiers": [
                "Direct, sharp, and technically flawless.",
                "Gives raw engineering facts, completely avoiding polite fluff."
            ],
            "custom_greeting": "🧠 SILEX memory core active. Kronos CLI operational. Systems are 100% green."
        }
        try:
            with open(KRONOS_PERSONA, "w", encoding="utf-8") as f:
                yaml.safe_dump(default_persona, f, sort_keys=False, allow_unicode=True)
            log.info("Initialized default persona.yaml at ~/.kronos/config/persona.yaml")
        except Exception as e:
            log.error(f"Failed to initialize default persona.yaml: {e}")

    # 10. Enforce Absolute Path Warning for WSL mounts (/mnt/)
    if str(WORKSPACE_DIR).startswith("/mnt/"):
        import sys
        sys.stderr.write(
            "\033[90m⚠️  Warning: Running Kronos on virtualized Windows mounts (/mnt/...) "
            "severely impacts filesystem monitoring latency.\n"
            "   It is highly advised to move your files to the native Linux volume structure "
            "for optimal execution speeds.\033[0m\n"
        )

ensure_kronos_home()


def load_persona_config() -> dict:
    """Load the persona configuration from ~/.kronos/persona.yaml."""
    ensure_kronos_home()
    if not KRONOS_PERSONA.exists():
        return {
            "agent_name": "Kronos",
            "engine_name": "SILEX",
            "primary_brand": "Kronos (λ)",
            "personality_archetype": "Sovereign CLI Development Engine",
            "tone_modifiers": [
                "Direct, sharp, and technically flawless.",
                "Gives raw engineering facts, completely avoiding polite fluff."
            ],
            "custom_greeting": "🧠 SILEX memory core active. Kronos CLI operational. Systems are 100% green."
        }
    import yaml
    try:
        with open(KRONOS_PERSONA, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logging.getLogger("kronos.init").error(f"Failed to load persona.yaml: {e}")
        return {}

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

# Load .env from project root, then ~/.kronos/.env (installer default location)
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    load_dotenv(_env_file)
_kronos_env = KRONOS_HOME / ".env"
if _kronos_env.exists():
    load_dotenv(_kronos_env, override=True)

_settings_store = None

def get_settings_store() -> "RuntimeSettingsStore":
    global _settings_store
    if _settings_store is None:
        from clasp.vendor.silex.runtime.settings import RuntimeSettingsStore
        _settings_store = RuntimeSettingsStore()
    return _settings_store


def get_provider_settings(settings_store = None) -> dict:
    store = settings_store or get_settings_store()
    saved = store.load_settings()
    
    # Priority: Env Var > Saved Settings > Hardcoded Default
    provider = os.getenv("SILEX_PROVIDER") or os.getenv("ARIA_PROVIDER") or saved.get("provider", "gemini")
    model = os.getenv("SILEX_MODEL") or os.getenv("ARIA_MODEL") or saved.get("model", "gemini-3.1-flash-lite")
    
    if provider == "custom":
        model = saved.get("model", model)
        
    fast_model = os.getenv("SILEX_FAST_MODEL") or os.getenv("ARIA_FAST_MODEL") or saved.get("fast_model", model)
    reasoning_model = os.getenv("SILEX_REASONING_MODEL") or os.getenv("ARIA_REASONING_MODEL") or saved.get("reasoning_model", fast_model)
    critic_model = os.getenv("SILEX_CRITIC_MODEL") or os.getenv("ARIA_CRITIC_MODEL") or saved.get("critic_model", reasoning_model)
    
    return {
        "provider": provider,
        "model": model,
        "fast_model": fast_model,
        "reasoning_model": reasoning_model,
        "critic_model": critic_model,
        "base_url": saved.get("base_url", ""),
    }


def get_provider_secret(provider_id: str, settings_store = None) -> str | None:
    store = settings_store or get_settings_store()
    stored = store.get_provider_secret(provider_id)
    if stored:
        return stored

    from clasp.vendor.silex.llm.registry import get_provider_profile
    profile = get_provider_profile(provider_id)
    if not profile or not profile.env_vars:
        return ""
    env_name = profile.env_vars[0]
    value = os.getenv(env_name, "")
    if not value or value.endswith("_here"):
        return ""
    return value


def get_search_secret(provider_id: str, settings_store = None) -> str:
    store = settings_store or get_settings_store()
    stored = store.get_provider_secret(provider_id)
    if stored:
        return stored

    # Env fallbacks
    env_map = {
        "tavily": "TAVILY_API_KEY",
        "brave": "BRAVE_API_KEY"
    }
    env_var = env_map.get(provider_id.lower())
    if env_var:
        val = os.getenv(env_var, "")
        if val and not val.endswith("_here"):
            return val
    return ""


def get_api_key() -> str:
    """Backward-compatible provider key lookup."""
    provider = get_provider_settings()["provider"]
    key = get_provider_secret(provider)
    if key:
        return key
    raise EnvironmentError(
        f"{provider} API key is not set.\n"
        "Run `kronos setup`, use the web onboarding flow, or configure the matching env var."
    )


def get_model() -> str:
    """Get the active model."""
    return get_provider_settings()["model"]


def get_log_level() -> str:
    """Get the logging level."""
    return (os.getenv("SILEX_LOG_LEVEL") or os.getenv("ARIA_LOG_LEVEL") or "INFO").upper()


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean feature flag from the environment."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _saved_security_flag(name: str, default: bool) -> bool:
    settings = get_settings_store().load_settings()
    return bool(settings.get("security", {}).get(name, default))


def terminal_execution_enabled() -> bool:
    """Whether Kronos may run sandboxed terminal commands."""
    return (env_flag("SILEX_ENABLE_TERMINAL_EXECUTION") or 
            env_flag("ARIA_ENABLE_TERMINAL_EXECUTION", _saved_security_flag("terminal_execution", False)))


def code_apply_enabled() -> bool:
    """Whether Kronos may apply code edits without a human approval step."""
    return (env_flag("SILEX_ENABLE_CODE_APPLY") or 
            env_flag("ARIA_ENABLE_CODE_APPLY", _saved_security_flag("code_apply", False)))


def browser_actions_enabled() -> bool:
    """Whether Kronos may use the browser automation tool."""
    return (env_flag("SILEX_ENABLE_BROWSER_ACTIONS") or 
            env_flag("ARIA_ENABLE_BROWSER_ACTIONS", _saved_security_flag("browser_actions", True)))


def background_actions_enabled() -> bool:
    """Whether Kronos may wake itself up to work on active goals."""
    return (env_flag("SILEX_ENABLE_BACKGROUND_LOOP") or 
            env_flag("ARIA_ENABLE_BACKGROUND_LOOP", _saved_security_flag("background_actions", False)))


def require_tool_approvals() -> bool:
    """Whether high-risk tools should enter a pending approval queue."""
    return (env_flag("SILEX_REQUIRE_TOOL_APPROVALS") or 
            env_flag("ARIA_REQUIRE_TOOL_APPROVALS", _saved_security_flag("require_tool_approvals", True)))


def max_tool_calls_per_turn() -> int:
    """Hard ceiling for model-requested tool calls in a single turn."""
    raw = os.getenv("SILEX_MAX_TOOL_CALLS_PER_TURN") or os.getenv("ARIA_MAX_TOOL_CALLS_PER_TURN", "8")
    try:
        return max(1, int(raw))
    except ValueError:
        return 8


def get_process_role() -> str:
    """Identify this process for single-writer deployment checks."""
    return os.getenv("SILEX_PROCESS_ROLE") or os.getenv("ARIA_PROCESS_ROLE", "standalone")


def allow_multi_writer() -> bool:
    """Whether multiple Kronos processes may share a data directory."""
    return env_flag("SILEX_ALLOW_MULTI_WRITER") or env_flag("ARIA_ALLOW_MULTI_WRITER", False)


def telegram_public_mode_enabled() -> bool:
    settings_value = bool(_settings_store.load_settings().get("telegram", {}).get("public_mode", False))
    return env_flag("TELEGRAM_PUBLIC_MODE", settings_value)


def autonomy_policy_snapshot() -> dict:
    """Operator-facing summary of the active autonomy policy."""
    return {
        "terminal_execution": terminal_execution_enabled(),
        "code_apply": code_apply_enabled(),
        "browser_actions": browser_actions_enabled(),
        "background_actions": background_actions_enabled(),
        "require_tool_approvals": require_tool_approvals(),
        "max_tool_calls_per_turn": max_tool_calls_per_turn(),
        "process_role": get_process_role(),
        "provider": get_provider_settings()["provider"],
        "model": get_provider_settings()["model"],
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Memory retrieval budget per turn
MAX_RECENT_MEMORIES = 5
MAX_IMPORTANT_MEMORIES = 5
MAX_RELEVANT_MEMORIES = 5

# Conversation context
MAX_HISTORY_TURNS = 10

# Memory pruning
MEMORY_ARCHIVE_THRESHOLD = 0.1  # Importance below this gets archived eventually
MEMORY_MAX_AGE_DAYS = 365       # For future use

# ---------------------------------------------------------------------------
# Epistemic Memory Orchestration Constants
# ---------------------------------------------------------------------------

# A-MAC (Adaptive Memory Admission Control)
AMAC_THRESHOLD = float(os.getenv("KRONOS_AMAC_THRESHOLD", "0.40"))
AMAC_WEIGHTS = [0.1, 0.1, 0.1, 0.1, 0.6]  # utility, confidence, novelty, recency, type_prior

# Bayesian Trust Engine
TRUST_CUTOFF = float(os.getenv("KRONOS_TRUST_CUTOFF", "0.50"))
TRUST_FLOOR = float(os.getenv("KRONOS_TRUST_FLOOR", "0.30"))

# MemoryGuard Middleware
MEMORY_GUARD_STRICT = env_flag("KRONOS_MEMORY_GUARD_STRICT", False)
