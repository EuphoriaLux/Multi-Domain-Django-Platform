"""Staff-only social-media planning endpoints for hub.crush.lu."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from functools import partial

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from crush_lu.models import CrushProfile, EventConnection, MeetupEvent
from crush_lu.utils.i18n import build_absolute_url

from .buffer_service import (
    BufferPartialFailure,
    BufferServiceError,
    create_buffer_update,
    list_buffer_profiles,
)
from .claude_service import ClaudeServiceError, expand_social_post, generate_social_copy
from .image_generator import (
    generate_kpi_card,
    generate_profile_card,
)
from .models import HubResource, SocialPost
from .serializers import SocialPostSerializer

logger = logging.getLogger(__name__)

ALLOWED_CATEGORIES = {"events", "kpis", "profiles", "tips", "recaps"}
ALLOWED_PLATFORMS = {"instagram", "facebook", "linkedin"}
ALLOWED_LANGUAGES = {choice for choice, _label in SocialPost.Language.choices}
ALLOWED_PILLARS = {choice for choice, _label in SocialPost.Pillar.choices}

BUFFER_SCHEDULE_ERROR = "Buffer could not schedule this post. Try again later."
COPY_GENERATION_ERROR = "Social copy generation is temporarily unavailable."
BUFFER_PROFILES_ERROR = "Buffer channels are temporarily unavailable."
ARTICLE_GENERATION_ERROR = "Article generation is temporarily unavailable."
BUFFER_PARTIAL_ERROR = (
    "Some Buffer channels were scheduled before another channel failed. "
    "Reconcile the saved Buffer IDs before retrying."
)
PROFILE_INELIGIBLE_ERROR = (
    "The featured profile is no longer eligible for marketing publication."
)
EVENT_INELIGIBLE_ERROR = "The linked event is no longer public, published, or upcoming."
EVENT_SCHEDULE_ERROR = "Publication time must be before the linked event starts."
SCHEDULED_EDIT_ERROR = (
    "Delivery fields cannot be changed after a post has been scheduled in Buffer."
)
SCHEDULING_FIELDS = {"content", "scheduled_for", "media_url", "buffer_profile_ids"}
PROMOTED_STATUSES = {SocialPost.Status.SCHEDULED, SocialPost.Status.PUBLISHED}
PROMOTION_STATUS_RANK = {
    SocialPost.Status.PUBLISHED: 6,
    SocialPost.Status.SCHEDULED: 5,
    SocialPost.Status.APPROVED: 4,
    SocialPost.Status.PENDING_REVIEW: 3,
    SocialPost.Status.DRAFT: 2,
    SocialPost.Status.FAILED: 1,
}
KPI_LABELS = {
    "fr": ("Nouveaux membres", "Connexions créées", "Répartition des profils"),
    "en": ("New members", "Connections made", "Profile distribution"),
    "de": ("Neue Mitglieder", "Neue Verbindungen", "Profilverteilung"),
}


def _history_entry(request, state: str, *, note: str = "") -> dict:
    entry = {
        "status": state,
        "timestamp": timezone.now().isoformat(),
        "actor": request.user.get_username(),
    }
    if note:
        entry["note"] = note
    return entry


def _next_friday_at_1600() -> datetime:
    now = timezone.localtime()
    days = (4 - now.weekday() + 7) % 7
    if days == 0 and (now.hour, now.minute) >= (16, 0):
        days = 7
    return (now + timedelta(days=days)).replace(
        hour=16, minute=0, second=0, microsecond=0
    )


def _kpi_snapshot() -> dict[str, str]:
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    new_members = CrushProfile.objects.filter(
        verification_status="verified",
        approved_at__gte=week_ago,
    ).count()
    connections = EventConnection.objects.filter(shared_at__gte=week_ago).count()
    events = MeetupEvent.objects.filter(
        is_published=True,
        is_cancelled=False,
        date_time__gte=month_start,
        date_time__lt=now,
    ).count()

    gender_counts = dict(
        CrushProfile.objects.filter(is_active=True, verification_status="verified")
        .values_list("gender")
        .annotate(total=Count("id"))
    )
    known_total = sum(gender_counts.get(code, 0) for code in ("M", "F", "NB", "O"))
    if known_total:
        male = round(gender_counts.get("M", 0) * 100 / known_total)
        female = round(gender_counts.get("F", 0) * 100 / known_total)
        other = max(0, 100 - male - female)
        parity = f"{male}% ♂ / {female}% ♀"
        if other:
            parity += f" / {other}% ⚧"
    else:
        parity = "N/D"

    return {
        "new_members_week": f"+{new_members}",
        "matches_created_week": str(connections),
        "parity_ratio": parity,
        "events_hosted_month": str(events),
    }


def _generate_kpi_graphic(context: dict[str, str], *, language: str) -> str:
    member_label, connection_label, parity_label = KPI_LABELS[language]
    return generate_kpi_card(
        language=language,
        stats=[
            {"value": context["new_members_week"], "label": member_label},
            {"value": context["matches_created_week"], "label": connection_label},
            {"value": context["parity_ratio"], "label": parity_label},
        ],
    )


def _eligible_profiles():
    return (
        CrushProfile.objects.filter(
            is_active=True,
            verification_status="verified",
            date_of_birth__isnull=False,
            user__is_active=True,
            user__is_staff=False,
            user__data_consent__crushlu_consent_given=True,
            user__data_consent__marketing_consent=True,
        )
        .exclude(user__first_name="")
        .exclude(location="")
        .exclude(event_vibe="")
        .select_related("user")
        .prefetch_related("interests_new")
        .order_by("-updated_at")
    )


def _profile_payload(profile: CrushProfile) -> dict:
    return {
        "id": str(profile.pk),
        "first_name": profile.user.first_name,
        "age": profile.age_display,
        "region": profile.location,
        "passions": [item.label for item in profile.event_interest_chips[:4]],
        "bio_quote": (
            str(profile.get_event_vibe_display()) if profile.event_vibe else ""
        ),
    }


def _event_payload(event: MeetupEvent, request) -> dict:
    image_url = ""
    if event.image:
        image_url = event.image.url
        if image_url.startswith("/"):
            image_url = request.build_absolute_uri(image_url)
    facebook_posts = [
        post
        for post in event.social_promotion_posts.all()
        if "facebook" in (post.platforms or [])
    ]
    promotion_post = max(
        facebook_posts,
        key=lambda post: (PROMOTION_STATUS_RANK.get(post.status, 0), post.created_at),
        default=None,
    )
    available_languages = _event_available_languages(event)

    return {
        "id": str(event.pk),
        "title": event.title,
        "event_type": event.get_event_type_display(),
        "date": event.date_time.isoformat(),
        "location": event.location,
        "image_url": image_url,
        "event_url": build_absolute_url(
            "crush_lu:event_detail",
            lang=available_languages[0],
            kwargs={"event_id": event.pk},
        ),
        "available_languages": available_languages,
        "promotion_post_id": str(promotion_post.pk) if promotion_post else None,
        "promotion_status": promotion_post.status if promotion_post else "not_started",
        "is_promoted": bool(
            promotion_post and promotion_post.status in PROMOTED_STATUSES
        ),
    }


def _event_available_languages(event: MeetupEvent) -> list[str]:
    languages = [
        language
        for language in (
            SocialPost.Language.FR,
            SocialPost.Language.EN,
            SocialPost.Language.DE,
        )
        if str(getattr(event, f"title_{language}", "") or "").strip()
        and str(getattr(event, f"description_{language}", "") or "").strip()
    ]
    return languages or [SocialPost.Language.FR]


def _event_value(event: MeetupEvent, field: str, language: str) -> str:
    """Return stored event copy without generating or paraphrasing it."""

    candidates = (
        f"{field}_{language}",
        f"{field}_{SocialPost.Language.FR}",
        f"{field}_{SocialPost.Language.EN}",
        f"{field}_{SocialPost.Language.DE}",
        field,
    )
    for candidate in candidates:
        value = str(getattr(event, candidate, "") or "").strip()
        if value:
            return value
    return ""


def _event_post_content(event: MeetupEvent, language: str) -> str:
    title = _event_value(event, "title", language)
    description = _event_value(event, "description", language)
    local_date = timezone.localtime(event.date_time).strftime("%d/%m/%Y · %H:%M")
    event_url = build_absolute_url(
        "crush_lu:event_detail",
        lang=language,
        kwargs={"event_id": event.pk},
    )
    return (
        f"{title}\n\n{description}\n\n"
        f"📅 {local_date}\n"
        f"📍 {event.location}\n\n"
        f"{event_url}"
    )


def _event_post_is_dispatched(post: SocialPost) -> bool:
    return post.status in PROMOTED_STATUSES or bool(post.buffer_id)


def _event_media_url(event: MeetupEvent, request) -> str | None:
    if not event.image:
        return None
    image_url = event.image.url
    return (
        request.build_absolute_uri(image_url)
        if image_url.startswith("/")
        else image_url
    )


def _refresh_event_draft(
    post: SocialPost,
    *,
    event: MeetupEvent,
    language: str,
    platforms: list[str],
    request,
) -> SocialPost:
    """Synchronize a reusable draft with the event's current stored content."""

    refreshed = {
        "hook": _event_value(event, "title", language),
        "content": _event_post_content(event, language),
        "media_url": _event_media_url(event, request),
        "platforms": platforms,
    }
    changed_fields = [
        field for field, value in refreshed.items() if getattr(post, field) != value
    ]
    if not changed_fields:
        return post

    for field, value in refreshed.items():
        setattr(post, field, value)
    if post.status != SocialPost.Status.DRAFT:
        post.status = SocialPost.Status.DRAFT
        changed_fields.append("status")
    history = list(post.status_history or [])
    history.append(
        _history_entry(
            request,
            SocialPost.Status.DRAFT,
            note="Refreshed from the event's current stored content.",
        )
    )
    post.status_history = history
    changed_fields.append("status_history")
    post.save(update_fields=[*dict.fromkeys(changed_fields), "updated_at"])
    return post


