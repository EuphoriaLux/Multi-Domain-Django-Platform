# Generated for Crush.lu iOS App Store readiness.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("crush_lu", "0177_alter_crushconnectmembership_excluded_by_coach_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="IOSNativeAuthCode",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("code_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("redirect_uri", models.URLField(max_length=500)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user_agent", models.TextField(blank=True)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ios_native_auth_codes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "iOS Native Auth Code",
                "verbose_name_plural": "iOS Native Auth Codes",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="IOSAppDevice",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "device_token",
                    models.CharField(
                        help_text="APNS device token for the native iOS app",
                        max_length=255,
                        unique=True,
                    ),
                ),
                (
                    "device_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        help_text="Stable app-generated device identifier",
                        max_length=128,
                    ),
                ),
                (
                    "environment",
                    models.CharField(
                        choices=[("sandbox", "Sandbox"), ("production", "Production")],
                        db_index=True,
                        default="production",
                        max_length=20,
                    ),
                ),
                ("bundle_id", models.CharField(default="lu.crush.app", max_length=128)),
                ("app_version", models.CharField(blank=True, max_length=32)),
                ("app_build", models.CharField(blank=True, max_length=32)),
                ("device_name", models.CharField(blank=True, max_length=100)),
                ("system_version", models.CharField(blank=True, max_length=50)),
                ("user_agent", models.TextField(blank=True)),
                ("enabled", models.BooleanField(default=True)),
                (
                    "notify_new_messages",
                    models.BooleanField(
                        default=True,
                        help_text="Notify about new connection messages",
                    ),
                ),
                (
                    "notify_event_reminders",
                    models.BooleanField(default=True, help_text="Notify about upcoming events"),
                ),
                (
                    "notify_new_connections",
                    models.BooleanField(
                        default=True,
                        help_text="Notify about new connection requests",
                    ),
                ),
                (
                    "notify_profile_updates",
                    models.BooleanField(
                        default=True,
                        help_text="Notify about profile approval status",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                ("last_push_at", models.DateTimeField(blank=True, null=True)),
                ("failure_count", models.PositiveIntegerField(default=0)),
                (
                    "user",
                    models.ForeignKey(
                        help_text="User who owns this iOS app installation",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ios_app_devices",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "iOS App Device",
                "verbose_name_plural": "iOS App Devices",
                "ordering": ["-last_seen_at"],
                "indexes": [
                    models.Index(fields=["user", "enabled"], name="crush_lu_io_user_id_a52b0a_idx"),
                    models.Index(fields=["environment", "enabled"], name="crush_lu_io_environ_9b61d5_idx"),
                ],
            },
        ),
    ]
