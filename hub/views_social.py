"""
Social media management views for hub.crush.lu.

Handles post listings, AI batch generation, post updates & Buffer dispatch,
and conversion to long-form articles in Publications.
"""
import logging
from datetime import datetime, timedelta
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .buffer_service import create_buffer_update, list_buffer_profiles
from .models import HubResource, SocialPost
from .serializers import SocialPostSerializer

logger = logging.getLogger(__name__)


class SocialPostsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        status_filter = request.query_params.get("status")
        queryset = SocialPost.objects.all()
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        serializer = SocialPostSerializer(queryset, many=True)
        return Response({"items": serializer.data})

    def post(self, request):
        serializer = SocialPostSerializer(data=request.data)
        if serializer.is_valid():
            post = serializer.save(user=request.user)
            return Response({"post": SocialPostSerializer(post).data}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SocialPostDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        try:
            post = SocialPost.objects.get(pk=pk)
        except SocialPost.DoesNotExist:
            return Response({"error": "Post not found"}, status=status.HTTP_404_NOT_FOUND)

        new_status = request.data.get("status")
        serializer = SocialPostSerializer(post, data=request.data, partial=True)
        if serializer.is_valid():
            updated_post = serializer.save()

            # Record status history event if status changed
            if new_status and new_status != post.status:
                history = updated_post.status_history or []
                history.append({
                    "status": new_status,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "actor": request.user.username,
                })
                updated_post.status_history = history
                updated_post.save(update_fields=["status_history"])

            # If transition to 'scheduled' or 'approved', dispatch update to Buffer
            if new_status in ["scheduled", "approved"]:
                buf_res = create_buffer_update(
                    text=updated_post.content,
                    profile_ids=updated_post.buffer_profile_ids or [],
                    scheduled_at=updated_post.scheduled_for.isoformat() if updated_post.scheduled_for else None,
                    media_url=updated_post.media_url,
                )
                if buf_res.get("success"):
                    updated_post.status = "scheduled"
                    updated_post.buffer_id = buf_res.get("buffer_id", "")
                    updated_post.save(update_fields=["status", "buffer_id"])
                else:
                    updated_post.status = "failed"
                    updated_post.save(update_fields=["status"])

            return Response({"post": SocialPostSerializer(updated_post).data})
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SocialGenerateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        hook = request.data.get("hook", "Crush.lu Event")
        pillar = request.data.get("pillar", "event_recap")
        platforms = request.data.get("platforms", ["instagram", "facebook"])
        languages = request.data.get("languages", ["fr"])

        created_posts = []
        now = datetime.utcnow()

        for idx, lang in enumerate(languages):
            # Scheduled for next Friday 16:00
            scheduled_date = now + timedelta(days=(4 - now.weekday() + 7) % 7)
            scheduled_date = scheduled_date.replace(hour=16, minute=0, second=0, microsecond=0)

            sample_content = {
                "fr": f"🍷 {hook} — Retrouvez la communauté Crush.lu ce week-end ! Événements exclusifs, rencontres authentiques et moments inoubliables. Réservez votre place sur Crush.lu ✨ #CrushLu #Luxembourg #Rencontres",
                "en": f"🍷 {hook} — Join the Crush.lu community this weekend! Exclusive events, authentic connections and unforgettable moments. Book your spot at Crush.lu ✨ #CrushLu #Luxembourg #Dating",
                "de": f"🍷 {hook} — Schließen Sie sich diesem Wochenende der Crush.lu-Community an! Exklusive Events, authentische Begegnungen und unvergessliche Momente. Buchen Sie auf Crush.lu ✨ #CrushLu #Luxembourg",
            }

            content_text = sample_content.get(lang, sample_content["fr"])

            post = SocialPost.objects.create(
                user=request.user,
                hook=hook,
                pillar=pillar,
                language=lang,
                platforms=platforms,
                content=content_text,
                status="draft",
                scheduled_for=scheduled_date,
                status_history=[{
                    "status": "draft",
                    "timestamp": now.isoformat() + "Z",
                    "actor": "Claude AI Generator",
                }],
            )
            created_posts.append(post)

        serializer = SocialPostSerializer(created_posts, many=True)
        return Response({"posts": serializer.data}, status=status.HTTP_201_CREATED)


class SocialBufferProfilesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        profiles = list_buffer_profiles()
        return Response({"items": profiles})


class SocialExpandArticleView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            post = SocialPost.objects.get(pk=pk)
        except SocialPost.DoesNotExist:
            return Response({"error": "Post not found"}, status=status.HTTP_404_NOT_FOUND)

        title = f"Article Marketing: {post.hook or 'Crush.lu Focus'}"
        summary = f"Article détaillé généré à partir de la publication sociale [{post.pillar}].\n\n{post.content}"

        resource = HubResource.objects.create(
            title=title,
            summary=summary,
            type=HubResource.Type.GUIDE,
            is_public=True,
        )

        post.article_id = str(resource.id)
        post.save(update_fields=["article_id"])

        return Response({
            "article_id": str(resource.id),
            "title": resource.title,
            "content": resource.summary,
        })
