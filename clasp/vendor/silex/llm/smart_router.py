"""
silex/llm/smart_router.py — Multi-provider routing with fallback.

Routing modes:
  auto     → keyword + context-size signals (existing logic, extended)
  speed    → always use the cheapest fast-tier model available
  quality  → always use the best reasoning-tier model available
  local    → force a local Ollama-compatible provider

Fallback chain:
  If the primary provider raises RateLimitError, AuthError, or TimeoutError,
  SmartRouter tries the next provider in the fallback_chain that has a valid key.
"""
from __future__ import annotations
import os
import logging
from typing import Literal

log = logging.getLogger("silex.llm.smart_router")

RoutingMode = Literal["auto", "speed", "quality", "local"]

# Providers tried in order when primary fails.
# Can be overridden by settings.
DEFAULT_FALLBACK_CHAIN = ["gemini", "anthropic", "openai", "ollama"]

# These exception message substrings indicate a retryable provider failure.
_SWITCH_SIGNALS = {"rate limit", "429", "quota", "auth", "401", "403",
                   "invalid api key", "timeout", "connection"}


def _is_switchable_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(sig in msg for sig in _SWITCH_SIGNALS)


class SmartRouter:
    """
    Routes each LLM call to the right provider + model.

    Usage in CognitiveLoop:
        router = SmartRouter(settings_store, usage_tracker)
        provider_instance = await router.get_provider()
        response = await provider_instance.think(system_prompt, user_input)
    """

    def __init__(self, settings_store=None, usage_tracker=None):
        from clasp.vendor.silex.utils.config import get_provider_settings, get_provider_secret
        from clasp.vendor.silex.llm.factory import build_provider
        from clasp.vendor.silex.llm.registry import list_providers

        self._settings_store = settings_store
        self._usage_tracker = usage_tracker
        self._build_provider = build_provider
        self._get_secret = get_provider_secret
        self._list_providers = list_providers

        settings = get_provider_settings(settings_store)
        self._primary_provider_name = settings["provider"]
        self._fast_model = settings["fast_model"]
        self._reasoning_model = settings["reasoning_model"]
        self._mode: RoutingMode = "auto"

        # Cache of instantiated provider clients: {provider_name: client_instance}
        self._clients: dict = {}
        # The primary client (built at startup, same as before)
        self._primary = self._build_provider(settings_store, usage_tracker)
        self._clients[self._primary_provider_name] = self._primary
        self._active_provider = self._primary

    # ── Public API ────────────────────────────────────────────────────────────

    def set_mode(self, mode: RoutingMode) -> None:
        """Set routing mode for this session."""
        self._mode = mode
        log.info("Routing mode set to: %s", mode)

    def set_provider(self, provider_name: str) -> bool:
        """
        Switch the primary provider for this session.
        Returns True if successful, False if provider is unavailable.
        """
        client = self._get_or_build_client(provider_name)
        if client is None:
            return False
        self._primary_provider_name = provider_name
        self._primary = client
        self._active_provider = client
        # Update fast/reasoning models from this provider's catalog
        try:
            from clasp.vendor.silex.llm.catalog import get_provider_defaults
            defaults = get_provider_defaults(provider_name)
            self._fast_model = defaults["fast_model"]
            self._reasoning_model = defaults["reasoning_model"]
        except Exception:
            pass
        log.info("Switched primary provider to: %s", provider_name)
        return True

    def route(self, user_input: str, context_size: int = 0) -> str:
        """Alias for route_model to maintain compatibility with ModelRouter."""
        return self.route_model(user_input, context_size)

    def route_model(self, user_input: str, context_size: int = 0) -> str:
        """
        Returns the model ID to use for this turn based on mode + signals.
        Does NOT switch providers — use get_provider() for that.
        """
        if self._mode == "speed":
            return self._fast_model
        if self._mode == "quality":
            return self._reasoning_model
        if self._mode == "local":
            return self._fast_model  # local providers have their own model names

        # mode == "auto" — keyword + context-size signals (legacy logic preserved)
        lower = user_input.lower()
        reasoning_signals = [
            "architect", "refactor", "debug", "deep dive", "analyze",
            "complex", "plan", "strategy", "why", "logic", "optimize",
            "recursive", "generalize",
        ]
        fast_signals = ["list", "show", "read", "what is", "where is", "hello", "hi", "status"]

        if context_size > 150_000:
            return self._reasoning_model
        if any(s in lower for s in reasoning_signals):
            return self._reasoning_model
        if any(s in lower for s in fast_signals):
            return self._fast_model
        return self._fast_model

    def get_provider(self):
        """Return the active provider client instance."""
        return self._primary

    async def call_with_fallback(self, coro_factory, *args, **kwargs):
        """
        Call an LLM coroutine with automatic provider fallback.

        coro_factory(client) should return an awaitable.
        On switchable errors, tries the next provider in the fallback chain.
        """
        tried = [self._primary_provider_name]
        try:
            return await coro_factory(self._primary)
        except Exception as exc:
            if not _is_switchable_error(exc):
                raise
            log.warning("Provider %s failed (%s), trying fallback chain.",
                        self._primary_provider_name, exc)

        for provider_name in DEFAULT_FALLBACK_CHAIN:
            if provider_name in tried:
                continue
            client = self._get_or_build_client(provider_name)
            if client is None:
                continue
            tried.append(provider_name)
            try:
                log.info("Fallback: trying provider %s", provider_name)
                result = await coro_factory(client)
                log.info("Fallback succeeded with provider %s", provider_name)
                # Temporarily treat fallback as active client to avoid repeatedly hitting failing primary
                self._active_provider = client
                return result
            except Exception as exc2:
                if not _is_switchable_error(exc2):
                    raise
                log.warning("Fallback provider %s also failed: %s", provider_name, exc2)

        raise RuntimeError(
            f"All providers exhausted: {tried}. Check API keys and network."
        )

    def list_available(self) -> list[dict]:
        """
        Return info about all registered providers, indicating which have valid keys.
        Used by /providers command.
        """
        result = []
        for profile in self._list_providers():
            has_key = bool(self._get_secret(profile.name, self._settings_store))
            result.append({
                "name": profile.name,
                "label": profile.display_name,
                "available": has_key,
                "active": profile.name == self._primary_provider_name,
                "fast_model": next(
                    (m["id"] for m in profile.fallback_models if isinstance(m, dict) and m.get("tier") == "fast"),
                    "unknown"
                ),
                "reasoning_model": next(
                    (m["id"] for m in profile.fallback_models if isinstance(m, dict) and m.get("tier") == "reasoning"),
                    "unknown"
                ),
            })
        return result

    def get_proxy(self):
        """Return the routing proxy provider."""
        if not hasattr(self, "_proxy"):
            self._proxy = SmartRouterProxy(self)
        return self._proxy

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_or_build_client(self, provider_name: str):
        """Build and cache a provider client. Returns None if no key available."""
        if provider_name in self._clients:
            return self._clients[provider_name]
        key = self._get_secret(provider_name, self._settings_store)
        if not key:
            log.debug("Skipping provider %s — no API key found.", provider_name)
            return None
        try:
            from clasp.vendor.silex.llm.registry import get_provider_profile, get_provider_client_class
            profile = get_provider_profile(provider_name)
            client_class = get_provider_client_class(provider_name)
            if not profile or not client_class:
                return None
            client = client_class(
                provider_profile=profile,
                settings_store=self._settings_store,
                usage_tracker=self._usage_tracker,
            )
            self._clients[provider_name] = client
            return client
        except Exception as exc:
            log.warning("Failed to build provider %s: %s", provider_name, exc)
            return None


class SmartRouterProxy:
    """Delegates all SupportsLLM protocol calls to the active provider of a SmartRouter."""

    def __init__(self, router: SmartRouter):
        self._router = router

    @property
    def provider_name(self) -> str:
        return self._router._active_provider.provider_name

    @property
    def default_model(self) -> str:
        return self._router._active_provider.default_model

    def connect(self) -> None:
        self._router._active_provider.connect()

    async def complete_json(self, *args, **kwargs):
        return await self._router.call_with_fallback(
            lambda client: client.complete_json(*args, **kwargs)
        )

    async def think(self, *args, **kwargs):
        return await self._router.call_with_fallback(
            lambda client: client.think(*args, **kwargs)
        )

    async def complete_text(self, *args, **kwargs):
        return await self._router.call_with_fallback(
            lambda client: client.complete_text(*args, **kwargs)
        )

