from django.urls import path

from . import views
from .views_event_links import EventPartnerLinkView, PartnerEventsView
from .views_events import (
    EventCancellationDetailView,
    EventCancellationsView,
)
from .views_partners import (
    OfferEventDraftView,
    PartnerDetailView,
    PartnerOfferDetailView,
    PartnerOffersView,
    PartnerOnboardingView,
    PartnersView,
)
from .views_staffing import (
    EventAvailabilitiesView,
    EventAvailabilityDeclareView,
    EventAvailabilityStatusView,
    HubEventsView,
)
from .views_finance import (
    PaymentsInView,
    PaymentsOutView,
    PayrollView,
    RefundsView,
)
from .views_whatsapp import (
    WhatsAppInboxReadView,
    WhatsAppInboxView,
    WhatsAppMessagesView,
    WhatsAppSendView,
    WhatsAppTemplatesView,
)
from .views_social import (
    SocialBufferProfilesView,
    SocialEventDraftsView,
    SocialExpandArticleView,
    SocialFeaturedProfilesView,
    SocialGenerateView,
    SocialKpisSummaryView,
    SocialPostDetailView,
    SocialPlanningSlotView,
    SocialPostsView,
    SocialUpcomingEventsView,
)
from .views_team import TeamMembersView
from .views_analytics import AnalyticsOverviewView

app_name = "hub"

