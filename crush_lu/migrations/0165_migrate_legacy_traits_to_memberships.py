"""
Backfill legacy "Ideal Crush" traits onto ALREADY-ONBOARDED Crush Connect
memberships.

Members who onboarded into Crush Connect before the trait fields were added
(migration 0164) have empty membership traits. The grandfathering gate skips
them past the wizard, so the lazy form-prefill never runs for them — and since
matching now reads traits from the membership, they would silently fall out of
trait-based scoring (their Drop weight degrades to neutral).

This migration copies their legacy CrushProfile traits onto the membership,
but ONLY for onboarded memberships and ONLY where the membership field is still
empty/untouched (so a deliberate edit is never clobbered, and re-runs are
idempotent). It does NOT create memberships for users who never opted in — that
stays lazy-on-opt-in per the product decision.
"""

from django.db import migrations


def copy_legacy_traits(apps, schema_editor):
    CrushConnectMembership = apps.get_model("crush_lu", "CrushConnectMembership")
    CrushProfile = apps.get_model("crush_lu", "CrushProfile")

    for membership in CrushConnectMembership.objects.filter(
        onboarded_at__isnull=False
    ).iterator():
        try:
            profile = CrushProfile.objects.get(user_id=membership.user_id)
        except CrushProfile.DoesNotExist:
            continue

        member_has_traits = (
            membership.qualities.exists()
            or membership.defects.exists()
            or membership.sought_qualities.exists()
        )

        update_fields = []
        if not membership.first_step_preference and profile.first_step_preference:
            membership.first_step_preference = profile.first_step_preference
            update_fields.append("first_step_preference")
        # astro_enabled defaults True on both models — only carry the profile's
        # value across on the first trait migration (membership otherwise empty),
        # so a member who later toggles it on the membership isn't reset.
        if not member_has_traits and membership.astro_enabled != profile.astro_enabled:
            membership.astro_enabled = profile.astro_enabled
            update_fields.append("astro_enabled")
        if update_fields:
            membership.save(update_fields=update_fields)

        if not membership.qualities.exists():
            pks = list(profile.qualities.values_list("pk", flat=True))
            if pks:
                membership.qualities.set(pks)
        if not membership.defects.exists():
            pks = list(profile.defects.values_list("pk", flat=True))
            if pks:
                membership.defects.set(pks)
        if not membership.sought_qualities.exists():
            pks = list(profile.sought_qualities.values_list("pk", flat=True))
            if pks:
                membership.sought_qualities.set(pks)


def recompute_scores(apps, schema_editor):
    """Best-effort refresh of trait-based MatchScores for the members whose
    traits we just migrated, so they don't have to re-enter data to be scored.

    Wrapped so a failure can never block the migration; operators can also run
    ``python manage.py recalculate_match_scores`` (preferable for large
    datasets, since scoring is O(pool) per member).
    """
    try:
        from django.contrib.auth import get_user_model

        from crush_lu.matching import update_match_scores_for_user

        User = get_user_model()
        CrushConnectMembership = apps.get_model("crush_lu", "CrushConnectMembership")
        user_ids = list(
            CrushConnectMembership.objects.filter(
                onboarded_at__isnull=False
            ).values_list("user_id", flat=True)
        )
        for uid in user_ids:
            try:
                update_match_scores_for_user(User.objects.get(pk=uid))
            except Exception:
                continue
    except Exception:
        pass


class Migration(migrations.Migration):
    dependencies = [
        ("crush_lu", "0164_connect_traits"),
    ]

    operations = [
        migrations.RunPython(copy_legacy_traits, migrations.RunPython.noop),
        migrations.RunPython(recompute_scores, migrations.RunPython.noop),
    ]
