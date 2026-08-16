"""Model providers for the chronicle, on the standard library only.

No SDK dependency. A prototype that pulls in `openai` and `google-generativeai`
inherits their transitive trees and their opinions about retries — and retries
are exactly wrong here, because the deadline is a bartender's patience.

**Timeout caveat, deliberately not hidden:** `urllib`'s `timeout` applies per
socket operation (connect, then each `recv`), not to total wall time, so a
server that dribbles one byte just under that interval, repeatedly, can hold
the call open indefinitely — engine.generate_vignette's post-hoc elapsed-time
check can't run until `complete()` returns, so it can't prevent that, only
discard the answer once it finally does. `_post_json` below runs the actual
`urlopen`/`.read()` call on a background thread and joins it with the real
timeout, so the request-handling thread is bounded by wall time regardless of
how the server paces bytes. The daemon thread itself isn't killed on timeout
(Python has no safe way to do that) — it keeps running until its own
per-operation timeout eventually fires and it exits on its own; this bounds
the caller, not the leaked connection, which is the trade-off worth knowing
about before pointing this at an untrusted endpoint.
"""

from __future__ import annotations

import json
import threading
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

    outcome: dict = {}

    def _do_request() -> None:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                outcome["data"] = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            outcome["error"] = exc
        except Exception as exc:  # noqa: BLE001 - surfaced as ProviderError below
            outcome["error"] = exc

    # See module docstring: `timeout` bounds each socket op, not the total
    # call. Running it on a thread and joining with the same budget bounds
    # *this* function's wall-clock time regardless of how the server paces
    # its response — the thread itself is abandoned (daemon) if it doesn't
    # finish in time, not killed.
    worker = threading.Thread(target=_do_request, daemon=True)
    worker.start()
    worker.join(timeout=timeout)

    if worker.is_alive():
        raise ProviderError(f"provider call exceeded {timeout}s wall-clock deadline")
    if "error" in outcome:
        raise ProviderError(str(outcome["error"])) from outcome["error"]
    return outcome.get("data", {})


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
        # "-latest" alias, not a pinned version: Google retires dated model IDs
        # from under callers with no notice (gemini-2.0-flash, current when this
        # class was written, already 404s as of 2026-08 — verified against the
        # live API, not assumed). The alias is Google's own hedge against this.
        model: str = "gemini-flash-latest",
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
