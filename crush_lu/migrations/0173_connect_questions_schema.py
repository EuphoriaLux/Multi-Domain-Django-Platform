# Schema for Crush Connect "Read-the-Photo" question-gated matching (M8).
# Adds the question catalogue, the weekly rotation snapshot, per-member gate
# picks (with the owner's truth answer), viewer guesses, and the photo-share
# consent flag. Translatable ``text`` gets the modeltranslation _en/_de/_fr
# columns (all null=True), mirroring 0142_sparkprompt.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0172_backfill_daily_activity"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConnectQuestion",
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
                    "slug",
                    models.SlugField(
                        help_text="Stable identifier for seeds/rotation, e.g. 'work-finance'",
                        max_length=60,
                        unique=True,
                    ),
                ),
                (
                    "text",
                    models.CharField(
                        help_text="Yes/no question, profile-owner POV (translated via modeltranslation)",
                        max_length=200,
                    ),
                ),
                (
                    "text_en",
                    models.CharField(
                        help_text="Yes/no question, profile-owner POV (translated via modeltranslation)",
                        max_length=200,
                        null=True,
                    ),
                ),
                (
                    "text_de",
                    models.CharField(
                        help_text="Yes/no question, profile-owner POV (translated via modeltranslation)",
                        max_length=200,
                        null=True,
                    ),
                ),
                (
                    "text_fr",
                    models.CharField(
                        help_text="Yes/no question, profile-owner POV (translated via modeltranslation)",
                        max_length=200,
                        null=True,
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("lifestyle", "Lifestyle"),
                            ("career", "Career & money"),
                            ("personality", "Personality"),
                            ("dating", "Dating & romance"),
                            ("spicy", "Spicy"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                (
                    "tier",
                    models.PositiveSmallIntegerField(
                        choices=[(1, "Mild"), (2, "Medium"), (3, "Spicy")],
                        db_index=True,
                        default=1,
                        help_text="Spiciness tier; spicy questions ship inactive",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Inactive questions stop being offered but stay linked from history",
                    ),
                ),
                (
                    "weight",
                    models.PositiveSmallIntegerField(
                        default=1,
                        help_text="Rotation weight (higher = more likely to be in a week's set)",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Connect Question",
                "verbose_name_plural": "Connect Questions",
                "ordering": ["-is_active", "category", "-weight", "id"],
            },
        ),
        migrations.CreateModel(
            name="ConnectQuestionWeek",
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
                ("iso_year", models.PositiveSmallIntegerField()),
                (
                    "iso_week",
                    models.PositiveSmallIntegerField(help_text="ISO week number, 1..53"),
                ),
                (
                    "week_start",
                    models.DateField(help_text="Monday of this ISO week (local)"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "questions",
                    models.ManyToManyField(
                        blank=True,
                        related_name="active_weeks",
                        to="crush_lu.connectquestion",
                    ),
                ),
            ],
            options={
                "verbose_name": "Connect Question Week",
                "verbose_name_plural": "Connect Question Weeks",
                "ordering": ["-week_start"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("iso_year", "iso_week"), name="connect_qweek_unique"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="MemberGateQuestion",
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
                    "position",
                    models.PositiveSmallIntegerField(help_text="Display order, 1..3"),
                ),
                (
                    "owner_answer",
                    models.BooleanField(
                        help_text="The member's own truthful yes/no — what guesses are scored against"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "membership",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="gate_questions",
                        to="crush_lu.crushconnectmembership",
                    ),
                ),
                (
                    "question",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="crush_lu.connectquestion",
                    ),
                ),
                (
                    "picked_week",
                    models.ForeignKey(
                        blank=True,
                        help_text="Which week's catalogue this pick came from",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="crush_lu.connectquestionweek",
                    ),
                ),
            ],
            options={
                "verbose_name": "Member Gate Question",
                "verbose_name_plural": "Member Gate Questions",
                "ordering": ["membership_id", "position"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("membership", "position"),
                        name="member_gate_unique_position",
                    ),
                    models.UniqueConstraint(
                        fields=("membership", "question"),
                        name="member_gate_unique_question",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ConnectQuestionAnswer",
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
                    "answer",
                    models.BooleanField(help_text="The guess: Yes = True, No = False"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "question",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="crush_lu.connectquestion",
                    ),
                ),
                (
                    "profile_owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="connect_answers_received",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "responder",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="connect_answers_given",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Connect Question Answer",
                "verbose_name_plural": "Connect Question Answers",
                "ordering": ["-created_at"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("responder", "profile_owner", "question"),
                        name="connect_answer_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("responder", models.F("profile_owner")), _negated=True
                        ),
                        name="connect_answer_no_self",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["profile_owner", "question"],
                        name="connect_answer_owner_q_idx",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="crushconnectmembership",
            name="photo_share_consent",
            field=models.BooleanField(
                default=False,
                help_text="Member agreed their clear photo is shown to the people matched to them each day",
            ),
        ),
    ]
