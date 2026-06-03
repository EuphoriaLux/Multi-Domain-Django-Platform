from django.db import migrations, models


def backfill_verification_method(apps, schema_editor):
    """Record how each already-verified profile was verified.

    A verified profile whose user authenticated through LuxID is treated as
    LuxID-verified; every other verified profile is grandfathered as 'legacy'.
    Non-verified profiles keep the empty default.

    LuxID is reached two ways: the dedicated ``luxid`` provider, or the generic
    ``openid_connect`` provider configured as the LuxID ``SocialApp``
    (``provider_id="luxid"``). The generic OIDC provider is shared with other
    platforms (e.g. LinkedIn on Entreprinder), so a bare ``openid_connect``
    account must NOT be assumed to be LuxID — only accounts whose token belongs
    to the LuxID app count. This mirrors the runtime check in views/signals.
    """
    CrushProfile = apps.get_model("crush_lu", "CrushProfile")
    SocialAccount = apps.get_model("socialaccount", "SocialAccount")
    SocialApp = apps.get_model("socialaccount", "SocialApp")
    SocialToken = apps.get_model("socialaccount", "SocialToken")

    # Users on the dedicated LuxID provider.
    luxid_user_ids = set(
        SocialAccount.objects.filter(provider="luxid").values_list("user_id", flat=True)
    )

    # Users on the generic OIDC provider whose token belongs to the LuxID app
    # (provider_id="luxid"). Excludes other OIDC apps such as LinkedIn.
    luxid_app_ids = list(
        SocialApp.objects.filter(
            provider="openid_connect", provider_id="luxid"
        ).values_list("pk", flat=True)
    )
    if luxid_app_ids:
        luxid_user_ids |= set(
            SocialToken.objects.filter(
                app_id__in=luxid_app_ids,
                account__provider="openid_connect",
            ).values_list("account__user_id", flat=True)
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
        # 0004 adds SocialApp.provider_id; 0005 makes SocialToken.app nullable —
        # both needed for the LuxID-app token check in the backfill above.
        ("socialaccount", "0005_socialtoken_nullable_app"),
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
