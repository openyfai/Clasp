"""
LLM provider interfaces and shared helpers.

Shared utilities (v1.0.5):
  - retry_on_transient: exponential-backoff decorator for transient API errors.
  - repair_json: strips markdown fences and common LLM JSON formatting artifacts.
  Both are used by ALL providers, not just Gemini.
"""

from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from clasp.vendor.silex.models.schemas import CognitiveResponse
from clasp.vendor.silex.utils.logger import setup_logger

log = setup_logger("silex.llm.base")

SchemaT = TypeVar("SchemaT", bound=BaseModel)


# ---------------------------------------------------------------------------
# Shared: Retry decorator for transient API errors
# ---------------------------------------------------------------------------

_TRANSIENT_ERROR_CODES = {"503", "429", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED"}


def _is_transient(error: Exception) -> bool:
    """Check if an exception is a transient API error worth retrying."""
    error_str = str(error)
    return any(code in error_str for code in _TRANSIENT_ERROR_CODES)


def retry_on_transient(max_retries: int = 3, base_delay: float = 1.0):
    """
    Decorator that retries async functions on transient API errors.
    Uses exponential backoff with jitter.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if _is_transient(e) and attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        log.warning(
                            f"Transient API error (attempt {attempt + 1}/{max_retries}), "
                            f"retrying in {delay:.1f}s: {e}"
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise
            raise last_error  # Should never reach here, but safety net
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Shared: JSON repair for non-compliant LLM output
# ---------------------------------------------------------------------------

_MARKDOWN_JSON_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def repair_json(raw: str) -> str:
    """Attempt to extract clean JSON from LLM output that may include
    markdown fences, leading/trailing prose, or other formatting artifacts.

    Returns the cleaned string (still needs json.loads/pydantic validation).
    """
    text = raw.strip()

    # 1. Strip markdown code fences: ```json ... ``` or ``` ... ```
    match = _MARKDOWN_JSON_RE.search(text)
    if match:
        text = match.group(1).strip()

    # 2. If the string starts with prose before the JSON object/array,
    #    find the first { or [ and take everything from there.
    if text and text[0] not in ('{', '['):
        for i, ch in enumerate(text):
            if ch in ('{', '['):
                text = text[i:]
                break

    # 3. If the string ends with prose after the JSON, find the last } or ]
    if text and text[-1] not in ('}', ']'):
        for i in range(len(text) - 1, -1, -1):
            if text[i] in ('}', ']'):
                text = text[:i + 1]
                break

    return text


# ---------------------------------------------------------------------------
# Provider base class
# ---------------------------------------------------------------------------

class SupportsLLM(Protocol):
    provider_name: str
    default_model: str

    def connect(self) -> None:
        ...

    async def complete_json(
        self,
        *,
        schema: type[SchemaT],
        system_prompt: str,
        user_input: str,
        images: list[dict] | None = None,
        model_override: str | None = None,
        temperature: float = 0.7,
        request_kind: str = "chat",
    ) -> SchemaT:
        ...

    async def think(
        self,
        system_prompt: str,
        user_input: str,
        images: list[dict] | None = None,
        model_override: str | None = None,
    ) -> CognitiveResponse:
        ...


class BaseLLMProvider(ABC):
    provider_name = "unknown"

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        import inspect
        if cls.__name__ not in ("BaseLLMProvider",) and "mock" not in cls.__name__.lower() and "test" not in cls.__name__.lower():
            if "__init__" in cls.__dict__:
                sig = inspect.signature(cls.__init__)
                non_self_params = [p for p in sig.parameters.values() if p.name != "self"]
                
                if len(non_self_params) < 3:
                    raise TypeError(
                        f"Subclass '{cls.__name__}' must conform to standardized constructor signature: "
                        f"__init__(self, provider_profile, settings_store=None, usage_tracker=None). "
                        f"Found too few parameters: {list(sig.parameters.keys())}"
                    )
                
                p0, p1, p2 = non_self_params[:3]
                is_valid = (
                    ("profile" in p0.name.lower()) and
                    ("settings" in p1.name.lower() or "store" in p1.name.lower()) and
                    ("usage" in p2.name.lower() or "tracker" in p2.name.lower())
                )
                
                if not is_valid:
                    raise TypeError(
                        f"Subclass '{cls.__name__}' must conform to standardized constructor signature: "
                        f"__init__(self, provider_profile, settings_store=None, usage_tracker=None). "
                        f"Found signature: {sig}"
                    )
                    
                for param in non_self_params[3:]:
                    if param.default == inspect.Parameter.empty:
                        raise TypeError(
                            f"Subclass '{cls.__name__}' must conform to standardized constructor signature. "
                            f"Found additional required parameter '{param.name}' without a default value."
                        )

    def __init__(self, default_model: str):
        self.default_model = default_model
        
        # Dynamically wrap complete_json with caching
        original_complete_json = self.complete_json
        
        async def wrapped_complete_json(
            *,
            schema: type[SchemaT],
            system_prompt: str,
            user_input: str,
            images: list[dict] | None = None,
            model_override: str | None = None,
            temperature: float = 0.7,
            request_kind: str = "chat",
        ) -> SchemaT:
            return await self._cached_complete_json(
                original_complete_json,
                schema=schema,
                system_prompt=system_prompt,
                user_input=user_input,
                images=images,
                model_override=model_override,
                temperature=temperature,
                request_kind=request_kind,
            )
        
        self.complete_json = wrapped_complete_json

    async def _cached_complete_json(
        self,
        original_complete_json,
        *,
        schema: type[SchemaT],
        system_prompt: str,
        user_input: str,
        images: list[dict] | None = None,
        model_override: str | None = None,
        temperature: float = 0.7,
        request_kind: str = "chat",
    ) -> SchemaT:
        # Resolve DB from usage tracker
        db = None
        if hasattr(self, "_usage_tracker") and self._usage_tracker:
            db = self._usage_tracker.db
            
        if not db:
            return await original_complete_json(
                schema=schema,
                system_prompt=system_prompt,
                user_input=user_input,
                images=images,
                model_override=model_override,
                temperature=temperature,
                request_kind=request_kind,
            )

        import hashlib
        from datetime import datetime, timezone

        # 3. Hashing
        hash_input = f"{system_prompt}||{user_input}||{schema.__name__}"
        query_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

        # 4. Retrieval & TTL (15 minutes = 900 seconds)
        try:
            cached_row = await db.fetch_one(
                "SELECT response, created_at FROM response_cache WHERE query_hash = ?",
                (query_hash,)
            )
            if cached_row:
                cached_response = cached_row["response"]
                created_at_str = cached_row["created_at"]
                
                created_at = datetime.fromisoformat(created_at_str)
                now = datetime.now(timezone.utc)
                age = (now - created_at).total_seconds()
                
                if age <= 900:
                    log.info("Semantic Response Cache HIT! (Age: %.1fs)", age)
                    return self.parse_model_json(schema, cached_response)
                else:
                    log.debug("Cache hit but expired (Age: %.1fs)", age)
        except Exception as e:
            log.warning("Failed to check response cache: %s", e)

        # 5. Storage (on cache miss)
        result = await original_complete_json(
            schema=schema,
            system_prompt=system_prompt,
            user_input=user_input,
            images=images,
            model_override=model_override,
            temperature=temperature,
            request_kind=request_kind,
        )

        try:
            if isinstance(result, BaseModel):
                json_str = result.model_dump_json()
            else:
                json_str = json.dumps(result)

            now_str = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "INSERT OR REPLACE INTO response_cache (query_hash, response, created_at) VALUES (?, ?, ?)",
                (query_hash, json_str, now_str)
            )
            log.debug("Stored response in Semantic Response Cache.")
        except Exception as e:
            log.warning("Failed to save response to cache: %s", e)

        return result

    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def complete_json(
        self,
        *,
        schema: type[SchemaT],
        system_prompt: str,
        user_input: str,
        images: list[dict] | None = None,
        model_override: str | None = None,
        temperature: float = 0.7,
        request_kind: str = "chat",
    ) -> SchemaT:
        raise NotImplementedError

    async def complete_text(
        self,
        prompt: str,
        model_override: str | None = None,
        temperature: float = 0.3,
    ) -> str:
        """
        Plain-text completion without JSON schema enforcement.

        Default implementation wraps complete_json with a minimal schema.
        Providers can override this for a more efficient raw call.
        """
        from pydantic import BaseModel as _BaseModel

        class _TextResult(_BaseModel):
            text: str

        result = await self.complete_json(
            schema=_TextResult,
            system_prompt="Respond with only the requested content, no commentary.",
            user_input=prompt,
            model_override=model_override,
            temperature=temperature,
            request_kind="compression",
        )
        return result.text

    async def think(
        self,
        system_prompt: str,
        user_input: str,
        images: list[dict] | None = None,
        model_override: str | None = None,
    ) -> CognitiveResponse:
        return await self.complete_json(
            schema=CognitiveResponse,
            system_prompt=system_prompt,
            user_input=user_input,
            images=images,
            model_override=model_override,
            temperature=0.7,
            request_kind="chat",
        )

    @staticmethod
    def parse_model_json(schema: type[SchemaT], payload: str | dict[str, Any]) -> SchemaT:
        if isinstance(payload, str):
            try:
                return schema.model_validate_json(payload)
            except Exception:
                return schema.model_validate(json.loads(payload))
        return schema.model_validate(payload)


# Sentinel for "omit temperature entirely"
OMIT_TEMPERATURE = object()


@dataclass
class ProviderProfile:
    """Base provider profile — subclass or instantiate with overrides."""

    # Identity
    name: str
    display_name: str
    env_vars: tuple[str, ...]
    base_url: str = ""
    api_mode: str = "chat_completions"  # "chat_completions", "gemini_native", "anthropic_native"
    aliases: tuple[str, ...] = ()

    # Metadata
    description: str = ""
    signup_url: str = ""
    models_url: str = ""

    # Model Catalog
    fallback_models: tuple[dict[str, Any], ...] = ()

    # Client-level quirks
    default_headers: dict[str, str] = field(default_factory=dict)

    # Request-level quirks
    fixed_temperature: Any = None
    default_max_tokens: int | None = None
    default_aux_model: str = ""
    supports_health_check: bool = True

    def get_hostname(self) -> str:
        """Return the provider's base hostname for URL-based detection."""
        if self.base_url:
            from urllib.parse import urlparse
            return urlparse(self.base_url).hostname or ""
        return ""

    def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Provider-specific message preprocessing hook."""
        return messages

    def build_extra_body(self, **context: Any) -> dict[str, Any]:
        """Provider-specific extra_body fields hook."""
        return {}

    def build_api_kwargs_extras(self, **context: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        """Provider-specific split between extra_body and top-level api_kwargs.
        Returns (extra_body_additions, top_level_kwargs).
        """
        return {}, {}
