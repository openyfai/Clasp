"""
Dynamic provider registry.
Discovers profiles and client classes from:
1. Built-in plugins: plugins/providers/<name>/
2. User plugins: ~/.kronos/plugins/model-providers/<name>/
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import yaml
from pathlib import Path
from typing import Any

from clasp.vendor.silex.llm.base import ProviderProfile, BaseLLMProvider
from clasp.vendor.silex.utils.config import PROJECT_ROOT, KRONOS_PLUGINS_PROVIDERS

log = logging.getLogger("kronos.providers")

_REGISTRY: dict[str, ProviderProfile] = {}
_CLIENT_CLASSES: dict[str, type[BaseLLMProvider]] = {}
_ALIASES: dict[str, str] = {}
_discovered = False


def register_provider(profile: ProviderProfile, client_class: type[BaseLLMProvider]) -> None:
    """Register a provider profile and its client class by name and aliases."""
    _REGISTRY[profile.name] = profile
    _CLIENT_CLASSES[profile.name] = client_class
    for alias in profile.aliases:
        _ALIASES[alias] = profile.name
    log.debug(f"Registered provider: {profile.name} (aliases: {profile.aliases})")


def get_provider_profile(name: str) -> ProviderProfile | None:
    """Look up a provider profile by name or alias."""
    if not _discovered:
        _discover_providers()
    canonical = _ALIASES.get(name, name)
    return _REGISTRY.get(canonical)


def get_provider_client_class(name: str) -> type[BaseLLMProvider] | None:
    """Look up a provider client class by name or alias."""
    if not _discovered:
        _discover_providers()
    canonical = _ALIASES.get(name, name)
    client_cls = _CLIENT_CLASSES.get(canonical)
    if client_cls:
        return client_cls
    
    # Fallback lookup by api_mode if not directly associated
    profile = _REGISTRY.get(canonical)
    if profile:
        fallback_name = None
        if profile.api_mode == "chat_completions":
            fallback_name = "openai_compat"
        elif profile.api_mode == "gemini_native":
            fallback_name = "gemini"
        elif profile.api_mode == "anthropic_native":
            fallback_name = "anthropic"
            
        if fallback_name:
            return _CLIENT_CLASSES.get(fallback_name)
            
    return None


def list_providers() -> list[ProviderProfile]:
    """Return all registered provider profiles (one per canonical name)."""
    if not _discovered:
        _discover_providers()
    seen: set[int] = set()
    result: list[ProviderProfile] = []
    for profile in _REGISTRY.values():
        pid = id(profile)
        if pid not in seen:
            seen.add(pid)
            result.append(profile)
    return result


def _load_yaml_manifest(path: Path) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Safely load plugin.yaml."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as exc:
        log.warning(f"[Warning]: Failed to parse manifest {path}: {exc}")
        return None


def _import_plugin_client(name: str, client_py: Path) -> type[BaseLLMProvider] | None:
    """Dynamically import client.py and find the BaseLLMProvider subclass."""
    module_name = f"plugins.providers.{name}.client"
    
    if module_name in sys.modules:
        module = sys.modules[module_name]
    else:
        try:
            spec = importlib.util.spec_from_file_location(
                module_name, client_py, submodule_search_locations=[str(client_py.parent)]
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as exc:
            log.warning(
                f"[Warning]: Failed to load custom provider client '{name}' "
                f"due to compile/load error: {exc}"
            )
            sys.modules.pop(module_name, None)
            return None

    # Scan for BaseLLMProvider subclass (exclude BaseLLMProvider itself)
    for obj in module.__dict__.values():
        if isinstance(obj, type) and issubclass(obj, BaseLLMProvider) and obj is not BaseLLMProvider:
            return obj
    return None


def _discover_providers() -> None:
    """Populate the registry dynamically by scanning project and user plugins."""
    global _discovered
    if _discovered:
        return
    _discovered = True

    # Define plugin directories to scan
    scan_dirs: list[tuple[Path, bool]] = []
    
    # 1. Built-in plugins under absolute project root
    builtin_dir = PROJECT_ROOT / "plugins" / "providers"
    if builtin_dir.is_dir():
        scan_dirs.append((builtin_dir, False))

    # 2. User custom plugins
    user_dir = KRONOS_PLUGINS_PROVIDERS
    if user_dir.is_dir():
        scan_dirs.append((user_dir, True))

    for base_dir, is_user in scan_dirs:
        try:
            # Sort directories to ensure consistent load order
            for child in sorted(base_dir.iterdir()):
                if not child.is_dir() or child.name.startswith(("_", ".")):
                    continue
                
                manifest_path = child / "plugin.yaml"
                if not manifest_path.exists():
                    continue

                manifest = _load_yaml_manifest(manifest_path)
                if not manifest:
                    continue

                # Load client class from client.py if it exists
                client_py = child / "client.py"
                client_class = None
                if client_py.exists():
                    client_class = _import_plugin_client(child.name, client_py)

                # Parse profiles
                profiles_data = []
                if isinstance(manifest, dict):
                    if "profiles" in manifest:
                        profiles_data = manifest["profiles"]
                    else:
                        profiles_data = [manifest]
                elif isinstance(manifest, list):
                    profiles_data = manifest

                for p_data in profiles_data:
                    try:
                        # Construct ProviderProfile
                        fallback_models = tuple(p_data.get("fallback_models", []))
                        env_vars = tuple(p_data.get("env_vars", []))
                        aliases = tuple(p_data.get("aliases", []))
                        
                        profile = ProviderProfile(
                            name=p_data["name"],
                            display_name=p_data["display_name"],
                            env_vars=env_vars,
                            base_url=p_data.get("base_url", ""),
                            api_mode=p_data.get("api_mode", "chat_completions"),
                            aliases=aliases,
                            description=p_data.get("description", ""),
                            signup_url=p_data.get("signup_url", ""),
                            models_url=p_data.get("models_url", ""),
                            fallback_models=fallback_models,
                            default_headers=p_data.get("default_headers", {}),
                            fixed_temperature=p_data.get("fixed_temperature", None),
                            default_max_tokens=p_data.get("default_max_tokens", None),
                            default_aux_model=p_data.get("default_aux_model", ""),
                            supports_health_check=p_data.get("supports_health_check", True),
                        )
                        
                        # Resolve client class
                        resolved_client = client_class
                        if not resolved_client:
                            # Search registered classes by api_mode
                            fallback_name = None
                            if profile.api_mode == "chat_completions":
                                fallback_name = "openai_compat"
                            elif profile.api_mode == "gemini_native":
                                fallback_name = "gemini"
                            elif profile.api_mode == "anthropic_native":
                                fallback_name = "anthropic"

                            if fallback_name:
                                resolved_client = _CLIENT_CLASSES.get(fallback_name)

                        if resolved_client:
                            register_provider(profile, resolved_client)
                        else:
                            # Register with a dummy or delay mapping until it's loaded
                            # We can just register with None, since get_provider_client_class handles fallback
                            register_provider(profile, None)
                    except Exception as exc:
                        log.warning(f"Failed to register provider profile from {manifest_path}: {exc}")
        except Exception as exc:
            log.warning(f"Error scanning plugin folder {base_dir}: {exc}")
