"""Staff-only social-media planning endpoints for hub.crush.lu."""

from __future__ import annotations

import logging
import os
import re
from secrets import compare_digest
from io import BytesIO
from datetime import datetime, timedelta
from functools import partial
from urllib.parse import urlparse

from django.conf import settings
from django.core.files.storage import storages
from django.db import connection, transaction
from django.db.models import Count, TextField
from django.db.models.functions import Cast
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

from crush_lu.models import CrushProfile, EventConnection, MeetupEvent
from crush_lu.utils.i18n import build_absolute_url

from .buffer_service import (
    BufferAuthError,
    BufferDeliveryUnknown,
    BufferPartialFailure,
    BufferServiceError,
    create_buffer_update,
    list_buffer_profiles,
)
from .claude_service import ClaudeServiceError, expand_social_post, generate_social_copy
from .constants import SOCIAL_CONTENT_MAX_LENGTH
from .image_generator import (
    generate_kpi_card,
    generate_profile_card,
)
from .models import HubResource, SocialPost
from .serializers import SocialPostSerializer
from .social_planning import (
    REVIEW_FIELDS,
    posting_proposal,
    reserved_times,
    review_fingerprint,
    source_deadline,
)

logger = logging.getLogger(__name__)

ALLOWED_CATEGORIES = {"events", "kpis", "profiles", "tips", "recaps"}
ALLOWED_PLATFORMS = {"instagram", "facebook", "linkedin"}
ALLOWED_LANGUAGES = {choice for choice, _label in SocialPost.Language.choices}
ALLOWED_PILLARS = {choice for choice, _label in SocialPost.Pillar.choices}

BUFFER_SCHEDULE_ERROR = "Buffer could not schedule this post. Try again later."
COPY_GENERATION_ERROR = "Social copy generation is temporarily unavailable."
BUFFER_PROFILES_ERROR = "Buffer channels are temporarily unavailable."
BUFFER_AUTH_ERROR = (
    "Buffer rejected the configured API key. Update BUFFER_API_KEY, then retry."
)
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
BUFFER_PLATFORM_SCOPE_ERROR = (
    "Every selected Buffer channel must be mapped to one of this post's platforms."
)
SCHEDULED_EDIT_ERROR = (
    "Delivery fields cannot be changed after a post has been scheduled in Buffer."
)
SCHEDULING_FIELDS = {
    "content",
    "scheduled_for",
    "media_url",
    "media_urls",
    "source_metadata",
    "platforms",
    "buffer_profile_ids",
    "buffer_profile_platforms",
}
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
    # A partial Buffer failure leaves the post FAILED even though Facebook is
    # already live, and FAILED ranks below DRAFT — so a newer draft would win
    # max() and report the event as unpromoted while the publication exists.
    # Read the delivery that actually happened, not the overall status.
    dispatched_posts = [
        post
        for post in facebook_posts
        if "facebook" in _event_post_dispatched_platforms(post)
    ]
    promotion_post = max(
        dispatched_posts or facebook_posts,
        key=lambda post: (PROMOTION_STATUS_RANK.get(post.status, 0), post.created_at),
        default=None,
    )
    available_languages = _event_available_languages(event)
    event_url_language = available_languages[0] if available_languages else "fr"

    return {
        "id": str(event.pk),
        "title": event.title,
        "event_type": event.get_event_type_display(),
        "date": event.date_time.isoformat(),
        "location": event.location,
        "image_url": image_url,
        "event_url": build_absolute_url(
            "crush_lu:event_detail",
            lang=event_url_language,
            kwargs={"event_id": event.pk},
        ),
        "available_languages": available_languages,
        "promotion_post_id": str(promotion_post.pk) if promotion_post else None,
        "promotion_status": promotion_post.status if promotion_post else "not_started",
        "is_promoted": bool(dispatched_posts),
    }


