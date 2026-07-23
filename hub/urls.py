from django.urls import path

from . import views
from .views_whatsapp import (
    WhatsAppInboxReadView,
    WhatsAppInboxView,
    WhatsAppMessagesView,
    WhatsAppSendView,
    WhatsAppTemplatesView,
)
from .views_social import (
    SocialBufferProfilesView,
    SocialExpandArticleView,
    SocialGenerateView,
    SocialPostDetailView,
    SocialPostsView,
)

app_name = "hub"

urlpatterns = [
    path("me", views.MeView.as_view(), name="me"),
    path("me/", views.MeView.as_view()),
    path("requests", views.RequestsView.as_view(), name="requests"),
    path("requests/", views.RequestsView.as_view()),
    path("resources", views.ResourcesView.as_view(), name="resources"),
    path("resources/", views.ResourcesView.as_view()),
    path("timeline", views.TimelineView.as_view(), name="timeline"),
    path("timeline/", views.TimelineView.as_view()),

    # Social Media Marketing Routes
    path("social/posts", SocialPostsView.as_view(), name="social_posts"),
    path("social/posts/", SocialPostsView.as_view()),
    path("social/posts/<int:pk>", SocialPostDetailView.as_view(), name="social_post_detail"),
    path("social/posts/<int:pk>/", SocialPostDetailView.as_view()),
    path("social/generate", SocialGenerateView.as_view(), name="social_generate"),
    path("social/generate/", SocialGenerateView.as_view()),
    path("social/buffer-profiles", SocialBufferProfilesView.as_view(), name="social_buffer_profiles"),
    path("social/buffer-profiles/", SocialBufferProfilesView.as_view()),
    path("social/posts/<int:pk>/expand-article", SocialExpandArticleView.as_view(), name="social_expand_article"),
    path("social/posts/<int:pk>/expand-article/", SocialExpandArticleView.as_view()),

    path(
        "whatsapp/templates",
        WhatsAppTemplatesView.as_view(),
        name="whatsapp_templates",
    ),
    path("whatsapp/templates/", WhatsAppTemplatesView.as_view()),
    path("whatsapp/send", WhatsAppSendView.as_view(), name="whatsapp_send"),
    path("whatsapp/send/", WhatsAppSendView.as_view()),
    path(
        "whatsapp/messages",
        WhatsAppMessagesView.as_view(),
        name="whatsapp_messages",
    ),
    path("whatsapp/messages/", WhatsAppMessagesView.as_view()),
    path("whatsapp/inbox", WhatsAppInboxView.as_view(), name="whatsapp_inbox"),
    path("whatsapp/inbox/", WhatsAppInboxView.as_view()),
    path(
        "whatsapp/inbox/read",
        WhatsAppInboxReadView.as_view(),
        name="whatsapp_inbox_read",
    ),
    path("whatsapp/inbox/read/", WhatsAppInboxReadView.as_view()),
]
