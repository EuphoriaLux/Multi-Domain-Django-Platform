from django.db import migrations, models


def backfill_verification_method(apps, schema_editor):
    """Record how each already-verified profile was verified.

    A verified profile whose user has a LuxID/OIDC social account is treated as
    LuxID-verified; every other verified profile is grandfathered as 'legacy'.
    Non-verified profiles keep the empty default.
    """
    CrushProfile = apps.get_model("crush_lu", "CrushProfile")
    SocialAccount = apps.get_model("socialaccount", "SocialAccount")

    luxid_user_ids = set(
        SocialAccount.objects.filter(
            provider__in=["luxid", "openid_connect"]
        ).values_list("user_id", flat=True)
    )

    for profile in CrushProfile.objects.filter(verification_status="verified").only(
        "pk", "user_id"
    ):
        method = "luxid" if profile.user_id in luxid_user_ids else "legacy"
        CrushProfile.objects.filter(pk=profile.pk).update(verification_method=method)


class Migration(migrations.Migration):

    atomic = False  # adding a db_index column alongside RunPython on PostgreSQL

    dependencies = [
        ("crush_lu", "0152_add_reserved_premium_seats"),
        ("socialaccount", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="crushprofile",
            name="verification_method",
            field=models.CharField(
                blank=True,
                choices=[
                    ("luxid", "LuxID identity"),
                    ("coach_event", "Coach at event"),
                    ("premium_coach", "Premium coach"),
                    ("legacy", "Legacy"),
                ],
                db_index=True,
                default="",
                help_text="How the profile became verified (set at the verification point)",
                max_length=20,
            ),
        ),
        migrations.RunPython(
            backfill_verification_method,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
