"""
Data migration: copy match preferences from CrushProfile onto each
CrushConnectMembership, and park already-onboarded members past the final
wizard step so the new resumable wizard never re-prompts them.

The copy logic lives in the module-level ``copy_connect_prefs`` function so it
can be unit-tested against the real models (the migration just hands it the
historical model classes).
"""

from django.db import migrations


# Onboarding has 7 steps; "8" parks finished members one past the end. Kept as
# a literal (not imported from onboarding_connect) so this historical migration
# stays stable even if TOTAL_STEPS later changes.
ONBOARDED_STEP = 8


def copy_connect_prefs(CrushProfile, CrushConnectMembership, *, batch_size=500):
    """
    Copy ``preferred_genders`` / ``preferred_age_min`` / ``preferred_age_max``
    from each profile onto its membership, and set ``onboarding_step`` past the
    end for already-onboarded members.

    Pure function of the two model classes (real or historical) so it is
    unit-testable. Returns the number of membership rows updated.
    """
    profile_prefs = {
        p.user_id: (
            p.preferred_genders or [],
            p.preferred_age_min if p.preferred_age_min is not None else 18,
            p.preferred_age_max if p.preferred_age_max is not None else 99,
        )
        for p in CrushProfile.objects.all().only(
            "user_id", "preferred_genders", "preferred_age_min", "preferred_age_max"
        )
    }

    to_update = []
    for m in CrushConnectMembership.objects.all().iterator():
        changed = False
        prefs = profile_prefs.get(m.user_id)
        if prefs is not None:
            m.preferred_genders, m.preferred_age_min, m.preferred_age_max = prefs
            changed = True
        if m.onboarded_at is not None:
            m.onboarding_step = ONBOARDED_STEP
            changed = True
        if changed:
            to_update.append(m)

    if to_update:
        CrushConnectMembership.objects.bulk_update(
            to_update,
            [
                "preferred_genders",
                "preferred_age_min",
                "preferred_age_max",
                "onboarding_step",
            ],
            batch_size=batch_size,
        )
    return len(to_update)


def forwards(apps, schema_editor):
    CrushProfile = apps.get_model("crush_lu", "CrushProfile")
    CrushConnectMembership = apps.get_model("crush_lu", "CrushConnectMembership")
    copy_connect_prefs(CrushProfile, CrushConnectMembership)


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0161_connect_catalogue_schema"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
