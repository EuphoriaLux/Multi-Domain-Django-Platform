"""
Text helpers for the public changelog ("What's New").

`SECRET_PATTERNS` / `scrub()` strip content that must never appear in
user-facing patch notes — internal email addresses, internal Azure hosts, and
the Git trailer / session-URL lines Claude Code adds to commits.

These were originally defined inside the `generate_patch_notes` management
command. They live here so the command *and* the
``/api/admin/changelog/ingest/`` endpoint apply the exact same red-line filter:
the endpoint can auto-publish notes with no human review, so the server must
scrub incoming text rather than trust the caller.
"""
from __future__ import annotations

import re

# Red-line filter patterns — we never surface these in public copy.
SECRET_PATTERNS = [
    re.compile(r"[\w\.-]+@[\w\.-]+\.\w+"),                          # emails
    re.compile(r"https?://[\w\.-]+\.(azurewebsites|azure)\.net"),  # internal hosts
    re.compile(r"https?://claude\.ai/code/\S+"),                   # co-author session urls
    re.compile(r"co-authored-by:?[^\n]*", re.IGNORECASE),
    re.compile(r"signed-off-by:?[^\n]*", re.IGNORECASE),
]


def scrub(text: str) -> str:
    """Strip red-line patterns and collapse whitespace."""
    cleaned = text or ""
    for pat in SECRET_PATTERNS:
        cleaned = pat.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()