def _event_available_languages(event: MeetupEvent) -> list[str]:
    return [
        language
        for language in (
            SocialPost.Language.FR,
            SocialPost.Language.EN,
            SocialPost.Language.DE,
        )
        if str(getattr(event, f"title_{language}", "") or "").strip()
        and str(getattr(event, f"description_{language}", "") or "").strip()
    ]


def _event_value(event: MeetupEvent, field: str, language: str) -> str:
    """Return stored event copy without generating or paraphrasing it."""

    return str(getattr(event, f"{field}_{language}", "") or "").strip()


def _unique_string_list(value) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return list(dict.fromkeys(value))


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


def _event_copy_validation_error(
    event: MeetupEvent, languages: list[str]
) -> str | None:
    unavailable_languages = set(languages) - set(_event_available_languages(event))
    if unavailable_languages:
        return "The event has no stored title and description for: " + ", ".join(
            sorted(unavailable_languages)
        )
    oversized_languages = [
        language
        for language in languages
        if len(_event_post_content(event, language)) > SOCIAL_CONTENT_MAX_LENGTH
    ]
    if oversized_languages:
        return (
            f"The stored event copy exceeds {SOCIAL_CONTENT_MAX_LENGTH} characters for: "
            + ", ".join(oversized_languages)
        )
    return None


def _event_post_is_dispatched(post: SocialPost) -> bool:
    return (
        post.status in PROMOTED_STATUSES
        or bool(post.buffer_id)
        or post.buffer_delivery_uncertain
    )


