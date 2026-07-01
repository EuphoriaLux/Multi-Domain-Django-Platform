"""Backfill PatchNote.published_on for notes that predate the field.

published_on lets each note show the day it actually went live instead of the
whole release card's one released_on date. Existing rows have no independent
record of when they were added, so the best available default is the release's
own released_on.

A couple of notes on the currently-live 'v1-8-crush-connect' release are known
to have actually gone out later than the release's released_on (the release
window stayed open for weeks and the ingest endpoint was, until this change,
always stamping the release's date onto every note appended to it). Their real
publish dates are recoverable from the PR merge dates in related_commits, so
seed those explicitly rather than leaving them incorrectly dated.
"""

from django.db import migrations

# merge commit SHA -> actual merge date, for notes whose published_on would
# otherwise incorrectly backfill to the release's released_on.
KNOWN_PUBLISH_DATES = {
    "80510c36859951c10d1f1a8283fe552a4425d395": "2026-06-30",  # peer safety blocking/reporting
    "ef091554f0e14a8e4fd59dc849d39fc5576c4ae1": "2026-07-01",  # navbar LuxID-verified fix
}


def backfill_published_on(apps, schema_editor):
    from datetime import date

    PatchNote = apps.get_model("crush_lu", "PatchNote")

    rows = []
    for note in PatchNote.objects.select_related("release").only(
        "id", "related_commits", "release__released_on"
    ):
        override = next(
            (
                KNOWN_PUBLISH_DATES[sha]
                for sha in note.related_commits or []
                if sha in KNOWN_PUBLISH_DATES
            ),
            None,
        )
        note.published_on = (
            date.fromisoformat(override) if override else note.release.released_on
        )
        rows.append(note)

    if rows:
        PatchNote.objects.bulk_update(rows, ["published_on"], batch_size=1000)


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0173_patchnote_published_on"),
    ]

    operations = [
        migrations.RunPython(backfill_published_on, migrations.RunPython.noop),
    ]
