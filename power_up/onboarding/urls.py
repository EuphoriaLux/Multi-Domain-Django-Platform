from django.urls import path

from . import views

app_name = "onboarding"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("start/<uuid:group_id>/", views.start_session, name="start_session"),
    path("<uuid:session_id>/configure/", views.configure, name="configure"),
    path("<uuid:session_id>/preview/", views.preview, name="preview"),
    path("<uuid:session_id>/download/", views.download_eml, name="download_eml"),
    path("<uuid:session_id>/", views.session_detail, name="session_detail"),
    # Tenant edit (with optional session context for back-navigation)
    path(
        "<uuid:session_id>/tenant/<uuid:tenant_id>/edit/",
        views.tenant_edit,
        name="tenant_edit",
    ),
    path(
        "tenant/<uuid:tenant_id>/edit/",
        views.tenant_edit,
        name="tenant_edit_standalone",
    ),
    # HTMX partials
    path(
        "<uuid:session_id>/preview-partial/",
        views.partial_email_preview,
        name="partial_email_preview",
    ),
    path("slots-partial/", views.partial_slot_picker, name="partial_slot_picker"),
]
