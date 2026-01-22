"""
Data migration to copy existing 'options' data to 'options_en' field.

This follows the schema migration 0053_add_options_translation which added
the options_en, options_de, options_fr fields for django-modeltranslation.
"""

from django.db import migrations


def migrate_options_to_en(apps, schema_editor):
    """Copy options column data to options_en for all JourneyChallenge records."""
    JourneyChallenge = apps.get_model('crush_lu', 'JourneyChallenge')

    # Get challenges where options has data but options_en is empty
    challenges = JourneyChallenge.objects.exclude(options={}).filter(options_en={})

    count = 0
    for challenge in challenges:
        challenge.options_en = challenge.options
        challenge.save(update_fields=['options_en'])
        count += 1

    if count:
        print(f"  Migrated {count} JourneyChallenge options to options_en")


def reverse_migration(apps, schema_editor):
    """Reverse migration - nothing to do since original data is preserved."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0053_add_options_translation'),
    ]

    operations = [
        migrations.RunPython(migrate_options_to_en, reverse_migration),
    ]
