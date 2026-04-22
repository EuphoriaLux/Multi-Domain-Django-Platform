"""Add PatchNote.auto_generated flag and a compound index on PatchRelease.

Companion to the fix for two data-loss bugs in generate_patch_notes:

1. The generator now only deletes notes where auto_generated=True so
   curator edits survive regeneration.
2. The compound index speeds up the hot /changelog/ list query, which
   filters by is_published and orders by released_on.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0130_patchrelease_patchnote'),
    ]

    operations = [
        migrations.AddField(
            model_name='patchnote',
            name='auto_generated',
            field=models.BooleanField(
                default=True,
                db_index=True,
                help_text=(
                    'Internal: True for notes produced by generate_patch_notes, '
                    'False once a human has edited them. The generator only '
                    'replaces rows where this is True.'
                ),
            ),
        ),
        migrations.AddIndex(
            model_name='patchrelease',
            index=models.Index(
                fields=['is_published', '-released_on'],
                name='patchrel_pub_date_idx',
            ),
        ),
    ]