urlpatterns = [
    path(
        "locations/<int:pk>/events", PartnerEventsView.as_view(), name="partner_events"
    ),
    path("locations/<int:pk>/events/", PartnerEventsView.as_view()),
    path(
        "events/<int:pk>/partner-link",
        EventPartnerLinkView.as_view(),
        name="event_partner_link",
    ),
    path("events/<int:pk>/partner-link/", EventPartnerLinkView.as_view()),
    path("events", HubEventsView.as_view(), name="hub_events"),
    path("events/", HubEventsView.as_view()),
    path(
        "events/availabilities",
        EventAvailabilitiesView.as_view(),
        name="hub_event_availabilities",
    ),
    path("events/availabilities/", EventAvailabilitiesView.as_view()),
    path(
        "events/<int:event_id>/availability",
        EventAvailabilityDeclareView.as_view(),
        name="hub_event_availability_declare",
    ),
    path("events/<int:event_id>/availability/", EventAvailabilityDeclareView.as_view()),
    path(
        "events/<int:event_id>/availability/<str:availability_id>",
        EventAvailabilityStatusView.as_view(),
        name="hub_event_availability_status",
    ),
    path(
        "events/<int:event_id>/availability/<str:availability_id>/",
        EventAvailabilityStatusView.as_view(),
    ),
    path("me", views.MeView.as_view(), name="me"),
    path("me/", views.MeView.as_view()),
    path("requests", views.RequestsView.as_view(), name="requests"),
    path("requests/", views.RequestsView.as_view()),
    path("resources", views.ResourcesView.as_view(), name="resources"),
    path("resources/", views.ResourcesView.as_view()),
    path("timeline", views.TimelineView.as_view(), name="timeline"),
    path("timeline/", views.TimelineView.as_view()),
    # Partners (hub.Location): CRUD, their offers and the onboarding checklist.
    path("locations", PartnersView.as_view(), name="locations"),
    path("locations/", PartnersView.as_view()),
    path("locations/<int:pk>", PartnerDetailView.as_view(), name="location_detail"),
    path("locations/<int:pk>/", PartnerDetailView.as_view()),
    path(
        "locations/<int:pk>/offers",
        PartnerOffersView.as_view(),
        name="location_offers",
    ),
    path("locations/<int:pk>/offers/", PartnerOffersView.as_view()),
    path(
        "locations/<int:pk>/offers/<int:offer_pk>",
        PartnerOfferDetailView.as_view(),
        name="location_offer_detail",
    ),
    path(
        "locations/<int:pk>/offers/<int:offer_pk>/",
        PartnerOfferDetailView.as_view(),
    ),
    path(
        "locations/<int:pk>/onboarding",
        PartnerOnboardingView.as_view(),
        name="location_onboarding",
    ),
    path("locations/<int:pk>/onboarding/", PartnerOnboardingView.as_view()),
    path(
        "offers/<int:pk>/event-draft",
        OfferEventDraftView.as_view(),
        name="offer_event_draft",
    ),
    path("offers/<int:pk>/event-draft/", OfferEventDraftView.as_view()),
    path("team", TeamMembersView.as_view(), name="team"),
    path("team/", TeamMembersView.as_view()),
    # Aggregate analytics only: provider credentials and member rows stay server-side.
    path(
        "analytics/overview",
        AnalyticsOverviewView.as_view(),
        name="analytics_overview",
    ),
    path("analytics/overview/", AnalyticsOverviewView.as_view()),
    # Financials & Accounting Routes
    path("payments-in", PaymentsInView.as_view(), name="payments_in"),
    path("payments-in/", PaymentsInView.as_view()),
    path("payments-out", PaymentsOutView.as_view(), name="payments_out"),
    path("payments-out/", PaymentsOutView.as_view()),
    path("payroll", PayrollView.as_view(), name="payroll"),
    path("payroll/", PayrollView.as_view()),
    path("refunds", RefundsView.as_view(), name="refunds"),
    path("refunds/", RefundsView.as_view()),
    # Event Cancellation Reporting Routes (read-only, live-read from crush_lu)
    path(
        "events/cancelled",
        EventCancellationsView.as_view(),
        name="event_cancellations",
    ),
    path("events/cancelled/", EventCancellationsView.as_view()),
    path(
        "events/<int:pk>/cancellation",
        EventCancellationDetailView.as_view(),
        name="event_cancellation_detail",
    ),
    path("events/<int:pk>/cancellation/", EventCancellationDetailView.as_view()),
    # Social Media Marketing Routes
    path("social/posts", SocialPostsView.as_view(), name="social_posts"),
    path("social/posts/", SocialPostsView.as_view()),
    path(
        "social/planning-slot",
        SocialPlanningSlotView.as_view(),
        name="social_planning_slot",
    ),
    path("social/planning-slot/", SocialPlanningSlotView.as_view()),
    path(
        "social/posts/<int:pk>",
        SocialPostDetailView.as_view(),
        name="social_post_detail",
    ),
    path("social/posts/<int:pk>/", SocialPostDetailView.as_view()),
    path("social/generate", SocialGenerateView.as_view(), name="social_generate"),
    path("social/generate/", SocialGenerateView.as_view()),
    path(
        "social/upcoming-events",
        SocialUpcomingEventsView.as_view(),
        name="social_upcoming_events",
    ),
    path("social/upcoming-events/", SocialUpcomingEventsView.as_view()),
    path(
        "social/upcoming-events/<int:event_id>/drafts",
        SocialEventDraftsView.as_view(),
        name="social_event_drafts",
    ),
    path(
        "social/upcoming-events/<int:event_id>/drafts/",
        SocialEventDraftsView.as_view(),
    ),
    path(
        "social/kpis-summary",
        SocialKpisSummaryView.as_view(),
        name="social_kpis_summary",
    ),
    path("social/kpis-summary/", SocialKpisSummaryView.as_view()),
    path(
        "social/featured-profiles",
        SocialFeaturedProfilesView.as_view(),
        name="social_featured_profiles",
    ),
    path("social/featured-profiles/", SocialFeaturedProfilesView.as_view()),
    path(
        "social/buffer-profiles",
        SocialBufferProfilesView.as_view(),
        name="social_buffer_profiles",
    ),
    path("social/buffer-profiles/", SocialBufferProfilesView.as_view()),
    path(
        "social/posts/<int:pk>/expand-article",
        SocialExpandArticleView.as_view(),
        name="social_expand_article",
    ),
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
