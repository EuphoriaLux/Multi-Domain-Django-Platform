# Data migration to convert city-based locations to canton-based locations
# Generated for canton map integration

from django.db import migrations


# Mapping from old city values to new canton values
# Cities are mapped to their respective cantons in Luxembourg
CITY_TO_CANTON_MAP = {
    # Major cities and their cantons
    'Luxembourg City': 'canton-luxembourg',
    'Esch-sur-Alzette': 'canton-esch',
    'Differdange': 'canton-esch',
    'Dudelange': 'canton-esch',
    'Ettelbruck': 'canton-diekirch',
    'Diekirch': 'canton-diekirch',
    'Wiltz': 'canton-wiltz',
    'Echternach': 'canton-echternach',
    'Grevenmacher': 'canton-grevenmacher',
    'Remich': 'canton-remich',
    'Vianden': 'canton-vianden',
    'Clervaux': 'canton-clervaux',
    'Mersch': 'canton-mersch',
    'Mondorf-les-Bains': 'canton-remich',  # Mondorf is in Remich canton
    # "Other" cannot be mapped - will be cleared so users can re-select
    'Other': '',
}

# Reverse mapping for rollback
CANTON_TO_CITY_MAP = {
    'canton-luxembourg': 'Luxembourg City',
    'canton-esch': 'Esch-sur-Alzette',
    'canton-diekirch': 'Diekirch',
    'canton-wiltz': 'Wiltz',
    'canton-echternach': 'Echternach',
    'canton-grevenmacher': 'Grevenmacher',
    'canton-remich': 'Remich',
    'canton-vianden': 'Vianden',
    'canton-clervaux': 'Clervaux',
    'canton-mersch': 'Mersch',
    'canton-capellen': 'Luxembourg City',  # Map to closest major city
    'canton-redange': 'Luxembourg City',   # Map to closest major city
    # Border regions - map to "Other" for rollback
    'border-belgium': 'Other',
    'border-germany': 'Other',
    'border-germany-trier': 'Other',  # Legacy - keep for backwards compatibility
    'border-germany-saarland': 'Other',  # Legacy - keep for backwards compatibility
    'border-france': 'Other',
}


def migrate_locations_forward(apps, schema_editor):
    """
    Migrate city-based locations to canton-based locations.

    This converts existing city names to their corresponding canton IDs.
    Users who had "Other" selected will need to re-select their location.
    """
    CrushProfile = apps.get_model('crush_lu', 'CrushProfile')

    migrated_count = 0
    cleared_count = 0

    for profile in CrushProfile.objects.all():
        old_location = profile.location

        if old_location in CITY_TO_CANTON_MAP:
            new_location = CITY_TO_CANTON_MAP[old_location]
            profile.location = new_location
            profile.save(update_fields=['location'])

            if new_location:
                migrated_count += 1
            else:
                cleared_count += 1

    print(f"Migration complete: {migrated_count} profiles migrated, {cleared_count} cleared (need re-selection)")


def migrate_locations_reverse(apps, schema_editor):
    """
    Reverse migration: convert canton-based locations back to city-based.

    This is used for rollback purposes. Border regions will be converted to "Other".
    """
    CrushProfile = apps.get_model('crush_lu', 'CrushProfile')

    reverted_count = 0

    for profile in CrushProfile.objects.all():
        old_location = profile.location

        if old_location in CANTON_TO_CITY_MAP:
            profile.location = CANTON_TO_CITY_MAP[old_location]
            profile.save(update_fields=['location'])
            reverted_count += 1

    print(f"Rollback complete: {reverted_count} profiles reverted to city-based locations")


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0030_add_phone_verification_fields'),
    ]

    operations = [
        migrations.RunPython(
            migrate_locations_forward,
            migrate_locations_reverse,
        ),
    ]
