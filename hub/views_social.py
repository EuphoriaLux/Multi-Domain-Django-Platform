"""Staff-only social-media planning endpoints for hub.crush.lu."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from functools import partial

from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from crush_lu.models import CrushProfile, EventConnection, MeetupEvent

from .buffer_service import (
    BufferServiceError,
    create_buffer_update,
    list_buffer_profiles,
)
from .claude_service import ClaudeServiceError, expand_social_post, generate_social_copy
from .image_generator import (
    generate_event_flyer,
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

    new_members = CrushProfile.objects.filter(created_at__gte=week_ago).count()
    connections = EventConnection.objects.filter(requested_at__gte=week_ago).count()
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
        parity = f"{male}% H / {female}% F"
        if other:
            parity += f" / {other}% autre"
    else:
        parity = "N/D"

    return {
        "new_members_week": f"+{new_members}",
        "matches_created_week": str(connections),
        "parity_ratio": parity,
        "events_hosted_month": str(events),
    }


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
    return {
        "id": str(event.pk),
        "title": event.title,
        "event_type": event.get_event_type_display(),
        "date": event.date_time.isoformat(),
        "location": event.location,
        "image_url": image_url,
    }


class SocialPostsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        status_filter = request.query_params.get("status")
        queryset = SocialPost.objects.all()
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return Response({"items": SocialPostSerializer(queryset, many=True).data})

    def post(self, request):
        serializer = SocialPostSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        post = serializer.save(
            user=request.user,
            status_history=[_history_entry(request, SocialPost.Status.DRAFT)],
        )
        return Response(
            {"post": SocialPostSerializer(post).data}, status=status.HTTP_201_CREATED
        )


class SocialPostDetailView(APIView):
    permission_classes = [IsAdminUser]

    def patch(self, request, pk):
        try:
            post = SocialPost.objects.get(pk=pk)
        except SocialPost.DoesNotExist:
            return Response(
                {"error": "Post not found"}, status=status.HTTP_404_NOT_FOUND
            )

        requested_status = request.data.get("status", post.status)
        if requested_status == SocialPost.Status.SCHEDULED:
            scheduling_errors = {}
            if not request.data.get("scheduled_for", post.scheduled_for):
                scheduling_errors["scheduled_for"] = "Choose a publication time"
            if not request.data.get("buffer_profile_ids", post.buffer_profile_ids):
                scheduling_errors["buffer_profile_ids"] = "Select a Buffer channel"
            if not str(request.data.get("content", post.content)).strip():
                scheduling_errors["content"] = "Post content cannot be empty"
            if scheduling_errors:
                return Response(scheduling_errors, status=status.HTTP_400_BAD_REQUEST)

        old_status = post.status
        serializer = SocialPostSerializer(post, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
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
            except BufferServiceError as exc:
                updated_post.status = SocialPost.Status.FAILED
                history = list(updated_post.status_history or [])
                history.append(
                    _history_entry(request, SocialPost.Status.FAILED, note=str(exc))
                )
                updated_post.status_history = history
                updated_post.save(update_fields=["status", "status_history"])
                return Response(
                    {
                        "error": str(exc),
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
        warnings = []

        if category == "events":
            event_id = request.data.get("event_id")
            event = (
                MeetupEvent.objects.filter(
                    pk=event_id,
                    is_published=True,
                    is_cancelled=False,
                    date_time__gte=timezone.now(),
                ).first()
                if event_id
                else None
            )
            if not event:
                return Response(
                    {"event_id": "Select a published upcoming event"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            context = _event_payload(event, request)
            hook = event.title
            local_date = timezone.localtime(event.date_time)
            graphic = partial(
                generate_event_flyer,
                title=event.title,
                date_str=local_date.strftime("%d/%m/%Y · %H:%M"),
                location=event.location,
                background_url=context["image_url"] or None,
            )
        elif category == "kpis":
            context = _kpi_snapshot()
            stats = [
                {"value": context["new_members_week"], "label": "Nouveaux membres"},
                {
                    "value": context["matches_created_week"],
                    "label": "Connexions créées",
                },
                {"value": context["parity_ratio"], "label": "Répartition des profils"},
            ]
            graphic = partial(
                generate_kpi_card, title="CHIFFRES DE LA SEMAINE", stats=stats
            )
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
        except ClaudeServiceError as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        media_url = None
        if graphic:
            try:
                media_url = graphic()
            except Exception:
                logger.exception("Social graphic generation failed")
                warnings.append(
                    "Copy was generated, but the graphic renderer was unavailable"
                )

        scheduled_for = _next_friday_at_1600()
        posts = [
            SocialPost.objects.create(
                user=request.user,
                hook=hook,
                pillar=pillar,
                language=language,
                platforms=platforms,
                content=generated_copy[language],
                media_url=media_url,
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
        events = MeetupEvent.objects.filter(
            is_published=True,
            is_cancelled=False,
            date_time__gte=timezone.now(),
        ).order_by("date_time")[:20]
        return Response({"items": [_event_payload(event, request) for event in events]})


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
        except BufferServiceError as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return Response({"items": profiles})


class SocialExpandArticleView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        try:
            post = SocialPost.objects.get(pk=pk)
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
        except ClaudeServiceError as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
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
