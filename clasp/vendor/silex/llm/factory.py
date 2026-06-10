from __future__ import annotations

from clasp.vendor.silex.runtime.settings import RuntimeSettingsStore
from clasp.vendor.silex.runtime.usage import UsageTracker
from clasp.vendor.silex.utils.config import get_provider_settings
from clasp.vendor.silex.llm.registry import get_provider_profile, get_provider_client_class


def build_provider(
    settings_store: RuntimeSettingsStore | None = None,
    usage_tracker: UsageTracker | None = None,
):
    active = get_provider_settings(settings_store)
    provider = active["provider"]

    profile = get_provider_profile(provider)
    if not profile:
        raise ValueError(f"No profile found for provider: {provider}")

    client_class = get_provider_client_class(provider)
    if not client_class:
        raise ValueError(f"No client class found for provider: {provider}")

    return client_class(
        provider_profile=profile,
        settings_store=settings_store,
        usage_tracker=usage_tracker,
    )
