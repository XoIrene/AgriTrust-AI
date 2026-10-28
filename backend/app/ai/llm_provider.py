"""LLM provider abstraction: one class, one public method, provider from env.

``LLMProvider`` reads ``LLM_PROVIDER`` (through pydantic settings, so ``.env``
works) and routes to:

* ``anthropic`` - the official Anthropic SDK, model from ``LLM_MODEL``
  (falls back to ``ANTHROPIC_MODEL`` when ``LLM_MODEL`` is not a Claude model);
* ``ollama``    - a local model through the Ollama HTTP API (offline fallback);
* ``openai``    - any OpenAI-compatible endpoint (``LLM_API_BASE_URL`` + ``LLM_API_KEY``);
* ``offline``   - no model configured; ``generate`` raises so the caller can use
  its built-in deterministic answer instead.

This is the only LLM client in the app: the chat assistant, the farm-score
explanation and the farmer assistant all go through it.

One public method: ``generate(system_prompt, user_message) -> str``. An
unrecognised provider raises a clear error. No key is ever hard-coded: the
Anthropic SDK reads the key from settings (``ANTHROPIC_API_KEY`` in ``.env``).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("agritrust.llm_provider")

#: the values ``LLM_PROVIDER`` may take
SUPPORTED_PROVIDERS = ("anthropic", "ollama", "openai", "offline")

#: default reply budget; callers that want a shorter answer pass ``max_tokens``
DEFAULT_MAX_TOKENS = 500


class LLMProvider:
    """Single entry point for every LLM call in the assistant path.

    Instantiate with no arguments: the provider, model and endpoint all come
    from the environment. ``generate`` is the only method callers need.
    """

    def __init__(self, provider: str | None = None, model: str | None = None) -> None:
        self.provider_name = (provider or settings.llm_provider or "offline").strip().lower()
        self.model = (model or settings.llm_model or "").strip()
        if self.provider_name not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported LLM_PROVIDER '{self.provider_name}'. "
                f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}."
            )
        self._client: Any = None

    # ------------------------------------------------------------------
    # Anthropic
    # ------------------------------------------------------------------
    def _anthropic_model(self) -> str:
        """``LLM_MODEL`` when it names a Claude model, else ``ANTHROPIC_MODEL``."""
        if self.model.startswith("claude"):
            return self.model
        return settings.anthropic_model or "claude-sonnet-4-5"

    def _anthropic_client(self) -> Any:
        if self._client is None:
            import anthropic  # lazy: an absent SDK must not break app startup

            self._client = anthropic.Anthropic(
                api_key=settings.anthropic_api_key or None,  # None -> SDK env fallback
                timeout=settings.llm_timeout_seconds,
                max_retries=1,
            )
        return self._client

    def _generate_anthropic(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        if not (settings.anthropic_api_key or "").strip():
            raise RuntimeError("anthropic_api_key_missing: set ANTHROPIC_API_KEY in .env")
        response = self._anthropic_client().messages.create(
            model=self._anthropic_model(),
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("llm_refusal")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise RuntimeError("llm_empty_response")
        return text

    # ------------------------------------------------------------------
    # Ollama (local fallback)
    # ------------------------------------------------------------------
    def _generate_ollama(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        base = (settings.ollama_base_url or "http://127.0.0.1:11434").rstrip("/")
        payload = {
            "model": self.model or settings.ollama_model or "llama3.1",
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "options": {
                "temperature": settings.llm_temperature,
                "num_predict": max_tokens,
            },
        }
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            response = client.post(f"{base}/api/chat", json=payload)
            response.raise_for_status()
        text = (response.json().get("message") or {}).get("content", "").strip()
        if not text:
            raise RuntimeError("llm_empty_response")
        return text

    # ------------------------------------------------------------------
    # OpenAI-compatible (OpenAI, OpenRouter, Groq, Together...)
    # ------------------------------------------------------------------
    def _generate_openai(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        base = (settings.llm_api_base_url or "").rstrip("/")
        if not base or not settings.llm_api_key:
            raise RuntimeError("llm_not_configured: set LLM_API_BASE_URL and LLM_API_KEY in .env")
        payload = {
            "model": self.model,
            "temperature": settings.llm_temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
            response = client.post(f"{base}/chat/completions", json=payload,
                                   headers={"Authorization": f"Bearer {settings.llm_api_key}"})
            response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"].strip()
        if not text:
            raise RuntimeError("llm_empty_response")
        return text

    # ------------------------------------------------------------------
    # Offline
    # ------------------------------------------------------------------
    def _generate_offline(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        raise RuntimeError("no_llm_configured: set LLM_PROVIDER=anthropic, ollama or openai in .env")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate(self, system_prompt: str, user_message: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        """One completion. Raises on failure so the caller can fall back."""
        dispatch = {
            "anthropic": self._generate_anthropic,
            "ollama": self._generate_ollama,
            "openai": self._generate_openai,
            "offline": self._generate_offline,
        }
        return dispatch[self.provider_name](system_prompt, user_message, max_tokens)


def try_generate(system_prompt: str, user_message: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str | None:
    """Best-effort wrapper: ``None`` instead of raising when no LLM is usable."""
    try:
        return LLMProvider().generate(system_prompt, user_message, max_tokens)
    except Exception as exc:
        logger.info("LLM generate unavailable (%s); caller falls back to deterministic text", exc)
        return None
