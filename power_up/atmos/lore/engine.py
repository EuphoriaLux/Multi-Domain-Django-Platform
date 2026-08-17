"""Orchestration: get a vignette onto the ticket, whatever happens.

`generate_vignette` is total. It does not raise, it does not return None, and
it does not block past its deadline. Everything downstream of it — the ticket
layout, the ESC/POS stream, the printer — assumes there is text. A bar ticket
that fails to print because a model was slow is worse than a plain ticket.

The fallback triggers on a **hard deadline**, not merely on an error. That is
the important distinction: an API that answers correctly in four seconds has
still failed, because the bartender has moved on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .chronicle import Chronicle, ChronicleEvent, build_prompt
from .fallback import generate_fallback_vignette
from .providers import ProviderError, StoryProvider
from .safety import VignetteRejected, guard_vignette

#: Default wall-clock budget. Chosen from the service side, not the model side:
#: a bartender waiting on a ticket notices two seconds and resents three.
DEFAULT_DEADLINE_SECONDS = 1.2


@dataclass(frozen=True)
class VignetteResult:
    """What got written, where it came from, and how long it took."""

    text: str
    source: str  # "model" | "fallback"
    reason: str = ""  # why the fallback fired, for metrics
    elapsed_ms: int = 0

    @property
    def used_fallback(self) -> bool:
        return self.source == "fallback"


def generate_vignette(
    event: ChronicleEvent,
    chronicle: Chronicle,
    *,
    provider: StoryProvider | None = None,
    deadline_s: float = DEFAULT_DEADLINE_SECONDS,
    extra_blocklist: tuple[str, ...] = (),
    record: bool = True,
) -> VignetteResult:
    """Produce a printable vignette for `event`. Never raises.

    Order of preference: a model answer that arrives on time and passes the
    output guard; otherwise the deterministic procedural story.

    When `record` is true the event is appended to the chronicle with its final
    text, so the next order can reference it.
    """
    started = time.monotonic()

    def finish(text: str, source: str, reason: str = "") -> VignetteResult:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if record:
            chronicle.record(
                ChronicleEvent(
                    at=event.at,
                    table_label=event.table_label,
                    persona=event.persona,
                    drinks=event.drinks,
                    ticket_code=event.ticket_code,
                    vignette=text,
                )
            )
        return VignetteResult(
            text=text, source=source, reason=reason, elapsed_ms=elapsed_ms
        )

    def fall_back(reason: str) -> VignetteResult:
        # Built against the chronicle as it stood *before* this event, so the
        # fallback can still name another persona in the room.
        others = chronicle.active_personas(exclude=event.persona)
        return finish(
            generate_fallback_vignette(event, others=others),
            "fallback",
            reason,
        )

    if provider is None:
        return fall_back("no_provider")

    system, user = build_prompt(chronicle, event)

    try:
        raw = provider.complete(system, user, timeout=deadline_s)
    except ProviderError as exc:
        return fall_back(f"provider_error:{type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001 - a ticket must print regardless
        return fall_back(f"provider_crash:{type(exc).__name__}")

    # urllib's timeout is per socket operation, so a slow-dribble response can
    # beat it. Discard anything that arrived after the guests stopped caring.
    if time.monotonic() - started > deadline_s:
        return fall_back("deadline_exceeded")

    try:
        text = guard_vignette(raw, extra_blocklist=extra_blocklist)
    except VignetteRejected as exc:
        return fall_back(f"guard:{exc.reason}")
    except Exception as exc:  # noqa: BLE001 - the guard must not be able to
        return fall_back(f"guard_crash:{type(exc).__name__}")  # break printing

    return finish(text, "model")
