"""
Context processors for Crush.lu app
These make variables available to all templates
"""

import time

from datetime import timedelta
from django.db.models import Q
from django.utils import timezone
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from .models import (
    EventConnection,
    CrushProfile,
    SpecialUserExperience,
    JourneyProgress,
    ProfileSubmission,
    EventRegistration,
    CrushSpark,
)

# Simple in-memory cache for site config (avoids DB hit on every request)
_site_config_cache = {"config": None, "expires": 0}

# Profile verification state → navbar progress indicator
PROFILE_STEP_INFO = {
    "incomplete": (1, _("Complete your profile")),
    "pending": (2, _("Verify your identity")),
    "verified": (3, _("Profile verified")),
    "rejected": (0, _("Profile rejected")),
    # Legacy wizard-step keys kept for graceful handling of cached values
    "not_started": (1, _("Get started")),
    "step1": (1, _("Tell us about you")),
    "step2": (2, _("Add photos")),
    "step3": (2, _("Add photos")),
    "step4": (2, _("Review & submit")),
    "submitted": (2, _("Under review")),
}


def crush_user_context(request):
    """Add user-specific context for navigation and UI"""
    from .ios_app_utils import is_android_native_request, is_ios_native_request

    is_ios_native_app = is_ios_native_request(request)
    is_android_native_app = is_android_native_request(request)
    ios_native_commerce_enabled = getattr(
        settings, "IOS_NATIVE_COMMERCE_ENABLED", False
    )
    android_native_commerce_enabled = getattr(
        settings, "ANDROID_NATIVE_COMMERCE_ENABLED", False
    )
    context = {
        "crush_cache_enabled": getattr(settings, "CRUSH_CACHE_ENABLED", False),
        "is_ios_native_app": is_ios_native_app,
        "is_android_native_app": is_android_native_app,
        "ios_native_commerce_enabled": ios_native_commerce_enabled,
        "android_native_commerce_enabled": android_native_commerce_enabled,
        "suppress_ios_commerce": is_ios_native_app and not ios_native_commerce_enabled,
        "suppress_android_commerce": is_android_native_app
        and not android_native_commerce_enabled,
        "suppress_native_commerce": (
            (is_ios_native_app and not ios_native_commerce_enabled)
            or (is_android_native_app and not android_native_commerce_enabled)
        ),
    }

    if request.user.is_authenticated:
        # Email-verification flag — drives the verification banner in the
        # onboarding stepper. We rely on allauth's EmailAddress.verified;
        # social-login users always have at least one verified address
        # because providers we trust (Google, Microsoft, Apple, LuxID,
        # Facebook) are listed in SOCIALACCOUNT_EMAIL_VERIFIED_PROVIDERS.
        try:
            from allauth.account.models import EmailAddress

            context["email_verified"] = EmailAddress.objects.filter(
                user=request.user, verified=True
            ).exists()
        except Exception:
            # Allauth not installed in some test contexts — assume verified
            # to avoid spurious banners.
            context["email_verified"] = True

        # Blocked counterparts are hidden from every connection surface; reuse the
        # same id set so badge counts can't advertise a pair the lists now hide.
        from .services.blocking import blocked_user_ids

        blocked_ids = blocked_user_ids(request.user)

        # Connection count for badge. Excludes blocked counterparts — a `shared`
        # connection isn't terminated on block (contact was already exchanged),
        # so without this it would keep inflating the active count while
        # my_connections hides it.
        connection_count = (
            EventConnection.objects.filter(
                Q(requester=request.user) | Q(recipient=request.user),
                status__in=["accepted", "coach_reviewing", "coach_approved", "shared"],
            )
            .exclude(Q(requester_id__in=blocked_ids) | Q(recipient_id__in=blocked_ids))
            .count()
        )

        # Pending connection requests (received). Exclude blocked requesters so
        # the nav/dashboard badge can't advertise a request the page itself hides
        # (defence-in-depth; blocking also declines the underlying connection).
        pending_requests_count = (
            EventConnection.objects.filter(recipient=request.user, status="pending")
            .exclude(requester_id__in=blocked_ids)
            .count()
        )

        context["connection_count"] = connection_count
        context["pending_requests_count"] = pending_requests_count

        # Sparks needing action (approved by coach, waiting for journey creation)
        actionable_sparks_count = CrushSpark.objects.filter(
            sender=request.user,
            status__in=["coach_approved", "coach_assigned"],
        ).count()
        context["actionable_sparks_count"] = actionable_sparks_count

        # Crush Connect: pending received Sparks — drives the Connect nav badge
        # (sub-nav, navbar menu, mobile bottom-nav). Computed only when the
        # Connect nav is actually visible (onboarded membership, or staff) so it
        # stays off every other page's query budget.
        connect_pending_sparks_count = 0
        try:
            from crush_lu.connect_phase import candidate_access_open

            # Candidates receive Sparks too, so the badge follows candidate access
            # (open in the beta), not just the full launch flag.
            connect_open = candidate_access_open()
            membership = getattr(request.user, "crush_connect_membership", None)
            nav_visible = request.user.is_staff or (
                connect_open and membership is not None and membership.is_onboarded
            )
            if nav_visible:
                from .models import CuriositySpark

                # Reuse the already-computed blocked_ids (finding H4 — this was
                # a duplicate blocked_user_ids query on every authenticated page).
                connect_pending_sparks_count = (
                    CuriositySpark.objects.filter(
                        recipient=request.user, status="pending"
                    )
                    .exclude(sender_id__in=blocked_ids)
                    .count()
                )
        except Exception:
            connect_pending_sparks_count = 0
        context["connect_pending_sparks_count"] = connect_pending_sparks_count

        # Profile submission status for visual indicators
        profile_submission = None
        profile = CrushProfile.objects.filter(user=request.user).first()
        context["profile"] = profile
        if profile:
            verification_status = profile.verification_status
            context["profile_completion_status"] = (
                verification_status  # backward compat alias
            )

            # Profile step info for navbar progress indicator
            step_info = PROFILE_STEP_INFO.get(
                verification_status, (0, _("Get started"))
            )
            context["profile_completion_step"] = step_info[0]
            context["profile_step_label"] = step_info[1]

            # Approved flag drives the navbar's "full navigation" branch (Edit
            # Profile, Connect, …). It must be exposed even when there is NO
            # ProfileSubmission: LuxID-verified members never go through paid
            # coach review, so gating it on a submission row is what left them
            # stuck showing "Complete Profile 3/4" despite being fully verified
            # (every verification path — LuxID, coach-at-event, admin — sets
            # is_approved=True, so the flag itself was already correct).
            #
            # Mirror the SAME legacy `is_approved` boolean the downstream gates
            # still enforce (_render_edit_profile_form at views.py, the Connect
            # entry points in views_crush_connect.py). Keying the navbar off
            # verification_status instead could advertise Edit/Connect links to
            # a verified-but-is_approved=False profile that those views would
            # then bounce/403 — is_approved is the consistent predicate until
            # those gates migrate off the legacy flag.
            context["profile_is_approved"] = profile.is_approved

            # Expired submissions are closed-out pre-pivot reviews — the navbar
            # must not resurrect "needs action" / coach labels for them, not
            # even from an older non-expired row.
            profile_submission = ProfileSubmission.latest_for_profile(
                profile, select_related=("coach__user",)
            )

            if profile_submission:
                context["profile_submission"] = profile_submission
                context["profile_status"] = profile_submission.status
                context["profile_needs_action"] = profile_submission.status in (
                    "revision",
                    "recontact_coach",
                )

                # Coach name for pending review / recontact navbar display
                if profile_submission.coach and profile_submission.status in (
                    "pending",
                    "recontact_coach",
                ):
                    context["assigned_coach_name"] = (
                        profile_submission.coach.user.first_name
                    )
        else:
            # No profile yet - show step 0
            context["profile_completion_step"] = 0
            context["profile_step_label"] = _("Get started")

        # Upcoming events for user (includes ongoing events until end_time)
        # Use a generous cutoff to include events that may still be ongoing,
        # then filter precisely in Python. This avoids timedelta * F()
        # which is not supported on SQLite.
        now = timezone.now()
        generous_cutoff = now - timedelta(hours=24)
        upcoming_registrations = list(
            EventRegistration.objects.filter(
                user=request.user,
                event__date_time__gte=generous_cutoff,
                status__in=["confirmed", "waitlist", "attended"],
            )
            .select_related("event")
            .order_by("event__date_time")
        )
        # Filter precisely: keep events whose end_time hasn't passed
        upcoming_registrations = [
            reg
            for reg in upcoming_registrations
            if reg.event.date_time + timedelta(minutes=reg.event.duration_minutes or 0)
            >= now
        ][:5]

        context["upcoming_events"] = upcoming_registrations
        context["upcoming_events_count"] = len(upcoming_registrations)

        # Pending screening calls count for coaches
        if hasattr(request.user, "crushcoach") and request.user.crushcoach.is_active:
            pending_screening_count = ProfileSubmission.objects.filter(
                coach=request.user.crushcoach,
                status__in=["pending", "recontact_coach"],
                review_call_completed=False,
            ).count()
            context["pending_screening_count"] = pending_screening_count

            # Count pending invitations to review (future feature)
            context["pending_invitations_count"] = 0

        # Check for special journey experience
        # Prioritize linked_user (gifts), then fall back to name match (legacy)
        special_experience = SpecialUserExperience.objects.filter(
            Q(is_active=True)
            & (
                Q(linked_user=request.user)  # Direct link (gifts)
                | Q(
                    first_name__iexact=request.user.first_name,
                    last_name__iexact=request.user.last_name,
                    linked_user__isnull=True,  # Only name-match if no linked_user
                )
            )
        ).first()

        if special_experience:
            context["has_special_journey"] = True
            context["special_experience"] = special_experience

            # Check if journey is already started
            journey_progress = JourneyProgress.objects.filter(user=request.user).first()
            context["journey_started"] = journey_progress is not None
            if journey_progress:
                context["journey_progress"] = journey_progress

    return context


