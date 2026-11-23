"""Provider selection, fallback and the external-data policy gate.

The researcher's data policy (``ai_data_policy`` setting) controls whether
*private/unpublished* material may be sent to providers that run outside this
machine. Callers pass ``contains_private=True`` when the prompt includes such
material; external providers are then skipped unless permitted.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from app.config import get_settings
from app.services.llm.base import LLMError, LLMProvider, LLMQuotaExceeded, LLMResult, LLMUnavailable, Message
from app.services.llm.providers import PROVIDER_CLASSES

log = logging.getLogger("researchlab.llm")

# Tests (and only tests) may register an in-process provider here.
_override: Callable[[], list[LLMProvider]] | None = None


def set_provider_override(factory: Callable[[], list[LLMProvider]] | None) -> None:
    global _override
    _override = factory


def configured_providers(preferred: list[str] | None = None) -> list[LLMProvider]:
    if _override is not None:
        return _override()
    s = get_settings()
    names = preferred or s.llm_provider_list
    out: list[LLMProvider] = []
    for n in names:
        cls = PROVIDER_CLASSES.get(n)
        if cls:
            out.append(cls(s))
    return out


def provider_status() -> list[dict]:
    rows = []
    for p in configured_providers():
        try:
            ok = p.available()
        except Exception:  # pragma: no cover - defensive
            ok = False
        rows.append({"name": p.name, "model": p.model, "external": p.external, "available": ok})
    return rows


def generate(
    messages: list[Message] | str,
    *,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4096,
    json_mode: bool = False,
    contains_private: bool = False,
    allow_external_private: bool = False,
    preferred: list[str] | None = None,
) -> LLMResult:
    if isinstance(messages, str):
        messages = [Message("user", messages)]
    errors: list[str] = []
    providers = configured_providers(preferred)
    if not providers:
        raise LLMUnavailable("No AI provider is configured. Set LLM_PROVIDERS and the provider credentials.")
    for p in providers:
        if contains_private and p.external and not allow_external_private:
            errors.append(f"{p.name}: skipped — private material may not be sent to external providers (data policy)")
            continue
        if not p.available():
            hint = {"gemini": "GEMINI_API_KEY is not set", "ollama": f"Ollama not reachable at {get_settings().ollama_base_url}",
                    "openai_compat": "OPENAI_COMPAT_BASE_URL / OPENAI_COMPAT_MODEL not set"}.get(p.name, "not available")
            errors.append(f"{p.name}: {hint}")
            continue
        try:
            return p.generate(messages, system=system, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode)
        except LLMQuotaExceeded as e:
            errors.append(f"{p.name}: {e}")
            log.warning("LLM quota exceeded on %s, trying fallback", p.name)
        except LLMUnavailable as e:
            errors.append(f"{p.name}: {e}")
        except LLMError as e:
            errors.append(f"{p.name}: {e}")
            log.warning("LLM error on %s: %s", p.name, e)
    raise LLMUnavailable("No AI provider could complete the request. " + " | ".join(errors))
