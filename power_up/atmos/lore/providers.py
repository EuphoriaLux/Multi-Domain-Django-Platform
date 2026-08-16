"""Model providers for the chronicle, on the standard library only.

No SDK dependency. A prototype that pulls in `openai` and `google-generativeai`
inherits their transitive trees and their opinions about retries — and retries
are exactly wrong here, because the deadline is a bartender's patience.

**Timeout caveat, deliberately not hidden:** `urllib`'s `timeout` applies per
socket operation, not to total wall time, so a server that dribbles bytes can
outlast it. `engine.generate_vignette` therefore also checks a wall-clock
deadline after the call returns and discards a late answer. Neither mechanism
alone is sufficient.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol


class ProviderError(Exception):
    """Any provider failure. Always recoverable — the caller falls back."""


class StoryProvider(Protocol):
    """Anything that can turn two prompts into one vignette."""

    def complete(self, system: str, user: str, *, timeout: float) -> str: ...


class StaticProvider:
    """Returns canned text. For tests and for demoing without an API key."""

    def __init__(self, response: str = "", *, error: Exception | None = None) -> None:
        self._response = response
        self._error = error

    def complete(self, system: str, user: str, *, timeout: float) -> str:
        if self._error is not None:
            raise self._error
        return self._response


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ProviderError(str(exc)) from exc


class OpenAIProvider:
    """Chat Completions. `base_url` is overridable for compatible endpoints."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        max_tokens: int = 200,
        temperature: float = 1.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._temperature = temperature

    def complete(self, system: str, user: str, *, timeout: float) -> str:
        data = _post_json(
            f"{self._base_url}/chat/completions",
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": self._max_tokens,
                "temperature": self._temperature,
            },
            {"Authorization": f"Bearer {self._api_key}"},
            timeout,
        )
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape: {data!r}") from exc


class GeminiProvider:
    """Google Generative Language `generateContent`."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-2.0-flash",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        max_tokens: int = 200,
        temperature: float = 1.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._temperature = temperature

    def complete(self, system: str, user: str, *, timeout: float) -> str:
        data = _post_json(
            f"{self._base_url}/models/{self._model}:generateContent",
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "maxOutputTokens": self._max_tokens,
                    "temperature": self._temperature,
                },
            },
            {"x-goog-api-key": self._api_key},
            timeout,
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape: {data!r}") from exc
