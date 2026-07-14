"""Admin configuration for the Crush Cache scavenger hunt system."""

import uuid

from django.contrib import admin, messages
from django.http import HttpResponse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import (
    TranslationAdmin,
    TranslationStackedInline,
    TranslationTabularInline,
)

from azureproject.admin_translation_mixin import AutoTranslateMixin

from crush_lu.models.crush_cache import (
    CacheChallenge,
    CacheChallengeAttempt,
    CacheHunt,
    CacheStation,
    CacheStationAttempt,
    CacheTeam,
    CacheTeamMember,
    CacheTeamProgress,
)


class CacheStationInline(TranslationTabularInline):
    model = CacheStation
    extra = 1
    fields = (
        "order",
        "name",
        "unlock_mode",
        "latitude",
        "longitude",
        "radius_meters",
    )
    show_change_link = True


class CacheChallengeInline(TranslationStackedInline):
    model = CacheChallenge
    extra = 1
    fields = (
        "challenge_order",
        "challenge_type",
        "question",
        "options",
        "correct_answer",
        "alternative_answers",
        "points_awarded",
        "success_message",
        "hint_1",
        "hint_1_cost",
        "hint_2",
        "hint_2_cost",
        "hint_3",
        "hint_3_cost",
    )


class CacheTeamMemberInline(admin.TabularInline):
    model = CacheTeamMember
    extra = 0
    raw_id_fields = ("registration",)
    fields = ("registration", "joined_at")
    readonly_fields = ("joined_at",)


def generate_cache_qr_sheet(modeladmin, request, queryset):
    """Admin action: download a printable A4 PDF of station QR codes."""
    from crush_lu.qr_utils import HAS_REPORTLAB, generate_cache_station_sheet

    if queryset.count() != 1:
        messages.error(request, _("Select exactly one hunt to generate a QR sheet."))
        return None

    hunt = queryset.first()
    stations = list(hunt.ordered_stations())
    if not stations:
        messages.error(request, _("'%(hunt)s' has no stations yet.") % {"hunt": hunt})
        return None

    # Only stations that actually unlock via QR (qr / gps_qr) need a printed
    # code — a GPS or 'none' station's QR is a no-op sticker.
    stations = [s for s in stations if s.requires_qr]
    if not stations:
        messages.warning(
            request,
            _(
                "None of '%(hunt)s' stations unlock via QR (they are all GPS or "
                "none), so there is nothing to print."
            )
            % {"hunt": hunt},
        )
        return None

    if not HAS_REPORTLAB:
        messages.warning(
            request,
            _(
                "reportlab is not installed, so the PDF sheet is unavailable. "
                "Open each station in the admin and download its QR code "
                "preview image instead."
            ),
        )
        return None

    pdf_bytes = generate_cache_station_sheet(
        stations,
        title=f"Crush Cache — {hunt.title}",
        # Same-host URLs: the crush admin serves from the player-facing
        # host, so a sheet printed on a test slot stays on that slot.
        domain=request.get_host(),
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="crush-cache-{hunt.pk}-qr-sheet.pdf"'
    )
    return response


generate_cache_qr_sheet.short_description = _("Generate printable QR sheet (PDF)")


def regenerate_cache_qr_tokens(modeladmin, request, queryset):
    """Admin action: mint new QR tokens for every station of the selected
    hunts. Invalidates any already-printed sheets — reprint afterwards."""
    for hunt in queryset:
        count = 0
        for station in hunt.stations.all():
            station.qr_token = uuid.uuid4()
            # Printed sheets also carry the typeable fallback — rotate it
            # with the token, or an old sheet keeps unlocking stations via
            # manual entry. save() regenerates a blank manual_code.
            station.manual_code = ""
            station.save(update_fields=["qr_token", "manual_code"])
            count += 1
        messages.warning(
            request,
            _(
                "'%(hunt)s': regenerated %(count)d QR tokens. "
                "Previously printed sheets are now invalid — print a new sheet."
            )
            % {"hunt": hunt, "count": count},
        )


regenerate_cache_qr_tokens.short_description = _(
    "Regenerate QR tokens (invalidates printed sheets)"
)


