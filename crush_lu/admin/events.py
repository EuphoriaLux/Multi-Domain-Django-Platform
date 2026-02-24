"""
Event-related admin classes for Crush.lu Coach Panel.

Includes:
- MeetupEventAdmin
- EventRegistrationAdmin
- EventInvitationAdmin
- Event inlines (registrations, invitations, voting, presentations, speed dating)
"""

from django.contrib import admin
from django.contrib import messages as django_messages
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TranslationAdmin

from crush_lu.models import (
    MeetupEvent,
    EventRegistration,
    EventInvitation,
    EventVotingSession,
    PresentationQueue,
    SpeedDatingPair,
)
from .filters import EventCapacityFilter


# Inline admin for Event Registrations
class EventRegistrationInline(admin.TabularInline):
    model = EventRegistration
    extra = 0
    autocomplete_fields = ['user']
    fields = ("user", "status", "payment_confirmed", "registered_at")
    readonly_fields = ("registered_at",)
    can_delete = False
    show_change_link = True


# Inline admin for Event Invitations (Private Events)
class EventInvitationInline(admin.TabularInline):
    model = EventInvitation
    extra = 0
    fields = (
        "guest_email",
        "guest_first_name",
        "guest_last_name",
        "status",
        "approval_status",
        "invitation_sent_at",
    )
    readonly_fields = ("invitation_sent_at", "invitation_code")
    can_delete = True
    show_change_link = True
    verbose_name = "Private Invitation"
    verbose_name_plural = "Private Invitations"


# Inline admin for Voting Session
class EventVotingSessionInline(admin.StackedInline):
    model = EventVotingSession
    extra = 0
    fields = (
        ("is_active", "total_votes"),
        ("voting_start_time", "voting_end_time"),
        ("winning_presentation_style", "winning_speed_dating_twist"),
    )
    readonly_fields = ("total_votes",)
    can_delete = False


# Inline admin for Presentation Queue
class PresentationQueueInline(admin.TabularInline):
    model = PresentationQueue
    extra = 0
    autocomplete_fields = ['user']
    fields = (
        "user",
        "presentation_order",
        "status",
        "started_at",
        "completed_at",
        "duration_seconds",
    )
    readonly_fields = ("duration_seconds", "started_at", "completed_at")
    can_delete = False
    ordering = ["presentation_order"]
    show_change_link = True


# Inline admin for Speed Dating Pairs
class SpeedDatingPairInline(admin.TabularInline):
    model = SpeedDatingPair
    extra = 0
    autocomplete_fields = ['user1', 'user2']
    fields = (
        "round_number",
        "user1",
        "user2",
        "mutual_rating_score",
        "is_top_match",
        "duration_minutes",
    )
    readonly_fields = ("mutual_rating_score", "duration_minutes")
    can_delete = False
    ordering = ["round_number"]
    show_change_link = True