def social_preview_context(request):
    """Expose social preview settings for Open Graph/Twitter tags."""
    return {
        "social_preview_image_url": settings.SOCIAL_PREVIEW_IMAGE_URL,
    }


def firebase_config(request):
    """
    Add Firebase configuration to template context.

    These values come from environment variables and are safe to expose
    to the client (they are client-side API keys, protected by domain restrictions).
    """
    from .services import whatsapp

    return {
        "firebase_api_key": getattr(settings, "FIREBASE_API_KEY", ""),
        "firebase_auth_domain": getattr(settings, "FIREBASE_AUTH_DOMAIN", ""),
        "firebase_project_id": getattr(settings, "FIREBASE_PROJECT_ID", ""),
        # Whether WhatsApp is usable as an OTP delivery channel (Meta creds set).
        # Gates the "Verify via WhatsApp" button so we never show an option that
        # the send endpoint would only reject with a 503.
        "whatsapp_otp_enabled": whatsapp.is_configured(),
    }


def site_config_context(request):
    """Expose CrushSiteConfig values (cached for 5 minutes).

    The config model instance is cached so that translated fields
    (banner_message, banner_link_text) resolve per-request language
    via django-modeltranslation, while non-translated fields are
    cached as plain values.
    """
    now = time.time()
    if _site_config_cache["config"] is None or now > _site_config_cache["expires"]:
        from .models import CrushSiteConfig

        try:
            config = CrushSiteConfig.get_config()

            # Build social_links list from non-empty URLs
            social_platforms = [
                ("Instagram", config.social_instagram_url, "instagram"),
                ("Facebook", config.social_facebook_url, "facebook"),
                ("LinkedIn", config.social_linkedin_url, "linkedin"),
                ("Google Business", config.social_google_business_url, "google"),
                ("Reddit", config.social_reddit_url, "reddit"),
            ]
            social_links = [
                {"name": name, "url": url, "icon_id": icon_id}
                for name, url, icon_id in social_platforms
                if url
            ]

            # Cache non-translated values as plain data
            _site_config_cache["config"] = {
                "whatsapp_number": config.whatsapp_number,
                "whatsapp_enabled": config.whatsapp_enabled
                and bool(config.whatsapp_number),
                "whatsapp_default_message": config.whatsapp_default_message,
                "social_links": social_links,
                "banner_enabled": config.banner_enabled,
                "banner_style": config.banner_style,
                "banner_target_statuses": config.banner_target_statuses or [],
            }
            # Cache the model instance for translated field resolution
            _site_config_cache["config_obj"] = config
        except Exception:
            _site_config_cache["config"] = {
                "whatsapp_number": "",
                "whatsapp_enabled": False,
                "whatsapp_default_message": "",
                "social_links": [],
                "banner_enabled": False,
                "banner_message": "",
                "banner_link_text": "",
                "banner_link_url": "",
                "banner_style": "info",
                "banner_target_statuses": [],
            }
            _site_config_cache["config_obj"] = None
        _site_config_cache["expires"] = now + 300  # 5 minutes

    # Build result from cached data, resolving translated fields per-request
    result = dict(_site_config_cache["config"])
    config_obj = _site_config_cache.get("config_obj")
    if config_obj is not None:
        result["banner_message"] = config_obj.banner_message
        result["banner_link_text"] = config_obj.banner_link_text
        result["banner_link_url"] = config_obj.banner_link_url
    return result
