"""Model providers for the chronicle, on the standard library only.

No SDK dependency. A prototype that pulls in `openai` and `google-generativeai`
inherits their transitive trees and their opinions about retries — and retries
are exactly wrong here, because the deadline is a bartender's patience.

**Timeout caveat, deliberately not hidden:** `urllib`'s `timeout` applies per
socket operation (connect, then each `recv`), not to total wall time, so a
server that dribbles one byte just under that interval, repeatedly, can hold
the call open indefinitely — engine.generate_vignette's post-hoc elapsed-time
check can't run until `complete()` returns, so it can't prevent that, only
discard the answer once it finally does. `_post_json` below submits the actual
`urlopen`/`.read()` call to a small, bounded `ThreadPoolExecutor` and waits on
the future with the real timeout, so the request-handling thread is bounded by
wall time regardless of how the server paces bytes. A worker that's still
blocked on a slow socket when its deadline passes isn't killed (Python has no
safe way to do that) — but because the pool is bounded, at most
`_MAX_CONCURRENT_PROVIDER_CALLS` such workers can ever be stuck at once,
instead of leaking one new thread per timed-out order under sustained load.

**Admission control, not just a bounded pool:** `ThreadPoolExecutor`'s own
work queue is unbounded — cancelling a future whose work item hasn't started
yet stops it from *running*, but that item is only actually removed from the
queue when a worker gets around to dequeuing it. If every worker is stuck on
a slow-dribbling response, nothing dequeues, and calls keep piling up in the
queue (with their request payloads) for as long as callers keep submitting.
`_ADMISSION_SEMAPHORE` below tracks "admitted but not yet finished" work
directly — sized to the pool, acquired before `submit()`, released only when
the future actually completes (including a successful, never-started
cancellation) via `add_done_callback`. A call made while the pool is fully
saturated is rejected immediately, before ever touching the executor's queue,
so a saturated pool degrades to fast fallbacks rather than an ever-growing
backlog.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Protocol

# Bounds how many provider HTTP calls can be in flight (or queued) at once,
# across every guest ordering concurrently — a fixed-size pool instead of a
# thread per call, so a slow/degraded endpoint under sustained load can only
# ever have this many workers stuck on it, not an unbounded, ever-growing
# count. 8 is generous for a single bar's realistic concurrent-order volume.
_MAX_CONCURRENT_PROVIDER_CALLS = 8
_EXECUTOR = ThreadPoolExecutor(
    max_workers=_MAX_CONCURRENT_PROVIDER_CALLS, thread_name_prefix="atmos-provider"
)
# See "Admission control" above — one permit per call admitted to the
# executor, held until that call's future is actually done (run, errored, or
# cleanly cancelled before running). `acquire(blocking=False)` is the
# admission check: nothing waits on this semaphore.
_ADMISSION_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENT_PROVIDER_CALLS)


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

    def _do_request() -> dict:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    # Admission check, not a wait: reject immediately if every permit is
    # already held by in-flight/queued work, rather than joining an
    # unbounded queue behind it (see module docstring).
    if not _ADMISSION_SEMAPHORE.acquire(blocking=False):
        raise ProviderError(
            f"provider pool saturated at {_MAX_CONCURRENT_PROVIDER_CALLS} "
            "in-flight calls — rejecting immediately"
        )

    # See module docstring: `timeout` bounds each socket op, not the total
    # call. Submitting to the bounded pool and waiting on the future with
    # the same budget bounds *this* function's wall-clock time regardless
    # of how the server paces its response.
    try:
        future = _EXECUTOR.submit(_do_request)
    except BaseException:
        # If submit() itself raises — e.g. ThreadPoolExecutor.submit() after
        # interpreter shutdown has started — the permit acquired above would
        # otherwise never reach the done-callback that releases it,
        # permanently leaking one slot from this process-lifetime semaphore.
        _ADMISSION_SEMAPHORE.release()
        raise
    # Released when the future is actually done — run, errored, OR cleanly
    # cancelled before it ever started — not when *this* call's own wait
    # below times out. That keeps the semaphore counting real admitted work,
    # so a worker still stuck on a slow socket after we've moved on still
    # holds its permit.
    future.add_done_callback(lambda _f: _ADMISSION_SEMAPHORE.release())
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError:
        # If the call hadn't started yet (pool was saturated), this
        # actually removes it from the queue — no worker thread is spent
        # on it at all, and the done-callback above fires immediately,
        # freeing its permit. If it had already started, cancel() is a
        # no-op and that one worker (and its permit) stays held until its
        # own socket-level timeout eventually fires — bounded by pool size,
        # not unbounded.
        future.cancel()
        raise ProviderError(
            f"provider call exceeded {timeout}s wall-clock deadline"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ProviderError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surfaced as ProviderError below
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