class CacheHuntAdmin(AutoTranslateMixin, TranslationAdmin):
    list_display = (
        "title",
        "event",
        "status",
        "navigation_mode",
        "team_size_max",
        "station_count",
        "created_by",
        "created_at",
    )
    list_select_related = ["event", "created_by"]
    list_filter = ("status", "navigation_mode")
    inlines = [CacheStationInline]
    raw_id_fields = ("event", "created_by")
    readonly_fields = (
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
        "readiness_check_display",
    )
    actions = [generate_cache_qr_sheet, regenerate_cache_qr_tokens]

    def station_count(self, obj):
        return obj.stations.count()

    station_count.short_description = _("Stations")

    def readiness_check_display(self, obj):
        if obj is None or not obj.pk:
            return _("Will be checked after saving.")
        checks = obj.readiness_check()
        rows = format_html_join(
            "\n",
            '<tr><td style="padding:4px 8px">{}</td>'
            '<td style="padding:4px 8px;font-weight:600">{}</td>'
            '<td style="padding:4px 8px;color:#666">{}</td></tr>',
            (
                (
                    format_html(
                        '<span style="color:{}">{}</span>',
                        "#16a34a" if c["ok"] else "#dc2626",
                        "✅" if c["ok"] else "❌",
                    ),
                    c["label"],
                    c["detail"],
                )
                for c in checks
            ),
        )
        return format_html('<table style="border-collapse:collapse">{}</table>', rows)

    readiness_check_display.short_description = _("Readiness Check")


class CacheStationAdmin(AutoTranslateMixin, TranslationAdmin):
    list_display = (
        "order",
        "name",
        "hunt",
        "unlock_mode",
        "manual_code",
        "radius_meters",
        "challenge_count",
    )
    list_select_related = ["hunt__event"]
    list_filter = ("hunt__event", "unlock_mode")
    inlines = [CacheChallengeInline]
    raw_id_fields = ("hunt",)
    readonly_fields = ("qr_token", "manual_code", "qr_code_preview")

    def challenge_count(self, obj):
        return obj.challenges.count()

    challenge_count.short_description = _("Challenges")

    def qr_code_preview(self, obj):
        """Inline QR image — right-click to save as PNG when no reportlab."""
        if not obj.pk:
            return _("Save the station first to get its QR code.")
        if not obj.requires_qr:
            return _("No QR needed — this station unlocks by “%(mode)s”.") % {
                "mode": obj.get_unlock_mode_display()
            }
        from crush_lu.qr_utils import generate_cache_qr_url, generate_qr_code_base64

        url = generate_cache_qr_url(obj.qr_token)
        return format_html(
            '<img src="{}" alt="QR" style="width:180px;height:180px">'
            '<div style="color:#666;font-size:12px">{}</div>'
            '<div style="font-size:13px;font-weight:600">{}</div>',
            generate_qr_code_base64(url),
            url,
            _("Manual code: %(code)s") % {"code": obj.manual_code},
        )

    qr_code_preview.short_description = _("QR Code")


class CacheChallengeAdmin(AutoTranslateMixin, TranslationAdmin):
    list_display = (
        "station",
        "challenge_order",
        "challenge_type",
        "points_awarded",
    )
    list_select_related = ["station__hunt__event"]
    list_filter = ("challenge_type", "station__hunt__event")
    raw_id_fields = ("station",)


class CacheTeamAdmin(admin.ModelAdmin):
    list_display = ("name", "hunt", "join_code", "color", "member_count", "created_at")
    list_select_related = ["hunt__event"]
    list_filter = ("hunt__event",)
    raw_id_fields = ("hunt",)
    inlines = [CacheTeamMemberInline]

    def member_count(self, obj):
        return obj.members.count()

    member_count.short_description = _("Members")

    def save_formset(self, request, form, formset, change):
        # Inline members need the denormalized hunt FK from the parent team
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, CacheTeamMember) and not instance.hunt_id:
                instance.hunt = form.instance.hunt
            instance.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()


class CacheTeamProgressAdmin(admin.ModelAdmin):
    list_display = (
        "team",
        "current_station",
        "total_points",
        "is_finished",
        "finished_at",
        "last_position_at",
    )
    list_select_related = ["team__hunt__event", "current_station"]
    list_filter = ("is_finished", "team__hunt__event")
    raw_id_fields = ("team", "current_station")


class CacheStationAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "team",
        "station",
        "arrived_at",
        "scanned_at",
        "completed_at",
        "points_earned",
    )
    list_select_related = ["team__hunt__event", "station"]
    list_filter = ("team__hunt__event",)
    raw_id_fields = ("team", "station")
    readonly_fields = ("started_at",)


class CacheChallengeAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "station_attempt",
        "challenge",
        "is_correct",
        "attempts_count",
        "points_earned",
        "answered_by",
        "answered_at",
    )
    list_select_related = [
        "station_attempt__team__hunt__event",
        "challenge",
        "answered_by",
    ]
    list_filter = ("is_correct", "station_attempt__team__hunt__event")
    raw_id_fields = ("station_attempt", "challenge", "answered_by")
