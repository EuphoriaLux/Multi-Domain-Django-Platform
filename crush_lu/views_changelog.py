"""
Public-facing changelog views for crush.lu.

URL surface:
- /changelog/            timeline of published releases with filter + search
- /changelog/<slug>/     permalink for a single release (sharable)
"""

from django.db.models import Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from crush_lu.models import PatchNote, PatchNoteCategory, PatchRelease


def _filtered_notes_prefetch(category=None, q=None):
    """Return a Prefetch of notes, optionally filtered by category / keyword."""
    qs = PatchNote.objects.all()
    if category:
        qs = qs.filter(category=category)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))
    qs = qs.order_by("category", "order", "id")
    return Prefetch("notes", queryset=qs, to_attr="filtered_notes")


def _category_choices():
    return [
        {"value": value, "label": label}
        for value, label in PatchNoteCategory.choices
    ]


def changelog_list(request):
    """Timeline of published releases, newest first, filterable."""
    raw_category = (request.GET.get("category") or "").strip()
    category = raw_category if raw_category in PatchNoteCategory.values else ""
    q = (request.GET.get("q") or "").strip()[:120]

    releases = (
        PatchRelease.objects.filter(is_published=True)
        .prefetch_related(_filtered_notes_prefetch(category or None, q or None))
        .order_by("-released_on", "-version")
    )

    if category or q:
        releases = [r for r in releases if getattr(r, "filtered_notes", [])]
    else:
        releases = list(releases)

    template = (
        "crush_lu/changelog/_timeline.html"
        if request.headers.get("HX-Request")
        else "crush_lu/changelog/list.html"
    )

    return render(
        request,
        template,
        {
            "releases": releases,
            "category": category,
            "q": q,
            "category_choices": _category_choices(),
            "total_published": PatchRelease.objects.filter(is_published=True).count(),
        },
    )


def changelog_detail(request, slug):
    """Single-release permalink page."""
    release = get_object_or_404(PatchRelease, slug=slug, is_published=True)
    release.filtered_notes = list(release.notes.order_by("category", "order", "id"))
    return render(
        request,
        "crush_lu/changelog/detail.html",
        {
            "release": release,
            "category_choices": _category_choices(),
        },
    )