class MeetupEventAdmin(TranslationAdmin):
    list_display = (
        "title",
        "event_type",
        "date_time",
        "canton",
        "location",
        "get_event_languages",
        "get_registration_count",
        "get_confirmed_count",
        "get_waitlist_count",
        "max_participants",
        "get_spots_remaining",
        "is_private_invitation",
        "get_invited_users_count",
        "get_voting_status",
        "is_published",
        "is_cancelled",
    )
    list_filter = (
        "event_type",
        "is_published",
        "is_cancelled",
        "is_private_invitation",
        "enable_activity_voting",
        "require_approved_profile",
        EventCapacityFilter,
        "date_time",
    )
    search_fields = ("title", "description", "location", "address", "canton")
    readonly_fields = (
        "created_at",
        "updated_at",
        "invitation_code",
        "get_registration_count",
        "get_confirmed_count",
        "get_waitlist_count",
        "get_spots_remaining",
        "get_revenue",
        "get_voting_status",
        "get_presentation_status",
        "get_speed_dating_status",
    )
    inlines = [
        EventRegistrationInline,
        EventInvitationInline,
        EventVotingSessionInline,
        PresentationQueueInline,
        SpeedDatingPairInline,
    ]
    actions = [
        "publish_events",
        "unpublish_events",
        "cancel_events",
        "send_event_reminders",
        "export_attendees_csv",
    ]
    filter_horizontal = ("invited_users", "coaches")

    fieldsets = (
        (
            "Event Information",
            {
                "fields": (
                    "title",
                    "description",
                    "image",
                    "event_type",
                    "enable_activity_voting",
                ),
                "description": "Title and description are translatable (English/French/German tabs will appear above fields)",
            },
        ),
        (
            "Location & Timing",
            {
                "fields": (
                    "canton",
                    "location",
                    "address",
                    "date_time",
                    "duration_minutes",
                )
            },
        ),
        (
            "Capacity & Requirements",
            {
                "fields": (
                    "max_participants",
                    ("max_participants_m", "max_participants_f", "max_participants_nb"),
                    "min_age",
                    "max_age",
                    "require_approved_profile",
                    "languages",
                    "has_food_component",
                    "allow_plus_ones",
                ),
                "description": (
                    "Set all three gender caps to activate gender-aware waitlisting. "
                    "Leave all blank for total-only cap."
                ),
            },
        ),
        (
            "Event Coaches",
            {
                "fields": ("coaches",),
                "description": "Assign coaches to facilitate this event. They will be shown on the attendees page.",
            },
        ),
        ("Registration", {"fields": ("registration_deadline", "registration_fee")}),
        (
            "✨ Private Invitation Settings",
            {
                "fields": (
                    "is_private_invitation",
                    "invited_users",
                    "invitation_code",
                    "max_invited_guests",
                    "invitation_expires_at",
                ),
                "classes": ("collapse",),
                "description": "Configure this event as invitation-only. You can invite existing users directly OR send external guest invitations (managed via EventInvitation inline below)",
            },
        ),
        (
            "📊 Event Statistics",
            {
                "fields": (
                    "get_registration_count",
                    "get_confirmed_count",
                    "get_waitlist_count",
                    "get_spots_remaining",
                    "get_revenue",
                ),
                "classes": ("collapse",),
                "description": "Real-time event statistics and capacity information",
            },
        ),
        (
            "🎯 Phase Status Overview",
            {
                "fields": (
                    "get_voting_status",
                    "get_presentation_status",
                    "get_speed_dating_status",
                ),
                "classes": ("collapse",),
                "description": "Track progress through the 3-phase event system",
            },
        ),
        ("Status", {"fields": ("is_published", "is_cancelled")}),
        ("Metadata", {"fields": ("created_at", "updated_at")}),
    )

    def get_registration_count(self, obj):
        """Total registrations (all statuses)"""
        return obj.eventregistration_set.count()

    get_registration_count.short_description = _("📝 Total Registrations")

    def get_invited_users_count(self, obj):
        """Count of directly invited existing users"""
        count = obj.invited_users.count()
        if count > 0:
            return f"👥 {count}"
        return "-"

    get_invited_users_count.short_description = _("Invited Users")

    def get_confirmed_count(self, obj):
        """Confirmed registrations only"""
        return obj.get_confirmed_count()

    get_confirmed_count.short_description = _("✅ Confirmed")

    def get_waitlist_count(self, obj):
        """Waitlisted registrations"""
        return obj.get_waitlist_count()

    get_waitlist_count.short_description = _("⏳ Waitlist")

    def get_spots_remaining(self, obj):
        """Calculate remaining spots with optional gender breakdown"""
        remaining = obj.spots_remaining
        if remaining == 0:
            label = f"🔴 FULL (0/{obj.max_participants})"
        elif remaining <= 5:
            label = f"🟡 {remaining}/{obj.max_participants}"
        else:
            label = f"🟢 {remaining}/{obj.max_participants}"

        if obj.gender_limits_active:
            m_count = obj.get_confirmed_count_for_gender("M")
            f_count = obj.get_confirmed_count_for_gender("F")
            nb_count = obj.get_confirmed_count_for_gender("NB")
            label += (
                f" | M:{m_count}/{obj.max_participants_m}"
                f" F:{f_count}/{obj.max_participants_f}"
                f" NB:{nb_count}/{obj.max_participants_nb}"
            )

        return label

    get_spots_remaining.short_description = _("Spots Available")

    def get_event_languages(self, obj):
        """Display event languages"""
        if not obj.languages:
            return _("Any")
        return ", ".join(
            lang["flag"] + " " + lang["name"] for lang in obj.get_languages_display
        )

    get_event_languages.short_description = _("Languages")

    def get_revenue(self, obj):
        """Calculate total revenue from confirmed payments"""
        confirmed = obj.eventregistration_set.filter(payment_confirmed=True).count()
        revenue = confirmed * obj.registration_fee
        return f"€{revenue:.2f} ({confirmed} paid)"

    get_revenue.short_description = _("💰 Revenue")

    def get_voting_status(self, obj):
        """Display Phase 1 voting status"""
        try:
            voting_session = obj.voting_session
            if not voting_session.is_active and voting_session.voting_end_time:
                return f"✅ Completed ({voting_session.total_votes} votes) | Winners: {voting_session.winning_presentation_style or 'N/A'} & {voting_session.winning_speed_dating_twist or 'N/A'}"
            elif voting_session.is_active:
                return f"🟢 ACTIVE ({voting_session.total_votes} votes so far)"
            else:
                return "⏸️ Not Started"
        except EventVotingSession.DoesNotExist:
            return "❌ No Voting Session"

    get_voting_status.short_description = _("🗳️ Phase 1: Voting")

    def get_presentation_status(self, obj):
        """Display Phase 2 presentation status"""
        presentations = obj.presentation_queue.all()
        if not presentations.exists():
            return "❌ Not Initialized"

        total = presentations.count()
        completed = presentations.filter(status="completed").count()
        in_progress = presentations.filter(status="in_progress").exists()

        if completed == total:
            return f"✅ All Complete ({total}/{total})"
        elif in_progress:
            return f"🟢 IN PROGRESS ({completed}/{total} done)"
        elif completed > 0:
            return f"⏸️ Paused ({completed}/{total} done)"
        else:
            return f"⏳ Ready to Start (0/{total})"

    get_presentation_status.short_description = _("🎤 Phase 2: Presentations")

    def get_speed_dating_status(self, obj):
        """Display Phase 3 speed dating status"""
        pairs = obj.speed_dating_pairs.all()
        if not pairs.exists():
            return "❌ Not Initialized"

        total_pairs = pairs.count()
        completed_pairs = pairs.filter(completed_at__isnull=False).count()
        in_progress = pairs.filter(
            started_at__isnull=False, completed_at__isnull=True
        ).exists()

        if completed_pairs == total_pairs:
            return f"✅ All Rounds Complete ({total_pairs} pairs)"
        elif in_progress:
            return f"🟢 IN PROGRESS ({completed_pairs}/{total_pairs} rounds done)"
        elif completed_pairs > 0:
            return f"⏸️ Paused ({completed_pairs}/{total_pairs} rounds done)"
        else:
            return f"⏳ Ready to Start (0/{total_pairs} pairs)"

    get_speed_dating_status.short_description = _("💕 Phase 3: Speed Dating")

    @admin.action(description=_("✅ Publish selected events"))
    def publish_events(self, request, queryset):
        updated = queryset.update(is_published=True)
        django_messages.success(
            request, _("Published {count} event(s)").format(count=updated)
        )

    @admin.action(description=_("❌ Unpublish selected events"))
    def unpublish_events(self, request, queryset):
        updated = queryset.update(is_published=False)
        django_messages.success(
            request, _("Unpublished {count} event(s)").format(count=updated)
        )

    @admin.action(description=_("🚫 Cancel selected events"))
    def cancel_events(self, request, queryset):
        updated = queryset.update(is_cancelled=True)
        django_messages.success(
            request, _("Cancelled {count} event(s)").format(count=updated)
        )

    @admin.action(description=_("🔔 Send reminders to confirmed attendees"))
    def send_event_reminders(self, request, queryset):
        """Send push/email reminders to all confirmed registrations for selected events."""
        from crush_lu.notification_service import notify_event_reminder
        from django.utils import timezone

        total_sent = 0
        total_failed = 0

        for event in queryset:
            if event.is_cancelled:
                continue

            if event.event_date:
                days_until = (event.event_date - timezone.now().date()).days
            else:
                days_until = 1

            registrations = event.eventregistration_set.filter(status="confirmed")

            for registration in registrations:
                try:
                    result = notify_event_reminder(
                        user=registration.user,
                        registration=registration,
                        event=event,
                        days_until=days_until,
                        request=request,
                    )
                    if result.any_delivered:
                        total_sent += 1
                except Exception:
                    total_failed += 1

        if total_sent > 0:
            django_messages.success(
                request,
                _("Sent {count} reminder(s) successfully").format(count=total_sent),
            )
        if total_failed > 0:
            django_messages.warning(
                request,
                _("Failed to send {count} reminder(s)").format(count=total_failed),
            )
        if total_sent == 0 and total_failed == 0:
            django_messages.info(request, _("No confirmed registrations to notify"))

    @admin.action(description=_("📋 Export attendees to CSV"))
    def export_attendees_csv(self, request, queryset):
        """Export confirmed attendees for selected events to CSV"""
        import csv
        from django.http import HttpResponse
        from datetime import datetime

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="event_attendees_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        )

        writer = csv.writer(response)
        writer.writerow(
            [
                "Event",
                "Event Date",
                "Name",
                "Email",
                "Phone",
                "Status",
                "Payment Confirmed",
                "Dietary Restrictions",
                "Bringing Guest",
                "Guest Name",
                "Registered At",
            ]
        )

        for event in queryset:
            registrations = event.eventregistration_set.filter(
                status__in=["confirmed", "attended"]
            ).select_related("user__crushprofile")

            for reg in registrations:
                user = reg.user
                profile = getattr(user, "crushprofile", None)
                phone = profile.phone_number if profile else ""

                writer.writerow(
                    [
                        event.title,
                        (
                            event.date_time.strftime("%Y-%m-%d %H:%M")
                            if event.date_time
                            else ""
                        ),
                        user.get_full_name() or user.username,
                        user.email,
                        phone,
                        reg.get_status_display(),
                        "Yes" if reg.payment_confirmed else "No",
                        reg.dietary_restrictions or "",
                        "Yes" if reg.bringing_guest else "No",
                        reg.guest_name or "",
                        (
                            reg.registered_at.strftime("%Y-%m-%d %H:%M")
                            if reg.registered_at
                            else ""
                        ),
                    ]
                )

        total_events = queryset.count()
        django_messages.success(
            request, f"Exported attendees from {total_events} event(s) to CSV."
        )
        return response

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Customize form fields for better UX"""
        if db_field.name == "languages":
            from django import forms
            from crush_lu.models import CrushProfile

            kwargs["widget"] = forms.CheckboxSelectMultiple(
                choices=CrushProfile.EVENT_LANGUAGE_CHOICES
            )
            kwargs["help_text"] = _(
                "Select the languages this event will be conducted in. "
                "Leave all unchecked for no language restriction (any user can register)."
            )
            # Return a MultipleChoiceField that stores as JSON list
            return forms.TypedMultipleChoiceField(
                choices=CrushProfile.EVENT_LANGUAGE_CHOICES,
                widget=forms.CheckboxSelectMultiple,
                required=False,
                help_text=kwargs["help_text"],
                coerce=str,
            )
        elif db_field.name == "require_approved_profile":
            kwargs["help_text"] = _(
                "Require approved Crush profile for registration?\n"
                "• Checked (recommended): Only users with approved profiles can register\n"
                "• Unchecked: Any authenticated user can register (no profile needed)"
            )
        elif db_field.name == "image":
            kwargs["help_text"] = _(
                "📸 Event banner image (optional)\n"
                "• Recommended size: 1200×630 pixels (1.9:1 aspect ratio)\n"
                "• Format: JPG or PNG\n"
                "• File size: Under 500KB for best performance\n"
                "• This size works great for social media sharing (Open Graph) and displays perfectly on all devices\n"
                "• The image will be cropped to fit: centered on mobile, full width on desktop"
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)


class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = ("get_user_display", "event", "status", "payment_confirmed", "registered_at")
    list_filter = ("status", "payment_confirmed", "registered_at")
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email", "event__title")
    autocomplete_fields = ['user', 'event']
    readonly_fields = ("registered_at", "updated_at")
    # Quick inline editing for registration management
    list_editable = ("status", "payment_confirmed")
    actions = ["export_registrations_csv", "confirm_registrations", "move_to_waitlist"]
    fieldsets = (
        ("Registration Details", {"fields": ("event", "user", "status")}),
        (
            "Additional Information",
            {
                "fields": (
                    "dietary_restrictions",
                    "bringing_guest",
                    "guest_name",
                )
            },
        ),
        ("Payment", {"fields": ("payment_confirmed", "payment_date")}),
        ("Timestamps", {"fields": ("registered_at", "updated_at")}),
    )

    def get_user_display(self, obj):
        full_name = obj.user.get_full_name()
        if full_name:
            return format_html('{} <span style="color: #888; font-size: 11px;">({})</span>', full_name, obj.user.username)
        return obj.user.username
    get_user_display.short_description = _('User')
    get_user_display.admin_order_field = 'user__first_name'

    @admin.action(description=_("📋 Export selected registrations to CSV"))
    def export_registrations_csv(self, request, queryset):
        """Export selected registrations to CSV"""
        import csv
        from django.http import HttpResponse
        from datetime import datetime

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="registrations_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        )

        writer = csv.writer(response)
        writer.writerow(
            [
                "Event",
                "Event Date",
                "Name",
                "Email",
                "Phone",
                "Status",
                "Payment Confirmed",
                "Payment Date",
                "Dietary Restrictions",
                "Bringing Guest",
                "Guest Name",
                "Registered At",
            ]
        )

        for reg in queryset.select_related("user__crushprofile", "event"):
            user = reg.user
            profile = getattr(user, "crushprofile", None)
            phone = profile.phone_number if profile else ""

            writer.writerow(
                [
                    reg.event.title,
                    (
                        reg.event.date_time.strftime("%Y-%m-%d %H:%M")
                        if reg.event.date_time
                        else ""
                    ),
                    user.get_full_name() or user.username,
                    user.email,
                    phone,
                    reg.get_status_display(),
                    "Yes" if reg.payment_confirmed else "No",
                    reg.payment_date.strftime("%Y-%m-%d") if reg.payment_date else "",
                    reg.dietary_restrictions or "",
                    "Yes" if reg.bringing_guest else "No",
                    reg.guest_name or "",
                    (
                        reg.registered_at.strftime("%Y-%m-%d %H:%M")
                        if reg.registered_at
                        else ""
                    ),
                ]
            )

        django_messages.success(
            request, f"Exported {queryset.count()} registration(s) to CSV."
        )
        return response

    @admin.action(description=_("✅ Confirm selected registrations"))
    def confirm_registrations(self, request, queryset):
        """Confirm selected registrations"""
        updated = queryset.update(status="confirmed")
        django_messages.success(request, _("Confirmed %(count)s registration(s).") % {"count": updated})

    @admin.action(description=_("⏳ Move to waitlist"))
    def move_to_waitlist(self, request, queryset):
        """Move selected registrations to waitlist"""
        updated = queryset.update(status="waitlist")
        django_messages.success(
            request, f"Moved {updated} registration(s) to waitlist."
        )


class EventInvitationAdmin(admin.ModelAdmin):
    """
    ✨ PRIVATE EVENT INVITATIONS - Manage VIP Guest Invitations

    Send and manage private invitations for exclusive events.
    Track invitation status, approvals, and guest account creation.
    """

    list_display = (
        "get_guest_name",
        "guest_email",
        "event",
        "status",
        "approval_status",
        "invitation_sent_at",
        "invited_by",
        "has_special_user",
        "get_invitation_link",
    )
    list_filter = (
        "status",
        "approval_status",
        "invitation_sent_at",
        "event",
        "special_user",
    )
    search_fields = (
        "guest_email",
        "guest_first_name",
        "guest_last_name",
        "event__title",
        "invited_by__username",
        "special_user__first_name",
        "special_user__last_name",
    )
    readonly_fields = (
        "invitation_code",
        "invitation_sent_at",
        "accepted_at",
        "approved_at",
        "get_invitation_link",
        "get_status_display",
    )
    actions = ["approve_guests", "reject_guests", "resend_invitations"]

    fieldsets = (
        (
            "👤 Guest Information",
            {"fields": ("guest_first_name", "guest_last_name", "guest_email")},
        ),
        ("🎉 Event Details", {"fields": ("event", "invited_by")}),
        (
            "✨ Special User VIP Treatment",
            {
                "fields": ("special_user",),
                "classes": ("collapse",),
                "description": "Link this invitation to a Special User Experience for VIP treatment (auto-approval, custom journey, etc.)",
            },
        ),
        (
            "📧 Invitation Status",
            {
                "fields": (
                    "status",
                    "invitation_code",
                    "get_invitation_link",
                    "invitation_sent_at",
                    "accepted_at",
                ),
                "description": "Track invitation delivery and guest response",
            },
        ),
        (
            "✅ Approval Workflow",
            {
                "fields": ("approval_status", "approval_notes", "approved_at"),
                "description": "Coach approval for guests to attend the event",
            },
        ),
        (
            "👥 User Account",
            {
                "fields": ("created_user",),
                "description": "Linked user account (created when guest accepts invitation)",
            },
        ),
        (
            "📊 Status Overview",
            {
                "fields": ("get_status_display",),
                "classes": ("collapse",),
                "description": "Complete invitation lifecycle status",
            },
        ),
    )

    def get_guest_name(self, obj):
        """Display guest's full name"""
        return f"{obj.guest_first_name} {obj.guest_last_name}"

    get_guest_name.short_description = _("Guest Name")
    get_guest_name.admin_order_field = "guest_first_name"

    def has_special_user(self, obj):
        """Display if linked to Special User Experience"""
        return obj.special_user is not None

    has_special_user.boolean = True
    has_special_user.short_description = _("✨ VIP")

    def get_invitation_link(self, obj):
        """Display clickable invitation link"""
        if obj.invitation_code:
            url = f"https://crush.lu{reverse('crush_lu:invitation_landing', kwargs={'code': obj.invitation_code})}"
            return format_html(
                '<a href="{}" target="_blank" style="color: #9B59B6; font-weight: bold;">'
                "📧 View Invitation Page</a><br>"
                '<small style="color: #666; font-family: monospace;">{}</small>',
                url,
                url,
            )
        return "N/A"

    get_invitation_link.short_description = _("Invitation Link")

    def get_status_display(self, obj):
        """Display comprehensive status with visual indicators"""
        status_html = (
            '<div style="padding: 15px; background: #f8f9fa; border-radius: 8px;">'
        )

        status_colors = {
            "pending": "#ffc107",
            "accepted": "#0dcaf0",
            "declined": "#6c757d",
            "attended": "#28a745",
            "expired": "#dc3545",
        }
        status_color = status_colors.get(obj.status, "#6c757d")
        status_html += f'<p><strong>Invitation:</strong> <span style="color: {status_color}; font-weight: bold;">● {obj.get_status_display()}</span></p>'

        approval_colors = {
            "pending_approval": "#ffc107",
            "approved": "#28a745",
            "rejected": "#dc3545",
        }
        approval_color = approval_colors.get(obj.approval_status, "#6c757d")
        status_html += f'<p><strong>Approval:</strong> <span style="color: {approval_color}; font-weight: bold;">● {obj.get_approval_status_display()}</span></p>'

        if obj.is_expired:
            status_html += '<p style="color: #dc3545;"><strong>⚠️ EXPIRED</strong></p>'

        if obj.created_user:
            status_html += f"<p><strong>Account Created:</strong> ✅ {obj.created_user.username}</p>"
        else:
            status_html += "<p><strong>Account:</strong> ❌ Not yet created</p>"

        status_html += "</div>"
        return format_html(status_html)

    get_status_display.short_description = _("Complete Status")

    @admin.action(description=_("✅ Approve selected guests"))
    def approve_guests(self, request, queryset):
        """Approve guests to attend the event and send notification emails"""
        from django.utils import timezone
        from crush_lu.email_notifications import send_invitation_approval_email

        accepted_invitations = queryset.filter(
            status="accepted", approval_status="pending_approval"
        )

        updated = 0
        emails_sent = 0

        for invitation in accepted_invitations:
            invitation.approval_status = "approved"
            invitation.approved_at = timezone.now()
            invitation.save()
            updated += 1

            if send_invitation_approval_email(invitation, request=request):
                emails_sent += 1

        if updated > 0:
            django_messages.success(
                request,
                _(
                    "Approved {updated} guest(s) to attend the event. "
                    "Sent {emails_sent} email notification(s)."
                ).format(updated=updated, emails_sent=emails_sent),
            )
        else:
            django_messages.warning(
                request,
                _(
                    "No pending invitations to approve. Only accepted invitations can be approved."
                ),
            )

    @admin.action(description=_("❌ Reject selected guests"))
    def reject_guests(self, request, queryset):
        """Reject guests from attending the event"""
        from django.utils import timezone

        accepted_invitations = queryset.filter(
            status="accepted", approval_status="pending_approval"
        )

        updated = accepted_invitations.update(
            approval_status="rejected", approved_at=timezone.now()
        )

        if updated > 0:
            django_messages.success(
                request,
                _("Rejected {count} guest(s). They will be notified.").format(
                    count=updated
                ),
            )
        else:
            django_messages.warning(
                request,
                _(
                    "No pending invitations to reject. Only accepted invitations can be rejected."
                ),
            )

    @admin.action(description=_("📧 Resend invitation emails"))
    def resend_invitations(self, request, queryset):
        """Resend invitation emails to guests who haven't accepted"""
        pending_invitations = queryset.filter(status="pending")
        count = pending_invitations.count()

        if count > 0:
            django_messages.info(
                request,
                _(
                    "Would resend {count} invitation(s). Email sending not yet implemented."
                ).format(count=count),
            )
        else:
            django_messages.warning(
                request,
                _(
                    "No pending invitations to resend. Only unaccepted invitations can be resent."
                ),
            )