def _event_post_dispatched_platforms(post: SocialPost) -> set[str]:
    if post.dispatched_platforms:
        return set(post.dispatched_platforms)
    if post.status in PROMOTED_STATUSES or post.buffer_delivery_uncertain:
        # An uncertain delivery may already exist in Buffer until reconciled.
        return set(post.platforms or [])
    return set()


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
    superseded_urls = []
    if post.media_url != refreshed["media_url"]:
        # A stale ordered deck would otherwise override the refreshed image.
        superseded_urls = [*(post.media_urls or []), post.media_url or ""]
        refreshed["media_urls"] = []
    if post.platforms != platforms:
        refreshed["buffer_profile_ids"] = []
        refreshed["buffer_profile_platforms"] = {}
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
    if superseded_urls:
        transaction.on_commit(
            partial(_delete_superseded_social_blobs, superseded_urls, [])
        )
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
    event_posts = list(
        SocialPost.objects.select_for_update()
        .filter(source_event=event)
        .order_by("created_at")
    )
    for language in languages:
        language_posts = [post for post in event_posts if post.language == language]
        dispatched_posts = [
            post for post in language_posts if _event_post_is_dispatched(post)
        ]
        dispatched_platforms = {
            platform
            for post in dispatched_posts
            for platform in _event_post_dispatched_platforms(post)
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


_FORMAT_EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg"}
_DECK_FORMATS = set(_FORMAT_EXTENSIONS)
_SOCIAL_BLOB_RE = re.compile(r"/(social/ai_[A-Za-z0-9_.]+)$")


def _uploaded_social_images(request):
    """Validate all uploaded files before writing any to public storage."""
    deck = request.FILES.getlist("images")
    legacy = request.FILES.get("image") or request.FILES.get("media")
    images = deck or ([legacy] if legacy else [])
    if len(images) > 5:
        raise ValidationError({"images": "At most five images are supported."})
    if not deck:
        # Legacy single-file callers keep their original bytes and extension.
        for image in images:
            image.detected_extension = os.path.splitext(image.name)[1].lower() or ".jpg"
        return images
    dimensions = []
    for image in images:
        if image.size > 8 * 1024 * 1024:
            raise ValidationError({"images": "Each image must be under 8 MiB."})
        try:
            with Image.open(BytesIO(image.read())) as decoded:
                if decoded.format not in _DECK_FORMATS:
                    raise ValueError("Unsupported image")
                image.detected_extension = _FORMAT_EXTENSIONS[decoded.format]
                dimensions.append(decoded.size)
                decoded.verify()
        except (
            UnidentifiedImageError,
            Image.DecompressionBombError,
            OSError,
            ValueError,
        ) as exc:
            raise ValidationError({"images": "Use valid PNG or JPEG files."}) from exc
        finally:
            image.seek(0)
    if any(size != (1080, 1080) for size in dimensions):
        raise ValidationError({"images": "Carousel slides must be 1080×1080."})
    return images


def _social_blob_still_referenced(url):
    """True when any post still points at this file as its cover or in a deck."""
    return (
        SocialPost.objects.filter(media_url=url).exists()
        or SocialPost.objects.annotate(deck=Cast("media_urls", TextField()))
        .filter(deck__contains=url)
        .exists()
    )


def _delete_superseded_social_blobs(old_urls, new_urls):
    """Remove deck files replaced by a successful regeneration (best effort)."""
    storage = storages["crush_media"]
    for url in set(old_urls) - set(new_urls):
        match = _SOCIAL_BLOB_RE.search(urlparse(url).path)
        if not match:
            continue
        if _social_blob_still_referenced(url):
            continue
        try:
            storage.delete(match.group(1))
        except Exception:
            logger.warning("Could not delete superseded social blob %s", url)


_REVIEWED_FIELDS = REVIEW_FIELDS


def _social_review_fingerprint(post):
    return review_fingerprint(post)


def _save_social_images(images):
    storage = storages["crush_media"]
    paths, urls = [], []
    try:
        for image in images:
            ext = image.detected_extension
            filename = f"social/ai_{timezone.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(8).hex()}{ext}"
            path = storage.save(filename, image)
            paths.append(path)
            url = storage.url(path)
            if url.startswith("/"):
                url = f"{settings.BACKEND_BASE_URL.rstrip('/')}{url}"
            urls.append(url)
    except Exception:
        for path in paths:
            storage.delete(path)
        raise
    return urls


# Arbitrary constant key for the Postgres advisory lock guarding slot allocation.
_SLOT_ALLOCATION_LOCK = 0x48554253


class SocialPostsView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        status_filter = request.query_params.get("status")
        queryset = SocialPost.objects.select_related(
            "user", "featured_profile", "source_event"
        )
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return Response(
            {
                "items": SocialPostSerializer(
                    queryset, many=True, context={"reserved_times": reserved_times()}
                ).data
            }
        )

    @transaction.atomic
    def post(self, request):
        serializer = SocialPostSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        requested_status = request.data.get("status")
        initial_status = (
            SocialPost.Status.PENDING_REVIEW
            if requested_status == SocialPost.Status.PENDING_REVIEW
            else SocialPost.Status.DRAFT
        )

        uploaded_images = _uploaded_social_images(request)
        defaults = {
            **serializer.validated_data,
            "user": request.user,
            "status": initial_status,
            "status_history": [
                _history_entry(request, initial_status, note="Created via Hub API.")
            ],
        }
        key = defaults.pop("generation_key", None)
        if key:
            post, created = SocialPost.objects.get_or_create(
                generation_key=key, defaults=defaults
            )
            if not created:
                if post.user_id != request.user.pk:
                    return Response(
                        {"error": "Automation key already exists."}, status=409
                    )
                return Response(
                    {"post": SocialPostSerializer(post).data, "cached": True}
                )
        else:
            post = SocialPost.objects.create(**defaults)
        if uploaded_images:
            urls = _save_social_images(uploaded_images)
            post.media_url, post.media_urls = urls[0], urls
        if not post.scheduled_for:
            if connection.vendor == "postgresql":
                # Two concurrent intakes must not pick the same free slot: hold
                # a transaction-scoped lock until this post's time is committed.
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(%s)", [_SLOT_ALLOCATION_LOCK]
                    )
            proposal = posting_proposal(post)
            if proposal["scheduled_for"]:
                post.scheduled_for = datetime.fromisoformat(proposal["scheduled_for"])
                post.source_metadata = {
                    **post.source_metadata,
                    "posting_reason": proposal["reason"],
                }
        post.save()
        return Response(
            {"post": SocialPostSerializer(post).data}, status=status.HTTP_201_CREATED
        )


