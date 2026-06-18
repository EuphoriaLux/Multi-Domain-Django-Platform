"""
Admin API endpoint that ingests changelog ("What's New") entries.

Called by the Claude Code *changelog routine* when a PR is merged into ``main``.
The routine runs in a cloud session that has the full git clone, composes the
user-facing copy, then POSTs it here. This endpoint writes (and publishes) it
to the production database so it appears on ``/changelog/`` — so production DB
credentials never have to leave the server.

- Auth: Bearer token matching ``settings.ADMIN_API_KEY`` (see
  ``crush_lu.api_admin_auth``), like the other ``/api/admin/...`` endpoints.
- Lives outside ``i18n_patterns`` so the caller can use a fixed, language-neutral
  ``/api/admin/changelog/ingest/`` path.
- Because entries can auto-publish with no human review, every incoming string
  is passed through :func:`crush_lu.changelog_text.scrub` server-side — the
  caller is never trusted to have removed emails / internal hosts / session URLs.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from crush_lu.api_admin_auth import authenticate_admin_request, unauthorized
from crush_lu.changelog_text import scrub
from crush_lu.models import PatchNote, PatchNoteCategory, PatchRelease

logger = logging.getLogger(__name__)

# Mirror the model field limits so oversized input is a clean 400, not a 500.
_RELEASE_MAXLEN = {"version": 20, "slug": 80, "title": 140, "hero_summary": 280}
_NOTE_TITLE_MAXLEN = 160
_VALID_CATEGORIES = set(PatchNoteCategory.values)
_SLUG_RE = re.compile(r"^[-\w]+\Z")  # same character set as Django's SlugField


def _bad_request(msg: str) -> JsonResponse:
    return JsonResponse({"success": False, "error": msg}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def ingest_changelog(request):
    """Create/update a PatchRelease and append PatchNotes from a JSON payload.

    Body::

        {
          "release": {"version","slug","title","hero_summary","released_on","is_published"},
          "notes":   [{"category","title","body","related_commits","order"}, ...]
        }

    Returns 201 with the release slug/url, 400 on invalid input, 401 if the
    Bearer token is missing/wrong.
    """
    if not authenticate_admin_request(request):
        return unauthorized(request)

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return _bad_request("Body must be valid JSON.")

    release_in = payload.get("release")
    notes_in = payload.get("notes", [])
    if not isinstance(release_in, dict):
        return _bad_request("'release' object is required.")
    if not isinstance(notes_in, list):
        return _bad_request("'notes' must be a list.")

    # --- validate + scrub the release -----------------------------------
    slug = (release_in.get("slug") or "").strip()
    version = (release_in.get("version") or "").strip()
    title = scrub(release_in.get("title") or "")
    hero_summary = scrub(release_in.get("hero_summary") or "")

    if not slug or not _SLUG_RE.match(slug):
        return _bad_request("'release.slug' is required and must be a valid slug.")
    if not version:
        return _bad_request("'release.version' is required.")
    if not title:
        return _bad_request("'release.title' is required.")
    for field, value in (("version", version), ("slug", slug),
                         ("title", title), ("hero_summary", hero_summary)):
        if len(value) > _RELEASE_MAXLEN[field]:
            return _bad_request(f"'release.{field}' exceeds {_RELEASE_MAXLEN[field]} characters.")

    released_on_raw = release_in.get("released_on")
    if released_on_raw:
        try:
            released_on = date.fromisoformat(released_on_raw)
        except (ValueError, TypeError):
            return _bad_request("'release.released_on' must be an ISO date (YYYY-MM-DD).")
    else:
        released_on = timezone.now().date()

    # Auto-publish by default (per design); callers may override to stage drafts.
    is_published = bool(release_in.get("is_published", True))

    # --- validate + scrub the notes -------------------------------------
    clean_notes = []
    for i, note in enumerate(notes_in):
        if not isinstance(note, dict):
            return _bad_request(f"notes[{i}] must be an object.")
        category = (note.get("category") or "").strip()
        if category not in _VALID_CATEGORIES:
            return _bad_request(
                f"notes[{i}].category must be one of {sorted(_VALID_CATEGORIES)}."
            )
        n_title = scrub(note.get("title") or "")
        if not n_title:
            return _bad_request(f"notes[{i}].title is required.")
        if len(n_title) > _NOTE_TITLE_MAXLEN:
            return _bad_request(f"notes[{i}].title exceeds {_NOTE_TITLE_MAXLEN} characters.")
        related = note.get("related_commits", [])
        if not isinstance(related, list) or not all(isinstance(s, str) for s in related):
            return _bad_request(f"notes[{i}].related_commits must be a list of strings.")
        clean_notes.append({
            "category": category,
            "title": n_title,
            "body": scrub(note.get("body") or ""),
            "related_commits": related,
            "order": int(note.get("order", i)),
        })

    # --- write -----------------------------------------------------------
    defaults = {
        "version": version,
        "title": title,
        "hero_summary": hero_summary,
        "released_on": released_on,
        "is_published": is_published,
    }
    with transaction.atomic():
        release, created = PatchRelease.objects.update_or_create(
            slug=slug, defaults=defaults,
        )

        # Idempotency: a merge webhook can fire more than once. Skip any note
        # whose backing commit SHA was *already persisted* on this release by an
        # earlier request. We snapshot the pre-request SHAs and never add this
        # request's own SHAs to it — otherwise multiple notes in a single
        # payload that share the merge SHA would drop everything after the first.
        # On a re-delivery those SHAs are persisted, so the whole payload is
        # correctly skipped.
        preexisting_shas: set[str] = set()
        for sha_list in release.notes.values_list("related_commits", flat=True):
            preexisting_shas.update(sha_list or [])

        notes_added = 0
        for nd in clean_notes:
            if nd["related_commits"] and any(s in preexisting_shas for s in nd["related_commits"]):
                continue
            PatchNote.objects.create(release=release, auto_generated=True, **nd)
            notes_added += 1

    logger.info(
        "changelog ingest: slug=%s created=%s notes_added=%s published=%s",
        slug, created, notes_added, is_published,
    )
    return JsonResponse({
        "success": True,
        "created": created,
        "notes_added": notes_added,
        "version": release.version,
        "slug": release.slug,
        "url": release.get_absolute_url(),
        "is_published": release.is_published,
    }, status=201)
