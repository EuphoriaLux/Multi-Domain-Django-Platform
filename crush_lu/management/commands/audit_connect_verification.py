"""Read-only aggregate inventory for the January verification rollout."""

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Q

from crush_lu.models import ConnectRealPersonCheck
from crush_lu.real_person_check_rules import PURPOSE_BADGE_ONLY
from crush_lu.services.connect_verification import (
    valid_video_admissions,
    video_verification_mode,
)
from crush_lu.services.crush_connect import (
    annotate_connect_identity_arms,
    coach_attested_attendance_q,
    filter_catalogue_eligible,
    filter_primary_photo_review_approved,
)


class Command(BaseCommand):
    help = (
        "Audit Connect verification counts without writing or displaying member data."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Print aggregate JSON.")

    def handle(self, *args, **options):
        users = get_user_model().objects.filter(
            is_active=True,
            crushprofile__is_active=True,
            crushprofile__verification_status="verified",
            data_consent__crushlu_consent_given=True,
            data_consent__crushlu_banned=False,
        )
        identity = annotate_connect_identity_arms(users)
        events = identity.filter(coach_attested_attendance_q())
        videos = users.filter(
            pk__in=valid_video_admissions().values("profile__user_id")
        )
        historical = ConnectRealPersonCheck.objects.filter(
            profile__user__in=users, purpose=PURPOSE_BADGE_ONLY, status="passed"
        )
        # Preparation is candidate readiness without the recent-login window;
        # it does not claim that the member may start Connect Week right now.
        prepared = filter_catalogue_eligible(users, require_recent_login=False)
        result = {
            "video_mode": video_verification_mode(),
            "human_verification_required": bool(
                getattr(settings, "CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED", False)
            ),
            "active_verified_consented_members": users.count(),
            "luxid_linked": identity.filter(
                Q(_has_luxid_native=True) | Q(_has_luxid_oidc=True)
            ).count(),
            "event_qualified": events.count(),
            "video_qualified": videos.count(),
            "both_routes": events.filter(pk__in=videos.values("pk")).count(),
            "without_human_verification": users.exclude(pk__in=events.values("pk"))
            .exclude(pk__in=videos.values("pk"))
            .count(),
            "historical_badge_video_checks": historical.filter(medium="video").count(),
            "historical_badge_voice_checks": historical.filter(medium="voice").count(),
            "without_publishable_primary_photo": users.exclude(
                pk__in=filter_primary_photo_review_approved(users).values("pk")
            ).count(),
            "catalogue_prerequisites_incomplete": users.exclude(
                pk__in=prepared.values("pk")
            ).count(),
        }
        if options["json"]:
            self.stdout.write(json.dumps(result, sort_keys=True))
        else:
            for key, value in result.items():
                self.stdout.write(f"{key}: {value}")