class SocialPostDetailView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request, pk):
        try:
            post = SocialPost.objects.get(pk=pk)
        except SocialPost.DoesNotExist:
            return Response({"error": "Post not found"}, status=404)
        return Response({"post": SocialPostSerializer(post).data})

    @transaction.atomic
    def patch(self, request, pk):
        try:
            post = SocialPost.objects.select_for_update().get(pk=pk)
        except SocialPost.DoesNotExist:
            return Response(
                {"error": "Post not found"}, status=status.HTTP_404_NOT_FOUND
            )

        expected = request.data.get("review_fingerprint")
        for supplied in (expected, request.data.get("edit_fingerprint")):
            if supplied is not None and (
                not isinstance(supplied, str)
                or not compare_digest(
                    supplied.encode(), _social_review_fingerprint(post).encode()
                )
            ):
                return Response(
                    {"error": "Post changed since this review."}, status=409
                )

        requested_status = request.data.get("status", post.status)
        serializer = SocialPostSerializer(post, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        uploaded_images = _uploaded_social_images(request)
        if expected is not None:
            # The fingerprint authorizes exactly what was reviewed, so a
            # request that also alters reviewed fields is not covered by it.
            altered = uploaded_images or any(
                field in serializer.validated_data
                and serializer.validated_data[field] != getattr(post, field)
                for field in _REVIEWED_FIELDS
            )
            if altered:
                return Response(
                    {"error": "Reviewed content cannot change with a fingerprint."},
                    status=409,
                )
        if uploaded_images:
            if (
                post.status in PROMOTED_STATUSES
                or post.buffer_id
                or post.buffer_delivery_uncertain
            ):
                return Response({"error": SCHEDULED_EDIT_ERROR}, status=409)
            if requested_status == SocialPost.Status.SCHEDULED:
                return Response(
                    {"error": "Review replacement images before scheduling."},
                    status=400,
                )
        if requested_status == SocialPost.Status.SCHEDULED:
            if (
                post.buffer_id or post.buffer_delivery_uncertain
            ) and post.status != SocialPost.Status.SCHEDULED:
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
            elif (
                post.status != SocialPost.Status.SCHEDULED
                and scheduled_for <= timezone.now()
            ):
                scheduling_errors["scheduled_for"] = "Choose a future publication time"
            elif source_deadline(
                post, serializer.validated_data.get("source_metadata")
            ) and scheduled_for >= source_deadline(
                post, serializer.validated_data.get("source_metadata")
            ):
                scheduling_errors["scheduled_for"] = (
                    "Publication must precede the source event day"
                )
            selected_profile_ids = serializer.validated_data.get(
                "buffer_profile_ids", post.buffer_profile_ids
            )
            profile_platforms = serializer.validated_data.get(
                "buffer_profile_platforms", post.buffer_profile_platforms
            )
            effective_platforms = serializer.validated_data.get(
                "platforms", post.platforms
            )
            if scheduling_errors:
                return Response(scheduling_errors, status=status.HTTP_400_BAD_REQUEST)
            if (
                request.data.get("approval_mode") == "hub"
                and post.status != SocialPost.Status.SCHEDULED
            ):
                if not expected:
                    return Response(
                        {"error": "Refresh the review before approving."}, status=400
                    )
                try:
                    channels = {p["id"]: p for p in list_buffer_profiles()}
                except BufferServiceError:
                    return Response({"error": BUFFER_PROFILES_ERROR}, status=503)
                if any(
                    channel_id not in channels
                    or any(
                        channels[channel_id].get(k)
                        for k in ("is_queue_paused", "is_disconnected", "is_locked")
                    )
                    for channel_id in selected_profile_ids
                ):
                    return Response(
                        {
                            "error": "A selected Buffer account is unavailable. Refresh its connection."
                        },
                        status=409,
                    )
                profile_platforms = {
                    channel_id: channels[channel_id]["service"]
                    for channel_id in selected_profile_ids
                }
                serializer.validated_data["buffer_profile_platforms"] = (
                    profile_platforms
                )
            if not selected_profile_ids:
                scheduling_errors["buffer_profile_ids"] = "Select a Buffer channel"
            elif all(
                profile_id in profile_platforms for profile_id in selected_profile_ids
            ):
                # The mapping is what dispatched_platforms is recorded from, so
                # a channel mapped outside this post's own platforms would book
                # coverage the publication never had — and leave the platform it
                # really went to looking unpromoted, i.e. duplicable.
                mapped_platforms = {
                    profile_platforms[profile_id] for profile_id in selected_profile_ids
                }
                if not effective_platforms and mapped_platforms:
                    # Only the platforms a post may declare; a channel on any
                    # other service then fails the scope check below.
                    effective_platforms = sorted(mapped_platforms & ALLOWED_PLATFORMS)
                    serializer.validated_data["platforms"] = effective_platforms
                if not mapped_platforms.issubset(set(effective_platforms or [])):
                    scheduling_errors["buffer_profile_platforms"] = (
                        BUFFER_PLATFORM_SCOPE_ERROR
                    )
            elif len(effective_platforms or []) == 1:
                serializer.validated_data["buffer_profile_platforms"] = {
                    profile_id: effective_platforms[0]
                    for profile_id in selected_profile_ids
                }
            elif linked_event:
                scheduling_errors["buffer_profile_platforms"] = (
                    "Identify the platform for every selected Buffer channel"
                )
            if not str(request.data.get("content", post.content)).strip():
                scheduling_errors["content"] = "Post content cannot be empty"
            if scheduling_errors:
                return Response(scheduling_errors, status=status.HTTP_400_BAD_REQUEST)

        old_status = post.status
        if (
            post.status in PROMOTED_STATUSES
            or post.buffer_id
            or post.buffer_delivery_uncertain
        ):
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
        if uploaded_images:
            urls = _save_social_images(uploaded_images)
            old_urls = [*(post.media_urls or []), post.media_url or ""]
            transaction.on_commit(
                partial(_delete_superseded_social_blobs, old_urls, urls)
            )
            serializer.validated_data.update(media_url=urls[0], media_urls=urls)
        updated_post = serializer.save()
        new_status = updated_post.status

        if new_status != old_status:
            history = list(updated_post.status_history or [])
            history.append(_history_entry(request, new_status))
            updated_post.status_history = history
            updated_post.save(update_fields=["status_history"])

        if new_status == SocialPost.Status.SCHEDULED and new_status != old_status:
            profile_platforms = dict(updated_post.buffer_profile_platforms or {})
            selected_ids = updated_post.buffer_profile_ids or []
            lookup_error = None
            unresolved_ids = [
                profile_id
                for profile_id in selected_ids
                if profile_id not in profile_platforms
            ]
            if unresolved_ids:
                # The common case: multiple channels, no explicit mapping --
                # the frontend never sends buffer_profile_platforms today, so
                # this is where most scheduled posts land (the validation
                # block above already resolves the single-declared-platform
                # case into updated_post.buffer_profile_platforms before we
                # get here, so unresolved_ids is never that case). Ask Buffer
                # which service each channel actually is so
                # _create_channel_post still has a platform to key off (e.g.
                # the Facebook post-type metadata this fallback exists to
                # attach) -- this is more reliable than assuming a single
                # post-level platform applies to every selected channel
                # anyway. A lookup failure must not block scheduling --
                # that's exactly the pre-fix behaviour, just without the
                # metadata.
                #
                # This runs inside the row lock this view already holds for
                # create_buffer_update below (patch() is transaction.atomic
                # over a select_for_update() row) -- doubling worst-case lock
                # time under a slow Buffer API rather than adding a new class
                # of risk. Only previously-broken schedule requests (the ones
                # this fix targets) pay for the extra call.
                try:
                    known_services = {
                        profile["id"]: profile.get("service", "")
                        for profile in list_buffer_profiles()
                    }
                except BufferServiceError as exc:
                    lookup_error = exc
                    logger.warning(
                        "Could not resolve Buffer channel platforms for "
                        "post %s; scheduling without platform-specific "
                        "metadata.",
                        updated_post.pk,
                    )
                    known_services = {}
                for profile_id in unresolved_ids:
                    service = known_services.get(profile_id)
                    if service:
                        profile_platforms[profile_id] = service
                # Persist what was actually resolved -- dispatched_platforms
                # below (and any later anti-abuse re-validation of this post)
                # reads updated_post.buffer_profile_platforms, not the local
                # dict, so the resolution must land on the instance too.
                updated_post.buffer_profile_platforms = profile_platforms
                # A platformless post scheduled with only channel ids reaches
                # here with platforms=[] -- delivery fields are immutable once
                # scheduled, so record what the lookup resolved now.
                if not updated_post.platforms:
                    updated_post.platforms = sorted(
                        {
                            profile_platforms[profile_id]
                            for profile_id in selected_ids
                            if profile_platforms.get(profile_id) in ALLOWED_PLATFORMS
                        }
                    )
            try:
                result = create_buffer_update(
                    text=updated_post.content,
                    profile_ids=updated_post.buffer_profile_ids or [],
                    profile_platforms=profile_platforms,
                    scheduled_at=updated_post.scheduled_for.isoformat(),
                    media_url=updated_post.media_url,
                    media_urls=updated_post.media_urls,
                    require_resolved_platforms=True,
                )
            except BufferPartialFailure as exc:
                logger.exception(
                    "Buffer scheduling partially failed for social post %s",
                    updated_post.pk,
                )
                updated_post.status = SocialPost.Status.FAILED
                updated_post.buffer_id = ",".join(exc.created_post_ids)
                updated_post.dispatched_platforms = list(
                    dict.fromkeys(
                        updated_post.buffer_profile_platforms[profile_id]
                        for profile_id in exc.created_profile_ids
                        if profile_id in updated_post.buffer_profile_platforms
                    )
                )
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
                    update_fields=[
                        "status",
                        "buffer_id",
                        "platforms",
                        "buffer_profile_platforms",
                        "dispatched_platforms",
                        "status_history",
                    ]
                )
                return Response(
                    {
                        "error": BUFFER_PARTIAL_ERROR,
                        "post": SocialPostSerializer(updated_post).data,
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            except BufferServiceError as exc:
                # A rejected key found by the channel lookup must stay an auth
                # failure even when the preflight raised first.
                if isinstance(exc, BufferAuthError) or isinstance(
                    lookup_error, BufferAuthError
                ):
                    schedule_error = BUFFER_AUTH_ERROR
                    logger.error(
                        "Buffer scheduling failed for social post %s: "
                        "credential rejected",
                        updated_post.pk,
                    )
                else:
                    schedule_error = BUFFER_SCHEDULE_ERROR
                    logger.exception(
                        "Buffer scheduling failed for social post %s", updated_post.pk
                    )
                updated_post.status = SocialPost.Status.FAILED
                updated_post.buffer_delivery_uncertain = isinstance(
                    exc, BufferDeliveryUnknown
                )
                if updated_post.buffer_delivery_uncertain:
                    schedule_error = "Buffer delivery is uncertain. Reconcile in Buffer before retrying."
                history = list(updated_post.status_history or [])
                history.append(
                    _history_entry(
                        request,
                        SocialPost.Status.FAILED,
                        note=schedule_error,
                    )
                )
                updated_post.status_history = history
                updated_post.save(
                    update_fields=[
                        "status",
                        "status_history",
                        "buffer_delivery_uncertain",
                        "platforms",
                        "buffer_profile_platforms",
                    ]
                )
                return Response(
                    {
                        "error": schedule_error,
                        "post": SocialPostSerializer(updated_post).data,
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )

            updated_post.buffer_id = result["buffer_id"]
            successful_profile_ids = result.get(
                "created_profile_ids", updated_post.buffer_profile_ids
            )
            updated_post.dispatched_platforms = list(
                dict.fromkeys(
                    updated_post.buffer_profile_platforms[profile_id]
                    for profile_id in successful_profile_ids
                    if profile_id in updated_post.buffer_profile_platforms
                )
            )
            updated_post.save(
                update_fields=[
                    "buffer_id",
                    "platforms",
                    "buffer_profile_platforms",
                    "dispatched_platforms",
                ]
            )

        return Response({"post": SocialPostSerializer(updated_post).data})


class SocialPlanningSlotView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        # Force a real database-column check before advertising the intake contract.
        SocialPost.objects.values("generation_key", "source_metadata").first()
        requested = request.query_params.get("posting_date")
        if requested:
            try:
                target = datetime.strptime(requested, "%Y-%m-%d").date()
                today = timezone.localdate()
                if target < today or target > today + timedelta(days=90):
                    raise ValueError
            except ValueError:
                return Response(
                    {"error": "posting_date must be today or within 90 days"},
                    status=400,
                )
        try:
            profiles = list_buffer_profiles()
        except BufferServiceError:
            profiles = []
        proposal = posting_proposal(posting_date=requested, profiles=profiles)
        if not proposal["scheduled_for"]:
            return Response({"error": proposal["reason"]}, status=503)
        return Response(
            {
                "intake_version": 1,
                "review_in_hub": True,
                "posting_date": proposal["scheduled_for"][:10],
                **proposal,
            }
        )


class SocialGenerateView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        category = request.data.get("category", "events")
        hook = str(request.data.get("hook", "")).strip()
        pillar = request.data.get("pillar", SocialPost.Pillar.EVENT_RECAP)
        platforms = _unique_string_list(request.data.get("platforms"))
        languages = _unique_string_list(request.data.get("languages"))

        errors = {}
        if category not in ALLOWED_CATEGORIES:
            errors["category"] = "Unsupported category"
        if not hook or len(hook) > 255:
            errors["hook"] = "Provide a hook between 1 and 255 characters"
        if pillar not in ALLOWED_PILLARS:
            errors["pillar"] = "Unsupported pillar"
        if (
            platforms is None
            or not platforms
            or not set(platforms).issubset(ALLOWED_PLATFORMS)
        ):
            errors["platforms"] = "Select at least one supported platform"
        if (
            languages is None
            or not languages
            or not set(languages).issubset(ALLOWED_LANGUAGES)
        ):
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
                    .first()
                    if event_id
                    else None
                )
                if not event:
                    return Response(
                        {"event_id": "Select a public, published upcoming event"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                copy_error = _event_copy_validation_error(event, languages)
                if copy_error:
                    return Response(
                        {"languages": copy_error},
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
        languages = _unique_string_list(
            request.data.get("languages", [SocialPost.Language.FR])
        )
        if (
            languages is None
            or not languages
            or not set(languages).issubset(ALLOWED_LANGUAGES)
        ):
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
                .first()
            )
            if not event:
                return Response(
                    {"event_id": "Select a public, published upcoming event"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            copy_error = _event_copy_validation_error(event, languages)
            if copy_error:
                return Response(
                    {"languages": copy_error},
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
        except BufferAuthError:
            logger.error("Buffer channel discovery failed: credential rejected")
            return Response(
                {"error": BUFFER_AUTH_ERROR},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
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
