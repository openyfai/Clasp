from __future__ import annotations

from typing import Any
from clasp.vendor.silex.llm.registry import list_providers as get_registered_providers

# Dynamically populate MODEL_CATALOG from registered provider profiles on import
MODEL_CATALOG: dict[str, dict[str, Any]] = {}

for profile in get_registered_providers():
    MODEL_CATALOG[profile.name] = {
        "label": profile.display_name,
        "env_key": profile.env_vars[0] if profile.env_vars else "",
        "models": list(profile.fallback_models),
    }
    if profile.base_url:
        MODEL_CATALOG[profile.name]["base_url"] = profile.base_url


def list_providers() -> list[dict[str, Any]]:
    providers = []
    for provider_id, payload in MODEL_CATALOG.items():
        providers.append(
            {
                "id": provider_id,
                "label": payload["label"],
                "env_key": payload.get("env_key", ""),
                "base_url": payload.get("base_url", ""),
                "models": payload["models"],
            }
        )
    return providers


def get_provider_defaults(provider: str) -> dict[str, Any]:
    payload = MODEL_CATALOG.get(provider)
    if not payload:
        raise ValueError(f"Unknown provider: {provider}")
    models = payload.get("models")
    if not models:
        raise ValueError(f"No models defined for provider: {provider}")
    fast_model = next((m for m in models if m.get("tier") == "fast"), models[0])
    reasoning_model = next((m for m in models if m.get("tier") == "reasoning"), models[0])
    fast_id = fast_model["id"]
    reasoning_id = reasoning_model["id"]
    return {
        "provider": provider,
        "model": fast_id,
        "fast_model": fast_id,
        "reasoning_model": reasoning_id,
        "label": payload["label"],
        "env_key": payload.get("env_key", ""),
        "base_url": payload.get("base_url", ""),
    }


def find_model(provider: str, model_id: str) -> dict[str, Any] | None:
    payload = MODEL_CATALOG.get(provider, {})
    return next((model for model in payload.get("models", []) if model["id"] == model_id), None)
