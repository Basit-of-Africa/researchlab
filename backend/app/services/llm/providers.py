"""Concrete inference providers. Model names come from configuration only."""
from __future__ import annotations

import logging
import time

import httpx

from app.config import Settings
from app.services.llm.base import LLMError, LLMQuotaExceeded, LLMResult, LLMUnavailable, Message

log = logging.getLogger("researchlab.llm")


def _post_with_retry(url: str, *, json: dict, headers: dict | None, timeout: int, retries: int, provider: str) -> dict:
    delay = 2.0
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = httpx.post(url, json=json, headers=headers or {}, timeout=timeout)
        except httpx.ConnectError as e:
            raise LLMUnavailable(f"{provider} is not reachable: {e}") from e
        except httpx.HTTPError as e:
            last = e
        else:
            if r.status_code == 429:
                # Free-tier quota: retry briefly, then surface a clear quota error.
                last = LLMQuotaExceeded(f"{provider} rate limit or quota exceeded")
            elif r.status_code in (500, 502, 503, 504):
                last = LLMError(f"{provider} server error {r.status_code}")
            elif r.status_code in (401, 403):
                raise LLMUnavailable(f"{provider} rejected the credentials ({r.status_code})")
            elif r.status_code >= 400:
                raise LLMError(f"{provider} request failed ({r.status_code}): {r.text[:300]}")
            else:
                return r.json()
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    if isinstance(last, LLMError):
        raise last
    raise LLMError(f"{provider} request failed: {last}")


class GeminiProvider:
    name = "gemini"
    external = True

    def __init__(self, s: Settings) -> None:
        self.s = s
        self.model = s.gemini_model

    def available(self) -> bool:
        return bool(self.s.gemini_api_key)

    def generate(self, messages, system="", temperature=0.3, max_tokens=4096, json_mode=False) -> LLMResult:
        if not self.available():
            raise LLMUnavailable("GEMINI_API_KEY is not configured")
        body: dict = {
            "contents": [
                {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]} for m in messages
            ],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        data = _post_with_retry(
            f"{self.s.gemini_base_url}/models/{self.model}:generateContent",
            json=body,
            headers={"x-goog-api-key": self.s.gemini_api_key},
            timeout=self.s.llm_timeout_seconds,
            retries=self.s.llm_max_retries,
            provider="Gemini",
        )
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMError(f"Gemini returned no output ({reason})")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        usage = data.get("usageMetadata") or {}
        return LLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            tokens_in=usage.get("promptTokenCount"),
            tokens_out=usage.get("candidatesTokenCount"),
        )


class OllamaProvider:
    name = "ollama"
    external = False  # local inference

    def __init__(self, s: Settings) -> None:
        self.s = s
        self.model = s.ollama_model

    def available(self) -> bool:
        try:
            return httpx.get(f"{self.s.ollama_base_url}/api/tags", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def generate(self, messages, system="", temperature=0.3, max_tokens=4096, json_mode=False) -> LLMResult:
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {"role": m.role, "content": m.content} for m in messages
        ]
        body: dict = {
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_mode:
            body["format"] = "json"
        data = _post_with_retry(
            f"{self.s.ollama_base_url}/api/chat",
            json=body,
            headers=None,
            timeout=self.s.llm_timeout_seconds,
            retries=1,
            provider="Ollama",
        )
        return LLMResult(
            text=(data.get("message") or {}).get("content", ""),
            provider=self.name,
            model=self.model,
            tokens_in=data.get("prompt_eval_count"),
            tokens_out=data.get("eval_count"),
        )


class OpenAICompatProvider:
    """Any OpenAI-compatible chat endpoint (Groq, OpenRouter, LM Studio, vLLM...)."""

    name = "openai_compat"

    def __init__(self, s: Settings) -> None:
        self.s = s
        self.model = s.openai_compat_model
        host = s.openai_compat_base_url
        self.external = not any(h in host for h in ("localhost", "127.0.0.1", "0.0.0.0"))

    def available(self) -> bool:
        return bool(self.s.openai_compat_base_url and self.s.openai_compat_model)

    def generate(self, messages, system="", temperature=0.3, max_tokens=4096, json_mode=False) -> LLMResult:
        if not self.available():
            raise LLMUnavailable("OPENAI_COMPAT_BASE_URL / OPENAI_COMPAT_MODEL not configured")
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {"role": m.role, "content": m.content} for m in messages
        ]
        body: dict = {"model": self.model, "messages": msgs, "temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.s.openai_compat_api_key}"} if self.s.openai_compat_api_key else {}
        data = _post_with_retry(
            self.s.openai_compat_base_url.rstrip("/") + "/chat/completions",
            json=body,
            headers=headers,
            timeout=self.s.llm_timeout_seconds,
            retries=self.s.llm_max_retries,
            provider="OpenAI-compatible endpoint",
        )
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return LLMResult(
            text=(choice.get("message") or {}).get("content", "") or "",
            provider=self.name,
            model=self.model,
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
        )


PROVIDER_CLASSES = {"gemini": GeminiProvider, "ollama": OllamaProvider, "openai_compat": OpenAICompatProvider}
_ = Message