def _create_event_drafts(
    *,
    event: MeetupEvent,
    languages: list[str],
    platforms: list[str],
    request,
) -> tuple[list[SocialPost], int]:
    posts = []
    created_count = 0
    for language in languages:
        language_posts = [
            post
            for post in event.social_promotion_posts.all()
            if post.language == language
        ]
        dispatched_posts = [
            post for post in language_posts if _event_post_is_dispatched(post)
        ]
        dispatched_platforms = {
            platform for post in dispatched_posts for platform in (post.platforms or [])
        }
        missing_platforms = [
            platform for platform in platforms if platform not in dispatched_platforms
        ]
        if not missing_platforms:
            posts.extend(
                post
                for post in dispatched_posts
                if set(post.platforms or []).intersection(platforms)
            )
            continue

        reusable = next(
            (
                post
                for post in language_posts
                if not _event_post_is_dispatched(post)
                and set(missing_platforms).issubset(set(post.platforms or []))
            ),
            None,
        ) or next(
            (post for post in language_posts if not _event_post_is_dispatched(post)),
            None,
        )
        if reusable:
            reusable_platforms = [
                platform
                for platform in (reusable.platforms or [])
                if platform not in dispatched_platforms
            ]
            merged_platforms = list(
                dict.fromkeys([*reusable_platforms, *missing_platforms])
            )
            posts.append(
                _refresh_event_draft(
                    reusable,
                    event=event,
                    language=language,
                    platforms=merged_platforms,
                    request=request,
                )
            )
            continue

        post = SocialPost.objects.create(
            user=request.user,
            source_event=event,
            hook=_event_value(event, "title", language),
            pillar=SocialPost.Pillar.PROMO,
            language=language,
            platforms=missing_platforms,
            content=_event_post_content(event, language),
            media_url=_event_media_url(event, request),
            status=SocialPost.Status.DRAFT,
            status_history=[
                _history_entry(
                    request,
                    SocialPost.Status.DRAFT,
                    note="Created from stored event content without AI generation.",
                )
            ],
        )
        posts.append(post)
        created_count += 1
    return posts, created_count


class SocialPostsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        status_filter = request.query_params.get("status")
        queryset = SocialPost.objects.select_related(
            "user", "featured_profile", "source_event"
        )
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return Response({"items": SocialPostSerializer(queryset, many=True).data})

    def post(self, request):
        serializer = SocialPostSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        post = serializer.save(
            user=request.user,
            status=SocialPost.Status.DRAFT,
            status_history=[_history_entry(request, SocialPost.Status.DRAFT)],
        )
        return Response(
            {"post": SocialPostSerializer(post).data}, status=status.HTTP_201_CREATED
        )


class SocialPostDetailView(APIView):
    permission_classes = [IsAdminUser]

    @transaction.atomic
    def patch(self, request, pk):
        try:
            post = SocialPost.objects.select_for_update().get(pk=pk)
        except SocialPost.DoesNotExist:
            return Response(
                {"error": "Post not found"}, status=status.HTTP_404_NOT_FOUND
            )

        requested_status = request.data.get("status", post.status)
        serializer = SocialPostSerializer(post, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if requested_status == SocialPost.Status.SCHEDULED:
            if post.buffer_id and post.status != SocialPost.Status.SCHEDULED:
                return Response(
                    {
                        "error": (
                            "This post already has Buffer publications. "
                            "Reconcile them before retrying."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            if (
                post.featured_profile_id
                and not _eligible_profiles()
                .filter(pk=post.featured_profile_id)
                .exists()
            ):
                return Response(
                    {"error": PROFILE_INELIGIBLE_ERROR},
                    status=status.HTTP_409_CONFLICT,
                )
            linked_event = None
            if post.source_event_id:
                linked_event = MeetupEvent.objects.filter(
                    pk=post.source_event_id,
                    is_published=True,
                    is_cancelled=False,
                    is_private_invitation=False,
                    date_time__gte=timezone.now(),
                ).first()
            if post.source_event_id and not linked_event:
                return Response(
                    {"error": EVENT_INELIGIBLE_ERROR},
                    status=status.HTTP_409_CONFLICT,
                )
            scheduling_errors = {}
            scheduled_for = serializer.validated_data.get(
                "scheduled_for", post.scheduled_for
            )
            if not scheduled_for:
                scheduling_errors["scheduled_for"] = "Choose a publication time"
            elif linked_event and scheduled_for >= linked_event.date_time:
                scheduling_errors["scheduled_for"] = EVENT_SCHEDULE_ERROR
            if not request.data.get("buffer_profile_ids", post.buffer_profile_ids):
                scheduling_errors["buffer_profile_ids"] = "Select a Buffer channel"
            if not str(request.data.get("content", post.content)).strip():
                scheduling_errors["content"] = "Post content cannot be empty"
            if scheduling_errors:
                return Response(scheduling_errors, status=status.HTTP_400_BAD_REQUEST)

        old_status = post.status
        if post.status == SocialPost.Status.SCHEDULED:
            changed_delivery_fields = sorted(
                field
                for field in SCHEDULING_FIELDS
                if field in serializer.validated_data
                and serializer.validated_data[field] != getattr(post, field)
            )
            if changed_delivery_fields:
                return Response(
                    {
                        "error": SCHEDULED_EDIT_ERROR,
                        "fields": changed_delivery_fields,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        updated_post = serializer.save()
        new_status = updated_post.status

        if new_status != old_status:
            history = list(updated_post.status_history or [])
            history.append(_history_entry(request, new_status))
            updated_post.status_history = history
            updated_post.save(update_fields=["status_history"])

        if new_status == SocialPost.Status.SCHEDULED and new_status != old_status:
            try:
                result = create_buffer_update(
                    text=updated_post.content,
                    profile_ids=updated_post.buffer_profile_ids or [],
                    scheduled_at=updated_post.scheduled_for.isoformat(),
                    media_url=updated_post.media_url,
                )
            except BufferPartialFailure as exc:
                logger.exception(
                    "Buffer scheduling partially failed for social post %s",
                    updated_post.pk,
                )
                updated_post.status = SocialPost.Status.FAILED
                updated_post.buffer_id = ",".join(exc.created_post_ids)
                history = list(updated_post.status_history or [])
                history.append(
                    _history_entry(
                        request,
                        SocialPost.Status.FAILED,
                        note=BUFFER_PARTIAL_ERROR,
                    )
                )
                updated_post.status_history = history
                updated_post.save(
                    update_fields=["status", "buffer_id", "status_history"]
                )
                return Response(
                    {
                        "error": BUFFER_PARTIAL_ERROR,
                        "post": SocialPostSerializer(updated_post).data,
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            except BufferServiceError:
                logger.exception(
                    "Buffer scheduling failed for social post %s", updated_post.pk
                )
                updated_post.status = SocialPost.Status.FAILED
                history = list(updated_post.status_history or [])
                history.append(
                    _history_entry(
                        request,
                        SocialPost.Status.FAILED,
                        note=BUFFER_SCHEDULE_ERROR,
                    )
                )
                updated_post.status_history = history
                updated_post.save(update_fields=["status", "status_history"])
                return Response(
                    {
                        "error": BUFFER_SCHEDULE_ERROR,
                        "post": SocialPostSerializer(updated_post).data,
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )

            updated_post.buffer_id = result["buffer_id"]
            updated_post.save(update_fields=["buffer_id"])

        return Response({"post": SocialPostSerializer(updated_post).data})


class SocialGenerateView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        category = request.data.get("category", "events")
        hook = str(request.data.get("hook", "")).strip()
        pillar = request.data.get("pillar", SocialPost.Pillar.EVENT_RECAP)
        platforms = list(dict.fromkeys(request.data.get("platforms") or []))
        languages = list(dict.fromkeys(request.data.get("languages") or []))

        errors = {}
        if category not in ALLOWED_CATEGORIES:
            errors["category"] = "Unsupported category"
        if not hook or len(hook) > 255:
            errors["hook"] = "Provide a hook between 1 and 255 characters"
        if pillar not in ALLOWED_PILLARS:
            errors["pillar"] = "Unsupported pillar"
        if not platforms or not set(platforms).issubset(ALLOWED_PLATFORMS):
            errors["platforms"] = "Select at least one supported platform"
        if not languages or not set(languages).issubset(ALLOWED_LANGUAGES):
            errors["languages"] = "Select at least one supported language"
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        context: dict = {"topic": hook}
        graphic = None
        featured_profile = None
        warnings = []

        if category == "events":
            event_id = request.data.get("event_id")
            with transaction.atomic():
                event = (
                    MeetupEvent.objects.select_for_update()
                    .filter(
                        pk=event_id,
                        is_published=True,
                        is_cancelled=False,
                        is_private_invitation=False,
                        date_time__gte=timezone.now(),
                    )
                    .prefetch_related("social_promotion_posts")
                    .first()
                    if event_id
                    else None
                )
                if not event:
                    return Response(
                        {"event_id": "Select a public, published upcoming event"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                unavailable_languages = set(languages) - set(
                    _event_available_languages(event)
                )
                if unavailable_languages:
                    return Response(
                        {
                            "languages": (
                                "The event has no stored title and description for: "
                                + ", ".join(sorted(unavailable_languages))
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                posts, created_count = _create_event_drafts(
                    event=event,
                    languages=languages,
                    platforms=platforms,
                    request=request,
                )
            return Response(
                {
                    "posts": SocialPostSerializer(posts, many=True).data,
                    "warnings": [],
                    "created_count": created_count,
                    "reused_count": len(posts) - created_count,
                    "copy_source": "event",
                },
                status=(
                    status.HTTP_201_CREATED if created_count else status.HTTP_200_OK
                ),
            )

        if category == "kpis":
            context = _kpi_snapshot()
            graphic = partial(_generate_kpi_graphic, context)
        elif category == "profiles":
            profile = (
                _eligible_profiles().filter(pk=request.data.get("profile_id")).first()
            )
            if not profile:
                return Response(
                    {"profile_id": "Select an eligible, consenting profile"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            context = _profile_payload(profile)
            featured_profile = profile
            hook = f"Membre de la semaine : {context['first_name']}"
            graphic = partial(
                generate_profile_card,
                first_name=context["first_name"],
                age=context["age"],
                region=context["region"],
                passions=context["passions"],
                bio_quote=context["bio_quote"],
            )

        try:
            generated_copy = generate_social_copy(
                category=category,
                pillar=pillar,
                hook=hook,
                platforms=platforms,
                languages=languages,
                context=context,
            )
        except ClaudeServiceError:
            logger.exception("Claude social copy generation failed")
            return Response(
                {"error": COPY_GENERATION_ERROR},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        media_urls = {}
        if graphic:
            for language in languages:
                try:
                    media_urls[language] = graphic(language=language)
                except Exception:
                    logger.exception(
                        "Social graphic generation failed for language %s", language
                    )
                    warnings.append(
                        f"Copy was generated for {language}, but its graphic was unavailable"
                    )

        scheduled_for = _next_friday_at_1600()
        posts = [
            SocialPost.objects.create(
                user=request.user,
                featured_profile=featured_profile,
                hook=hook,
                pillar=pillar,
                language=language,
                platforms=platforms,
                content=generated_copy[language],
                media_url=media_urls.get(language),
                status=SocialPost.Status.DRAFT,
                scheduled_for=scheduled_for,
                status_history=[_history_entry(request, SocialPost.Status.DRAFT)],
            )
            for language in languages
        ]
        return Response(
            {
                "posts": SocialPostSerializer(posts, many=True).data,
                "warnings": warnings,
            },
            status=status.HTTP_201_CREATED,
        )


class SocialUpcomingEventsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        events = (
            MeetupEvent.objects.filter(
                is_published=True,
                is_cancelled=False,
                is_private_invitation=False,
                date_time__gte=timezone.now(),
            )
            .prefetch_related("social_promotion_posts")
            .order_by("date_time")[:20]
        )
        return Response({"items": [_event_payload(event, request) for event in events]})


class SocialEventDraftsView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, event_id):
        languages = list(
            dict.fromkeys(request.data.get("languages") or [SocialPost.Language.FR])
        )
        if not languages or not set(languages).issubset(ALLOWED_LANGUAGES):
            return Response(
                {"languages": "Select at least one supported language"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            event = (
                MeetupEvent.objects.select_for_update()
                .filter(
                    pk=event_id,
                    is_published=True,
                    is_cancelled=False,
                    is_private_invitation=False,
                    date_time__gte=timezone.now(),
                )
                .prefetch_related("social_promotion_posts")
                .first()
            )
            if not event:
                return Response(
                    {"event_id": "Select a public, published upcoming event"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            unavailable_languages = set(languages) - set(
                _event_available_languages(event)
            )
            if unavailable_languages:
                return Response(
                    {
                        "languages": (
                            "The event has no stored title and description for: "
                            + ", ".join(sorted(unavailable_languages))
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            posts, created_count = _create_event_drafts(
                event=event,
                languages=languages,
                platforms=["facebook"],
                request=request,
            )

        return Response(
            {
                "posts": SocialPostSerializer(posts, many=True).data,
                "created_count": created_count,
                "reused_count": len(posts) - created_count,
                "copy_source": "event",
            },
            status=status.HTTP_201_CREATED if created_count else status.HTTP_200_OK,
        )


class SocialKpisSummaryView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response({"kpis": _kpi_snapshot()})


class SocialFeaturedProfilesView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(
            {
                "items": [
                    _profile_payload(profile) for profile in _eligible_profiles()[:20]
                ]
            }
        )


class SocialBufferProfilesView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        try:
            profiles = [
                profile
                for profile in list_buffer_profiles()
                if profile.get("service") in ALLOWED_PLATFORMS
            ]
        except BufferServiceError:
            logger.exception("Buffer channel discovery failed")
            return Response(
                {"error": BUFFER_PROFILES_ERROR},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"items": profiles})


class SocialExpandArticleView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        with transaction.atomic():
            try:
                post = SocialPost.objects.select_for_update().get(pk=pk)
            except SocialPost.DoesNotExist:
                return Response(
                    {"error": "Post not found"}, status=status.HTTP_404_NOT_FOUND
                )

            if post.article_id:
                existing = HubResource.objects.filter(pk=post.article_id).first()
                if existing:
                    return Response(
                        {
                            "article_id": str(existing.pk),
                            "title": existing.title,
                            "content": existing.summary,
                        }
                    )

            try:
                article = expand_social_post(
                    hook=post.hook,
                    pillar=post.pillar,
                    language=post.language,
                    content=post.content,
                )
            except ClaudeServiceError:
                logger.exception(
                    "Claude article expansion failed for social post %s", post.pk
                )
                return Response(
                    {"error": ARTICLE_GENERATION_ERROR},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            resource = HubResource.objects.create(
                title=article.title,
                summary=article.content,
                type=HubResource.Type.GUIDE,
                is_public=True,
            )
            post.article_id = str(resource.pk)
            post.save(update_fields=["article_id"])
            return Response(
                {
                    "article_id": str(resource.pk),
                    "title": resource.title,
                    "content": resource.summary,
                },
                status=status.HTTP_201_CREATED,
            )
